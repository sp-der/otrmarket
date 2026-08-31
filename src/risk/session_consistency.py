from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import os
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_EXECUTION_TIMEFRAMES = {"1m", "5m", "15m", "1h"}
FUTURES_SYMBOLS = {"NQ", "MNQ", "ES", "MES", "GC", "MGC"}
ALWAYS_OPEN_SYMBOLS = {"BTC"}
SUNDAY_FUTURES_OPEN = time(18, 0)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except Exception:
        return fallback


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class SessionConsistencyConfig:
    """Session/quality profile for autonomous futures execution.

    The session governor controls when the bot may add risk, while setup quality
    is graded separately by the A/A+ context engine. ``execution_timeframe`` can
    be a single timeframe for controlled experiments or ``ALL`` to let
    1m/5m/15m/1h compete for execution as long as each setup passes its own
    quality rules. It never forces a trade.

    Operation 7.2L retires the old production defaults that stopped after two
    trades or banked a day at +$250. A positive ``max_trades_per_day`` or
    ``base_win_lock_dollars`` can still be supplied explicitly for a controlled
    experiment, but zero means disabled. Normal EVAL session banking is owned by
    EvaluationRiskGuard's realized session-profit cap (normally $1,500).
    """

    timezone_name: str = "America/New_York"
    execution_timeframe: str = "5m"
    session_start: str = "00:00"
    session_end: str = "23:59"
    max_trades_per_day: int = 0
    base_win_lock_dollars: float = 0.0
    second_chance_min_rr: float = 2.0
    second_chance_body_ratio: float = 1.90
    second_chance_range_ratio: float = 1.50

    @classmethod
    def from_env(cls) -> "SessionConsistencyConfig":
        return cls(
            timezone_name=os.getenv("OTR_SESSION_TZ", "America/New_York").strip(),
            execution_timeframe=os.getenv("OTR_EXECUTION_TIMEFRAME", "5m").strip(),
            session_start=os.getenv("OTR_SESSION_START", "00:00").strip(),
            session_end=os.getenv("OTR_SESSION_END", "23:59").strip(),
            max_trades_per_day=_env_int("OTR_CALIBRATION_MAX_TRADES_DAY", 0),
            base_win_lock_dollars=_env_float("OTR_BASE_WIN_LOCK_DOLLARS", 0.0),
            second_chance_min_rr=_env_float("OTR_SECOND_CHANCE_MIN_RR", 2.0),
            second_chance_body_ratio=_env_float("OTR_SECOND_CHANCE_BODY_RATIO", 1.90),
            second_chance_range_ratio=_env_float("OTR_SECOND_CHANCE_RANGE_RATIO", 1.50),
        )

    @property
    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("America/New_York")

    @property
    def all_timeframes_enabled(self) -> bool:
        return self.execution_timeframe.strip().upper() in {"ALL", "ANY", "MULTI"}


@dataclass(frozen=True)
class SessionConsistencyDecision:
    allowed: bool
    reason: str
    details: dict


def _day_stats(connection: sqlite3.Connection, reference_time: datetime, tz: ZoneInfo) -> dict:
    current_day = reference_time.astimezone(tz).date()
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if not columns:
        return {"trades": 0, "losses": 0, "wins": 0, "realized_pnl": 0.0}

    result_dollars_expr = "result_dollars" if "result_dollars" in columns else "NULL AS result_dollars"
    rows = connection.execute(
        f"""
        SELECT status, result, opened_at, closed_at, {result_dollars_expr}
        FROM paper_trades
        ORDER BY COALESCE(closed_at, opened_at) ASC
        """
    ).fetchall()

    trades = 0
    losses = 0
    wins = 0
    realized = 0.0
    for row in rows:
        opened_at = _parse_dt(row["opened_at"])
        if opened_at and opened_at.astimezone(tz).date() == current_day:
            trades += 1

        closed_at = _parse_dt(row["closed_at"])
        if not closed_at or closed_at.astimezone(tz).date() != current_day:
            continue
        if row["status"] != "CLOSED":
            continue
        result = str(row["result"] or "").upper()
        losses += int(result == "LOSS")
        wins += int(result == "WIN")
        realized += float(row["result_dollars"] or 0.0)

    return {
        "trades": trades,
        "losses": losses,
        "wins": wins,
        "realized_pnl": realized,
    }


def _second_chance_quality(setup, config: SessionConsistencyConfig) -> tuple[bool, str, dict]:
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    rr = float(setup.risk_reward or 0.0)
    details = {"strategy": strategy, "risk_reward": rr}

    if strategy == "REJECTION_BLOCK_10_10":
        score = int(setup.metadata.get("checklist_score", 0) or 0)
        total = int(setup.metadata.get("checklist_total", 10) or 10)
        details.update(checklist_score=score, checklist_total=total)
        allowed = score >= total >= 10 and rr >= 3.0
        return (
            allowed,
            "Full 10/10 rejection block qualifies for the post-loss second chance."
            if allowed
            else "Post-loss second chance requires a full 10/10 rejection block at 3R+.",
            details,
        )

    displacement = getattr(setup, "displacement", None)
    body_ratio = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    range_ratio = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    details.update(body_ratio=body_ratio, range_ratio=range_ratio)
    allowed = (
        rr >= config.second_chance_min_rr
        and body_ratio >= config.second_chance_body_ratio
        and range_ratio >= config.second_chance_range_ratio
    )
    return (
        allowed,
        (
            "Post-loss second chance passed stronger RR/displacement requirements."
            if allowed
            else (
                "After a loss, the next ICT trade must be exceptional: "
                f">={config.second_chance_min_rr:.2f}R, "
                f">={config.second_chance_body_ratio:.2f}x body and "
                f">={config.second_chance_range_ratio:.2f}x range displacement."
            )
        ),
        details,
    )


def evaluate_session_consistency(
    connection: sqlite3.Connection,
    setup,
    config: SessionConsistencyConfig | None = None,
) -> SessionConsistencyDecision:
    config = config or SessionConsistencyConfig.from_env()
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    local = created_at.astimezone(config.timezone)
    local_clock = local.time().replace(tzinfo=None)
    symbol = str(getattr(setup, "symbol", "") or "").upper()
    is_always_open = symbol in ALWAYS_OPEN_SYMBOLS
    is_supported_futures = symbol in FUTURES_SYMBOLS
    is_sunday_futures_session = (
        local.weekday() == 6
        and is_supported_futures
        and local_clock >= SUNDAY_FUTURES_OPEN
    )

    if is_always_open:
        market_session_mode = "24_7"
    elif is_sunday_futures_session:
        market_session_mode = "SUNDAY_GLOBEX"
    else:
        market_session_mode = "CALIBRATION_WINDOW"

    details = {
        "profile": "SESSION_CONSISTENCY_5_4",
        "timezone": config.timezone_name,
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
        "symbol": symbol,
        "market_session_mode": market_session_mode,
        "execution_timeframe": config.execution_timeframe,
        "candidate_timeframe": setup.timeframe,
        "multi_timeframe_execution": config.all_timeframes_enabled,
        "supported_execution_timeframes": sorted(SUPPORTED_EXECUTION_TIMEFRAMES),
        "session_start": config.session_start,
        "session_end": config.session_end,
        "sunday_futures_open": SUNDAY_FUTURES_OPEN.strftime("%H:%M"),
        "max_trades_per_day": config.max_trades_per_day,
        "base_win_lock_dollars": config.base_win_lock_dollars,
        "trade_count_limit_active": config.max_trades_per_day > 0,
        "base_win_lock_active": config.base_win_lock_dollars > 0,
    }

    if setup.timeframe not in SUPPORTED_EXECUTION_TIMEFRAMES:
        return SessionConsistencyDecision(
            False,
            f"{setup.timeframe} is not a supported autonomous execution timeframe.",
            details,
        )

    if not config.all_timeframes_enabled and setup.timeframe != config.execution_timeframe:
        return SessionConsistencyDecision(
            False,
            f"Calibration profile is executing {config.execution_timeframe}; {setup.timeframe} stays scanner/shadow only.",
            details,
        )

    # Market-aware weekend handling. BTC can qualify 24/7. Futures remain
    # context-only through Saturday and Sunday before the 18:00 ET Globex open.
    if not is_always_open:
        if local.weekday() == 5:
            return SessionConsistencyDecision(
                False,
                "Saturday is context-only for futures execution.",
                details,
            )
        if local.weekday() == 6 and not is_sunday_futures_session:
            reason = (
                f"Sunday futures execution begins at {SUNDAY_FUTURES_OPEN.strftime('%H:%M')} "
                f"{config.timezone_name}; current local time is {local.strftime('%H:%M')}."
                if is_supported_futures
                else "Sunday is context-only for this market."
            )
            return SessionConsistencyDecision(False, reason, details)

    # Weekday futures use the configured execution window. The 7.2L default is
    # effectively full-day so named eval sessions, rather than an old cash-only
    # calibration window, decide when primary risk can be added.
    start = _parse_hhmm(config.session_start, time(0, 0))
    end = _parse_hhmm(config.session_end, time(23, 59))
    if not is_always_open and not is_sunday_futures_session:
        if not (start <= local_clock < end):
            return SessionConsistencyDecision(
                False,
                f"Outside selected {config.session_start}-{config.session_end} {config.timezone_name} trade window.",
                details,
            )

    stats = _day_stats(connection, created_at, config.timezone)
    details["day_stats"] = stats

    if config.base_win_lock_dollars > 0 and stats["realized_pnl"] >= config.base_win_lock_dollars:
        return SessionConsistencyDecision(
            False,
            f"Base winning day secured at ${stats['realized_pnl']:.2f}; stop adding risk and bank the day.",
            details,
        )

    if config.max_trades_per_day > 0 and stats["trades"] >= config.max_trades_per_day:
        return SessionConsistencyDecision(
            False,
            f"Calibration daily trade cap reached ({stats['trades']}/{config.max_trades_per_day}).",
            details,
        )

    if stats["losses"] >= 1:
        second_ok, second_reason, second_details = _second_chance_quality(setup, config)
        details["second_chance"] = second_details
        if not second_ok:
            return SessionConsistencyDecision(False, second_reason, details)

    execution_scope = "multi-timeframe" if config.all_timeframes_enabled else config.execution_timeframe
    if is_always_open:
        session_text = "24/7 market session active"
    elif is_sunday_futures_session:
        session_text = "Sunday Globex session active"
    else:
        session_text = "selected weekday session window active"

    trade_limit_text = (
        f"{stats['trades']}/{config.max_trades_per_day} trades"
        if config.max_trades_per_day > 0
        else f"{stats['trades']} trades (no count cap)"
    )
    return SessionConsistencyDecision(
        True,
        (
            f"{session_text}; {execution_scope} execution lets this {setup.timeframe} candidate proceed to A/A+ grading. "
            f"Day is {trade_limit_text} and ${stats['realized_pnl']:+.2f}."
        ),
        details,
    )


def session_profile_snapshot(reference_time: datetime | None = None) -> dict:
    config = SessionConsistencyConfig.from_env()
    reference_time = reference_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    local = reference_time.astimezone(config.timezone)
    return {
        **asdict(config),
        "profile": "SESSION_CONSISTENCY_5_4",
        "multi_timeframe_execution": config.all_timeframes_enabled,
        "supported_execution_timeframes": sorted(SUPPORTED_EXECUTION_TIMEFRAMES),
        "always_open_symbols": sorted(ALWAYS_OPEN_SYMBOLS),
        "futures_symbols": sorted(FUTURES_SYMBOLS),
        "sunday_futures_open": SUNDAY_FUTURES_OPEN.strftime("%H:%M"),
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
    }
