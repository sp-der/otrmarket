from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
import os
import sqlite3
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session_bucket(value: datetime | None) -> dict | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(NY)
    clock = local.time().replace(tzinfo=None)
    day = local.date()
    if time(18, 0) <= clock < time(21, 0):
        return {"name": "ASIA", "date": day.isoformat()}
    if clock >= time(21, 0) or clock < time(2, 0):
        session_day = day if clock >= time(21, 0) else day - timedelta(days=1)
        return {"name": "TOKYO", "date": session_day.isoformat()}
    if time(2, 0) <= clock < time(8, 0):
        return {"name": "LONDON", "date": day.isoformat()}
    if time(8, 0) <= clock < time(16, 30):
        return {"name": "NEW_YORK", "date": day.isoformat()}
    return None


@dataclass(frozen=True)
class OperatingModeConfig:
    mode: str = "EVAL"
    # Legacy knobs are retained for config/backward compatibility, but EVAL no
    # longer rejects trades because a count was reached. The realized per-
    # session profit cap belongs to EvaluationRiskGuard.
    eval_max_trades_per_day: int = 0
    eval_max_trades_per_session: int = 0
    eval_trade_profit_objective: float = 1_500.0
    eval_day_profit_objective: float = 3_000.0
    funded_daily_goal_floor: float = 350.0
    funded_daily_goal_ceiling: float = 500.0
    funded_protect_risk_cap: float = 0.35

    @classmethod
    def from_env(cls) -> "OperatingModeConfig":
        explicit = os.getenv("OTR_TRADING_MODE", "").strip().upper()
        phase = os.getenv("EVAL_PHASE", "EVALUATION").strip().upper()
        if explicit in {"EVAL", "EVALUATION"}:
            mode = "EVAL"
        elif explicit in {"FUNDED", "LIVE_FUNDED"}:
            mode = "FUNDED"
        else:
            mode = "FUNDED" if phase.startswith("FUNDED") else "EVAL"
        return cls(
            mode=mode,
            # Read old variables so existing Railway configs remain valid, but
            # they are informational only in EVAL mode after Operation 7.2L.
            eval_max_trades_per_day=_env_int("OTR_EVAL_MAX_TRADES_DAY", 0),
            eval_max_trades_per_session=_env_int("OTR_EVAL_MAX_TRADES_SESSION", 0),
            eval_trade_profit_objective=_env_float("OTR_EVAL_TRADE_PROFIT_OBJECTIVE", 1_500.0),
            eval_day_profit_objective=_env_float("OTR_EVAL_DAY_PROFIT_OBJECTIVE", 3_000.0),
            funded_daily_goal_floor=_env_float("OTR_FUNDED_DAILY_GOAL_FLOOR", 350.0),
            funded_daily_goal_ceiling=_env_float("OTR_FUNDED_DAILY_GOAL_CEILING", 500.0),
            funded_protect_risk_cap=_env_float("OTR_FUNDED_PROTECT_RISK_CAP", 0.35),
        )


def _cap_risk(setup, cap: float, tier: str) -> None:
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, cap)
    setup.metadata["execution_tier"] = tier


def _trade_rows(connection: sqlite3.Connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if not columns:
        return []
    result_dollars = "result_dollars" if "result_dollars" in columns else "NULL AS result_dollars"
    return connection.execute(
        f"""
        SELECT status, opened_at, closed_at, {result_dollars}
        FROM paper_trades
        ORDER BY COALESCE(opened_at, closed_at) ASC
        """
    ).fetchall()


def _day_and_session_stats(connection: sqlite3.Connection, reference: datetime) -> dict:
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    local_day = reference.astimezone(NY).date()
    candidate_session = _session_bucket(reference)
    trades_today = 0
    session_trades = 0
    realized_today = 0.0
    session_realized = 0.0

    for status, opened_value, closed_value, result_dollars in _trade_rows(connection):
        opened = _parse_dt(opened_value)
        opened_session = _session_bucket(opened)
        belongs_to_candidate_session = bool(
            candidate_session
            and opened_session
            and opened_session["name"] == candidate_session["name"]
            and opened_session["date"] == candidate_session["date"]
        )
        if opened and opened.astimezone(NY).date() == local_day:
            trades_today += 1
            if belongs_to_candidate_session:
                session_trades += 1

        closed = _parse_dt(closed_value)
        if status == "CLOSED":
            dollars = float(result_dollars or 0.0)
            if closed and closed.astimezone(NY).date() == local_day:
                realized_today += dollars
            if belongs_to_candidate_session:
                session_realized += dollars

    return {
        "local_day": local_day.isoformat(),
        "candidate_session": candidate_session,
        "trades_today": trades_today,
        "session_trades": session_trades,
        "realized_today": realized_today,
        "session_realized": session_realized,
    }


def _quality_grade(setup) -> str:
    value = setup.metadata.get("a_plus_context", {}).get("quality_grade")
    if value:
        return str(value).upper()
    strategy = str(setup.metadata.get("strategy", ""))
    if strategy == "REJECTION_BLOCK_10_10":
        score = int(setup.metadata.get("checklist_score", 0) or 0)
        total = int(setup.metadata.get("checklist_total", 10) or 10)
        if score >= total >= 10:
            return "A+"
    return "A"


def evaluate_operating_mode(connection: sqlite3.Connection, setup, config: OperatingModeConfig | None = None):
    config = config or OperatingModeConfig.from_env()
    stats = _day_and_session_stats(connection, setup.created_at)
    grade = _quality_grade(setup)
    details = {
        "profile": "OTR_OPERATING_MODE_1_0",
        **asdict(config),
        **stats,
        "quality_grade": grade,
        "forced_trade": False,
        "trade_count_limits_active": False,
    }

    if config.mode == "EVAL":
        # The actual EVAL stop is realized session P&L, enforced by
        # EvaluationRiskGuard through EVAL_SESSION_PROFIT_CAP (normally $1,500).
        # This layer never blocks a qualified setup merely because one or more
        # trades were already taken in the day/session.
        setup.metadata["profit_objective_dollars"] = config.eval_trade_profit_objective
        setup.metadata["session_profit_objective_dollars"] = config.eval_trade_profit_objective
        details["objective_note"] = (
            "No trade-count cap. Continue taking qualified trades; the evaluation risk guard "
            "banks the session when its realized profit cap is reached."
        )
        if stats["candidate_session"] is None:
            return False, "Eval mode only opens new primary risk inside a named futures session bucket.", details
        name = stats["candidate_session"]["name"]
        return True, (
            f"Eval mode {name}: no trade-count limit; {stats['session_trades']} trades so far and "
            f"${stats['session_realized']:.2f} realized this session. The realized session-profit cap "
            "is enforced separately by the evaluation risk guard."
        ), details

    setup.metadata["daily_profit_goal"] = {
        "floor": config.funded_daily_goal_floor,
        "ceiling": config.funded_daily_goal_ceiling,
    }
    if stats["realized_today"] >= config.funded_daily_goal_ceiling:
        return False, (
            f"Funded daily ceiling reached at ${stats['realized_today']:.2f}; stop adding risk and protect the day."
        ), details
    if stats["realized_today"] >= config.funded_daily_goal_floor:
        if grade != "A+":
            return False, (
                f"Funded protect mode is active above ${config.funded_daily_goal_floor:.0f}; only A+ setups may add risk before the "
                f"${config.funded_daily_goal_ceiling:.0f} ceiling."
            ), details
        _cap_risk(setup, config.funded_protect_risk_cap, "FUNDED_PROTECT_A_PLUS")
        details["risk_cap"] = config.funded_protect_risk_cap
        return True, (
            f"Funded protect mode: A+ setup may continue at <= {config.funded_protect_risk_cap:.0%} risk while daily P&L is "
            f"${stats['realized_today']:.2f}."
        ), details

    return True, (
        f"Funded accumulation mode: daily P&L ${stats['realized_today']:.2f}; aim for the "
        f"${config.funded_daily_goal_floor:.0f}-${config.funded_daily_goal_ceiling:.0f} zone without forcing trades."
    ), details
