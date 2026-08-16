from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import os
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    """Calibration profile for repeatable funded-style trading days.

    The profile deliberately targets one focused execution timeframe and one
    liquid daytime window. It never forces a trade. A day can finish PASS/NO
    TRADE while rejected candidates remain available in setup history for
    research.
    """

    timezone_name: str = "America/New_York"
    execution_timeframe: str = "5m"
    session_start: str = "09:30"
    session_end: str = "13:00"
    max_trades_per_day: int = 2
    base_win_lock_dollars: float = 250.0
    second_chance_min_rr: float = 2.0
    second_chance_body_ratio: float = 1.90
    second_chance_range_ratio: float = 1.50

    @classmethod
    def from_env(cls) -> "SessionConsistencyConfig":
        return cls(
            timezone_name=os.getenv("OTR_SESSION_TZ", "America/New_York").strip(),
            execution_timeframe=os.getenv("OTR_EXECUTION_TIMEFRAME", "5m").strip(),
            session_start=os.getenv("OTR_SESSION_START", "09:30").strip(),
            session_end=os.getenv("OTR_SESSION_END", "13:00").strip(),
            max_trades_per_day=_env_int("OTR_CALIBRATION_MAX_TRADES_DAY", 2),
            base_win_lock_dollars=_env_float("OTR_BASE_WIN_LOCK_DOLLARS", 250.0),
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

    details = {
        "profile": "SESSION_CONSISTENCY_5_1",
        "timezone": config.timezone_name,
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
        "execution_timeframe": config.execution_timeframe,
        "session_start": config.session_start,
        "session_end": config.session_end,
        "max_trades_per_day": config.max_trades_per_day,
        "base_win_lock_dollars": config.base_win_lock_dollars,
    }

    if setup.timeframe != config.execution_timeframe:
        return SessionConsistencyDecision(
            False,
            f"Calibration profile is executing {config.execution_timeframe}; {setup.timeframe} stays scanner/shadow only.",
            details,
        )

    # Keep the calibration focused on the regular weekday futures session. The
    # Sunday evening open is still ingested for context, but is not forced into
    # a trade just to increase the sample count.
    if local.weekday() >= 5:
        return SessionConsistencyDecision(
            False,
            f"{local.strftime('%A')} is context-only in the calibration profile.",
            details,
        )

    start = _parse_hhmm(config.session_start, time(9, 30))
    end = _parse_hhmm(config.session_end, time(13, 0))
    local_clock = local.time().replace(tzinfo=None)
    if not (start <= local_clock < end):
        return SessionConsistencyDecision(
            False,
            f"Outside selected {config.session_start}-{config.session_end} {config.timezone_name} trade window.",
            details,
        )

    stats = _day_stats(connection, created_at, config.timezone)
    details["day_stats"] = stats

    if stats["realized_pnl"] >= config.base_win_lock_dollars:
        return SessionConsistencyDecision(
            False,
            f"Base winning day secured at ${stats['realized_pnl']:.2f}; stop adding risk and bank the day.",
            details,
        )

    if stats["trades"] >= config.max_trades_per_day:
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

    return SessionConsistencyDecision(
        True,
        (
            f"Selected {config.execution_timeframe} session window active; "
            f"day is {stats['trades']}/{config.max_trades_per_day} trades and ${stats['realized_pnl']:+.2f}."
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
        "profile": "SESSION_CONSISTENCY_5_1",
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
    }
