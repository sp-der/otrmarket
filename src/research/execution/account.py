from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PropConfig:
    starting_balance: float = 50_000.0
    profit_target: float = 3_000.0
    max_loss_limit: float = 2_000.0
    firm_daily_loss_limit: float = 1_200.0
    internal_daily_stop: float = 750.0
    initial_trail_balance: float = 52_100.0
    locked_mll_balance: float = 50_100.0
    max_trades_per_day: int = 4
    max_consecutive_losses: int = 3
    max_concurrent_positions: int = 1
    max_micros: int = 40
    session_profit_cap: float = 1_500.0
    no_new_trades_after_et: str = "16:30"
    resume_trading_et: str = "18:00"
    continue_after_target: bool = True
    trailing_loss_basis: str = "END_OF_DAY_BALANCE"
    session_timezone: str = "America/New_York"
    profile_verification: str = "RESEARCH_REFERENCE_PROFILE"
    base_risk: float = 250.0
    minimum_risk: float = 250.0

    @classmethod
    def from_snapshot(cls, data: dict):
        aliases = {
            "start_balance":"starting_balance", "max_loss":"max_loss_limit",
            "firm_daily_loss":"firm_daily_loss_limit", "max_concurrent":"max_concurrent_positions",
        }
        fields = cls.__dataclass_fields__
        values = {}
        for key, value in data.items():
            target = aliases.get(key, key)
            if target in fields:
                values[target] = value
        return cls(**values)


def reference_account_profile(overrides: dict | None = None) -> dict:
    """Complete, explicit research snapshot; never claims deployed verification."""
    from dataclasses import asdict
    values = asdict(PropConfig())
    values.update(overrides or {})
    values["profile_verification"] = values.get("profile_verification") or "RESEARCH_REFERENCE_PROFILE"
    return values


def session_name(value: datetime) -> str:
    local = value.astimezone(NY)
    clock = local.time().replace(tzinfo=None)
    if time(18) <= clock < time(21): return "ASIA"
    if clock >= time(21) or clock < time(2): return "TOKYO"
    if time(2) <= clock < time(8): return "LONDON"
    if time(8) <= clock < time(16,30): return "NEW_YORK"
    return "CLOSED_WINDOW"


def trading_day(value: datetime):
    local = value.astimezone(NY)
    return (local.date() if local.time().replace(tzinfo=None) >= time(18) else (local.date()))


@dataclass
class AccountState:
    config: PropConfig
    balance: float = 0.0
    peak_equity: float = 0.0
    peak_balance: float = 0.0
    committed_risk: float = 0.0
    open_positions: int = 0
    consecutive_losses: int = 0
    daily_pnl: dict = field(default_factory=dict)
    daily_trades: dict = field(default_factory=dict)
    session_pnl: dict = field(default_factory=dict)
    symbol_losses: dict = field(default_factory=dict)
    last_exit: dict = field(default_factory=dict)
    closed_results: list = field(default_factory=list)
    equity_points: list = field(default_factory=list)
    daily_equity_lows: dict = field(default_factory=dict)
    session_equity_lows: dict = field(default_factory=dict)

    def __post_init__(self):
        self.balance = self.balance or self.config.starting_balance
        self.peak_equity = self.peak_equity or self.balance
        self.peak_balance = self.peak_balance or self.balance

    def recovery(self, symbol, grade, timestamp):
        day = trading_day(timestamp)
        symbol_losses = self.symbol_losses.get((day,symbol), 0)
        streak = 0
        for result in reversed(self.closed_results):
            if result[0] != day: continue
            if result[2] != "LOSS": break
            streak += 1
        if streak >= 2:
            if grade == "B+": return False, 0.0, "ACCOUNT_RECOVERY", "B+ blocked after two consecutive futures losses"
            return True, 0.35 if grade == "A+" else 0.30, "ACCOUNT_RECOVERY", "Operation 7.0 portfolio recovery risk cap"
        if symbol_losses:
            if grade == "B+": return False, 0.0, "SYMBOL_RECOVERY", f"B+ {symbol} blocked after symbol loss"
            return True, 0.60 if grade == "A+" else 0.50, "SYMBOL_RECOVERY", "Operation 7.0 losing-symbol recovery risk cap"
        return True, 1.0, "NORMAL", "Normal Operation 7.0 risk"

    def can_open(self, symbol, grade, timestamp):
        day, session = trading_day(timestamp), session_name(timestamp)
        local_clock = timestamp.astimezone(NY).time().replace(tzinfo=None)
        stop_h, stop_m = map(int, self.config.no_new_trades_after_et.split(":"))
        resume_h, resume_m = map(int, self.config.resume_trading_et.split(":"))
        if time(stop_h,stop_m) <= local_clock < time(resume_h,resume_m): return False, "SESSION_LOCK", "No-new-risk window active", 0, "NORMAL"
        mll_floor = self.mll_floor(day)
        if self.balance <= mll_floor: return False, "BREACHED", "Trailing maximum-loss floor reached", 0, "NORMAL"
        pnl = self.daily_pnl.get(day, 0.0)
        if pnl <= -abs(self.config.firm_daily_loss_limit): return False, "FIRM_DLL_LOCK", "Firm daily loss limit reached", 0, "NORMAL"
        if pnl <= -abs(self.config.internal_daily_stop): return False, "DAILY_LOCK", "Internal daily stop reached", 0, "NORMAL"
        if self.daily_trades.get(day, 0) >= self.config.max_trades_per_day: return False, "DAILY_LOCK", "Maximum daily trades reached", 0, "NORMAL"
        if self.consecutive_losses >= self.config.max_consecutive_losses: return False, "DAILY_LOCK", "Consecutive-loss breaker reached", 0, "NORMAL"
        if self.open_positions >= self.config.max_concurrent_positions: return False, "POSITION_LOCK", "Maximum concurrent positions reached", 0, "NORMAL"
        if self.session_pnl.get((day,session), 0.0) >= self.config.session_profit_cap: return False, "SESSION_PROFIT_LOCK", "Session profit cap reached", 0, "NORMAL"
        if self.balance >= self.config.starting_balance + self.config.profit_target and not self.config.continue_after_target:
            return False, "PASSED", "Evaluation profit target reached", 0, "NORMAL"
        allowed, cap, recovery, reason = self.recovery(symbol, grade, timestamp)
        return allowed, "ACTIVE" if allowed else "RECOVERY_BLOCK", reason, cap, recovery

    def mll_floor(self, current_day=None):
        basis=self.config.trailing_loss_basis.upper()
        if basis=="REALIZED_BALANCE":
            return self.config.locked_mll_balance if self.peak_balance>=self.config.initial_trail_balance else max(self.config.starting_balance-self.config.max_loss_limit,self.peak_balance-self.config.max_loss_limit)
        if basis=="INTRADAY_EQUITY":
            return self.config.locked_mll_balance if self.peak_equity>=self.config.initial_trail_balance else max(self.config.starting_balance-self.config.max_loss_limit,self.peak_equity-self.config.max_loss_limit)
        completed = [(day,pnl) for day,pnl in self.daily_pnl.items() if current_day is None or day < current_day]
        cumulative, peak_eod = 0.0, self.config.starting_balance
        for _, pnl in sorted(completed):
            cumulative += pnl
            peak_eod = max(peak_eod, self.config.starting_balance + cumulative)
        return self.config.locked_mll_balance if peak_eod >= self.config.initial_trail_balance else max(self.config.starting_balance-self.config.max_loss_limit, peak_eod-self.config.max_loss_limit)

    def cooldown(self, symbol, timestamp):
        prior = self.last_exit.get(symbol)
        if not prior: return True, "No prior exit"
        when, result = prior
        minutes = 30 if result == "LOSS" else 20
        elapsed = (timestamp-when).total_seconds()/60
        return (elapsed >= minutes, f"{max(0,minutes-elapsed):.0f} minutes of same-symbol cooldown remain")

    def fill(self, timestamp, risk):
        day = trading_day(timestamp)
        self.open_positions += 1; self.committed_risk += risk
        self.daily_trades[day] = self.daily_trades.get(day,0)+1
        self.record(timestamp,"FILL")

    def close(self, symbol, timestamp, pnl, risk, result):
        day, session = trading_day(timestamp), session_name(timestamp)
        self.open_positions -= 1; self.committed_risk = max(0,self.committed_risk-risk)
        self.balance += pnl; self.peak_balance=max(self.peak_balance,self.balance); self.peak_equity = max(self.peak_equity,self.balance)
        self.daily_pnl[day] = self.daily_pnl.get(day,0)+pnl
        self.session_pnl[(day,session)] = self.session_pnl.get((day,session),0)+pnl
        self.consecutive_losses = self.consecutive_losses+1 if result == "LOSS" else 0
        if result == "LOSS": self.symbol_losses[(day,symbol)] = self.symbol_losses.get((day,symbol),0)+1
        self.closed_results.append((day,symbol,result)); self.last_exit[symbol]=(timestamp,result)
        self.record(timestamp,"EXIT")

    def record(self, timestamp, event_type, unrealized=0.0, intrabar_adverse=0.0):
        equity = self.balance + unrealized
        self.peak_equity=max(self.peak_equity,equity); dd=self.peak_equity-equity
        day, session=trading_day(timestamp),session_name(timestamp)
        self.daily_equity_lows[day]=min(self.daily_equity_lows.get(day,equity),equity)
        self.session_equity_lows[(day,session)]=min(self.session_equity_lows.get((day,session),equity),equity)
        self.equity_points.append({"timestamp":timestamp.isoformat(),"event_type":event_type,"balance":self.balance,
          "realized_pnl":self.balance-self.config.starting_balance,"unrealized_pnl":unrealized,
          "equity":equity,"peak_balance":self.peak_balance,"peak_equity":self.peak_equity,
          "realized_drawdown":self.peak_balance-self.balance,"equity_drawdown":dd,"intrabar_approximate_drawdown":intrabar_adverse,
          "drawdown_dollars":dd,
          "drawdown_percent":100*dd/self.peak_equity if self.peak_equity else 0,
          "daily_pnl":self.daily_pnl.get(day,0),"session_pnl":self.session_pnl.get((day,session),0),
          "daily_equity_low":self.daily_equity_lows[day],"session_equity_low":self.session_equity_lows[(day,session)],"open_risk":self.committed_risk})
