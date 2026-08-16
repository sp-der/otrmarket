from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
import os
import sqlite3
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


@dataclass(frozen=True)
class EvaluationConfig:
    enabled: bool = True
    profile: str = "LUCID_PRO_50K"
    phase: str = "EVALUATION"
    starting_balance: float = 50_000.0
    profit_target: float = 3_000.0
    max_loss_limit: float = 2_000.0
    firm_daily_loss_limit: float = 1_200.0
    initial_trail_balance: float = 52_100.0
    locked_mll_balance: float = 50_100.0
    max_micros: int = 40

    # OTR internal training limits. These are intentionally stricter than the
    # firm limits and can be tuned after replay statistics are large enough.
    risk_per_trade: float = 250.0
    min_risk_per_trade: float = 250.0
    internal_daily_stop: float = 750.0
    mll_safety_buffer: float = 400.0
    max_trades_per_day: int = 4
    max_consecutive_losses: int = 3
    max_concurrent_positions: int = 1
    no_new_trades_after_et: str = "16:30"
    resume_trading_et: str = "18:00"

    # Keep the $3k target as the evaluation pass marker while optionally
    # continuing the replay/paper run so a full week can be measured.
    # The dataclass default remains conservative for direct/test construction;
    # from_env enables continuation by default for the deployed research bot.
    continue_after_target: bool = False

    @classmethod
    def from_env(cls) -> "EvaluationConfig":
        return cls(
            enabled=_env_bool("EVAL_GUARD_ENABLED", True),
            profile=os.getenv("EVAL_PROFILE", "LUCID_PRO_50K").strip().upper(),
            phase=os.getenv("EVAL_PHASE", "EVALUATION").strip().upper(),
            starting_balance=_env_float("EVAL_START_BALANCE", 50_000.0),
            profit_target=_env_float("EVAL_PROFIT_TARGET", 3_000.0),
            max_loss_limit=_env_float("EVAL_MAX_LOSS", 2_000.0),
            firm_daily_loss_limit=_env_float("EVAL_FIRM_DAILY_LOSS", 1_200.0),
            initial_trail_balance=_env_float("EVAL_INITIAL_TRAIL_BALANCE", 52_100.0),
            locked_mll_balance=_env_float("EVAL_LOCKED_MLL_BALANCE", 50_100.0),
            max_micros=_env_int("EVAL_MAX_MICROS", 40),
            risk_per_trade=_env_float("EVAL_RISK_PER_TRADE", 250.0),
            min_risk_per_trade=_env_float("EVAL_MIN_RISK_PER_TRADE", 250.0),
            internal_daily_stop=_env_float("EVAL_INTERNAL_DAILY_STOP", 750.0),
            mll_safety_buffer=_env_float("EVAL_MLL_SAFETY_BUFFER", 400.0),
            max_trades_per_day=_env_int("EVAL_MAX_TRADES_PER_DAY", 4),
            max_consecutive_losses=_env_int("EVAL_MAX_CONSECUTIVE_LOSSES", 3),
            max_concurrent_positions=_env_int("EVAL_MAX_CONCURRENT", 1),
            no_new_trades_after_et=os.getenv("EVAL_NO_NEW_AFTER_ET", "16:30").strip(),
            resume_trading_et=os.getenv("EVAL_RESUME_ET", "18:00").strip(),
            continue_after_target=_env_bool("EVAL_CONTINUE_AFTER_TARGET", True),
        )


@dataclass(frozen=True)
class EvaluationDecision:
    allowed: bool
    risk_dollars: float
    status: str
    reason: str
    snapshot: dict


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


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except Exception:
        return fallback


class EvaluationRiskGuard:
    """Prop-account training governor for OTR paper/replay execution.

    It does not claim to guarantee a pass. It prevents OTR from accepting new
    paper orders when its configured account/risk budget says the setup would
    be irresponsible for the evaluation profile.
    """

    def __init__(self, config: EvaluationConfig | None = None):
        self.config = config or EvaluationConfig.from_env()

    def _rows(self, connection: sqlite3.Connection):
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
        if "risk_dollars" not in columns:
            return []
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT setup_id, status, opened_at, closed_at, result,
                   result_r, risk_dollars, result_dollars, updated_at
            FROM paper_trades
            WHERE risk_dollars IS NOT NULL AND risk_dollars > 0
            ORDER BY COALESCE(closed_at, opened_at, updated_at) ASC
            """
        ).fetchall()

    def snapshot(self, connection: sqlite3.Connection, reference_time: datetime | None = None) -> dict:
        c = self.config
        reference_time = reference_time or datetime.now(timezone.utc)
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        reference_time = reference_time.astimezone(timezone.utc)
        current_day = reference_time.astimezone(NY).date()
        rows = self._rows(connection)

        realized = 0.0
        daily_results: dict[object, float] = {}
        trades_by_day: dict[object, int] = {}
        committed_risk = 0.0
        active_positions = 0
        closed_sequence = []

        for row in rows:
            result_dollars = float(row["result_dollars"] or 0.0)
            if row["status"] == "CLOSED":
                realized += result_dollars
                closed_at = _parse_dt(row["closed_at"])
                if closed_at:
                    day = closed_at.astimezone(NY).date()
                    daily_results[day] = daily_results.get(day, 0.0) + result_dollars
                closed_sequence.append(str(row["result"] or ""))
            if row["status"] in {"PENDING", "OPEN"}:
                committed_risk += float(row["risk_dollars"] or 0.0)
                active_positions += 1
            opened_at = _parse_dt(row["opened_at"])
            if opened_at:
                day = opened_at.astimezone(NY).date()
                trades_by_day[day] = trades_by_day.get(day, 0) + 1

        # End-of-day trailing MLL. Today's intraday gains do not move the floor;
        # only completed prior sessions can move the EOD trail.
        cumulative = 0.0
        peak_eod_balance = c.starting_balance
        for day in sorted(daily_results):
            if day >= current_day:
                break
            cumulative += daily_results[day]
            peak_eod_balance = max(peak_eod_balance, c.starting_balance + cumulative)

        if peak_eod_balance >= c.initial_trail_balance:
            mll_floor = c.locked_mll_balance
        else:
            mll_floor = max(c.starting_balance - c.max_loss_limit, peak_eod_balance - c.max_loss_limit)

        balance = c.starting_balance + realized
        cushion = balance - mll_floor
        day_pnl = daily_results.get(current_day, 0.0)
        trades_today = trades_by_day.get(current_day, 0)

        consecutive_losses = 0
        for result in reversed(closed_sequence):
            if result == "LOSS":
                consecutive_losses += 1
            else:
                break

        target_met = c.phase == "EVALUATION" and c.profit_target > 0 and realized >= c.profit_target
        profit_progress = max(0.0, min(1.0, realized / c.profit_target)) if c.profit_target > 0 else 0.0
        largest_day_profit = max([0.0] + [value for value in daily_results.values() if value > 0])
        positive_profit = max(0.0, realized)
        consistency = (largest_day_profit / positive_profit * 100.0) if positive_profit > 0 else None

        # Safety locks always outrank the profit target. Hitting $3k is a pass
        # marker, not permission to ignore the MLL/daily/concurrency governors.
        status = "ACTIVE"
        reason = "Within OTR evaluation risk limits."
        if not c.enabled:
            status, reason = "DISABLED", "Evaluation guard disabled."
        elif balance <= mll_floor:
            status, reason = "BREACHED", "Paper balance reached the modeled Max Loss Limit floor."
        elif day_pnl <= -abs(c.firm_daily_loss_limit):
            status, reason = "FIRM_DLL_LOCK", "Modeled firm daily loss limit reached; no more trades this session."
        elif day_pnl <= -abs(c.internal_daily_stop):
            status, reason = "DAILY_LOCK", "OTR internal daily stop reached."
        elif trades_today >= c.max_trades_per_day:
            status, reason = "DAILY_LOCK", "OTR maximum trades for this session reached."
        elif consecutive_losses >= c.max_consecutive_losses:
            status, reason = "DAILY_LOCK", "OTR consecutive-loss circuit breaker reached."
        elif active_positions >= c.max_concurrent_positions:
            status, reason = "POSITION_LOCK", "OTR already has the maximum allowed active paper position."

        local_t = reference_time.astimezone(NY).time().replace(tzinfo=None)
        stop_time = _parse_hhmm(c.no_new_trades_after_et, time(16, 30))
        resume_time = _parse_hhmm(c.resume_trading_et, time(18, 0))
        if stop_time <= local_t < resume_time and status == "ACTIVE":
            status, reason = "SESSION_LOCK", f"No new OTR trades between {c.no_new_trades_after_et} and {c.resume_trading_et} ET."

        if target_met and status == "ACTIVE":
            if c.continue_after_target:
                status = "PASSED_CONTINUING"
                reason = "Evaluation profit target reached; continuing the paper/replay run under normal risk locks."
            else:
                status = "PASSED"
                reason = "Configured evaluation profit target reached."

        internal_loss_used = max(0.0, -day_pnl)
        internal_daily_headroom = max(0.0, abs(c.internal_daily_stop) - internal_loss_used)
        firm_daily_headroom = max(0.0, abs(c.firm_daily_loss_limit) - max(0.0, -day_pnl))
        mll_headroom = max(0.0, cushion - c.mll_safety_buffer - committed_risk)
        available_risk = max(0.0, min(c.risk_per_trade, internal_daily_headroom, firm_daily_headroom, mll_headroom))

        return {
            "enabled": c.enabled,
            "profile": c.profile,
            "phase": c.phase,
            "status": status,
            "reason": reason,
            "starting_balance": c.starting_balance,
            "balance": balance,
            "realized_pnl": realized,
            "profit_target": c.profit_target,
            "profit_progress": profit_progress,
            "target_met": target_met,
            "continue_after_target": c.continue_after_target,
            "mll_floor": mll_floor,
            "mll_cushion": cushion,
            "mll_safety_buffer": c.mll_safety_buffer,
            "peak_eod_balance": peak_eod_balance,
            "firm_daily_loss_limit": c.firm_daily_loss_limit,
            "internal_daily_stop": c.internal_daily_stop,
            "today_pnl": day_pnl,
            "trades_today": trades_today,
            "max_trades_per_day": c.max_trades_per_day,
            "consecutive_losses": consecutive_losses,
            "max_consecutive_losses": c.max_consecutive_losses,
            "active_positions": active_positions,
            "max_concurrent_positions": c.max_concurrent_positions,
            "committed_risk": committed_risk,
            "base_risk_per_trade": c.risk_per_trade,
            "available_risk": available_risk,
            "max_micros": c.max_micros,
            "largest_day_profit": largest_day_profit,
            "consistency_pct": consistency,
            "reference_time": reference_time.isoformat(),
        }

    def decide(self, connection: sqlite3.Connection, reference_time: datetime | None = None) -> EvaluationDecision:
        snap = self.snapshot(connection, reference_time)
        if not self.config.enabled:
            return EvaluationDecision(True, self.config.risk_per_trade, snap["status"], snap["reason"], snap)
        if snap["status"] not in {"ACTIVE", "PASSED_CONTINUING"}:
            return EvaluationDecision(False, 0.0, snap["status"], snap["reason"], snap)
        risk = float(snap["available_risk"] or 0.0)
        if risk < self.config.min_risk_per_trade:
            reason = f"Available risk ${risk:.2f} is below OTR minimum ${self.config.min_risk_per_trade:.2f}."
            snap = {**snap, "status": "RISK_LOCK", "reason": reason}
            return EvaluationDecision(False, 0.0, "RISK_LOCK", reason, snap)
        if snap["status"] == "PASSED_CONTINUING":
            reason = f"Evaluation target already reached; continuing weekly paper test with ${risk:.2f} risk."
            return EvaluationDecision(True, risk, "PASSED_CONTINUING", reason, snap)
        return EvaluationDecision(True, risk, "ACTIVE", f"Approved with ${risk:.2f} paper risk.", snap)
