from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os

from src.research.execution.contracts import execution_contract, micro_spec
from src.risk.geometry import tick_size_for, validate_trade_geometry
from src.strategies.models import StrategySetup


# Cutover marker for realistic whole-contract paper accounting. Rows written
# before this patch have no accounting_version and keep their original
# theoretical (risk_dollars / risk_dollars * RR) P/L; they are never rewritten.
PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1 = "MGC_WHOLE_CONTRACT_V1"

# Whole-contract sizing currently applies only to Gold (MGC). Other symbols
# keep the existing theoretical dollar-budget accounting.
WHOLE_CONTRACT_SYMBOLS = {"GC"}

CANNOT_SIZE_RESULT = "CANNOT_SIZE_MGC"

# Contract ceiling used when no operator-supplied cap is present. It matches
# EvaluationConfig.max_micros / PropConfig.max_micros (40) and the documented
# Operation 8.1 envelope, NOT the deliberately conservative live-broker default
# of 1 in ExecutionConfig (which exists so an unarmed deployment can never size
# a real order). See default_max_micros_cap() for the resolution order.
DEFAULT_PAPER_MAX_MICROS = 40


@dataclass(frozen=True)
class ContractSizing:
    sizeable: bool
    quantity: int
    actual_risk_dollars: float
    per_contract_risk: float
    contract_multiplier: float
    execution_contract: str
    max_micros_cap: int
    unused_risk_dollars: float
    # True when the cap -- not the requested risk budget -- decided the size.
    # A binding cap silently shrinks every trade and makes the whole paper
    # ledger unreadable, so it is recorded and surfaced instead of hidden.
    cap_binding: bool = False
    uncapped_quantity: int = 0
    # True when the resolved cap came from the fallback default rather than an
    # explicit operator setting.
    cap_from_default: bool = False


def default_max_micros_cap() -> int:
    """Resolve the paper contract ceiling with an explicit precedence order.

    1. ``OTR_PAPER_MAX_MICROS`` -- paper/replay-only override.
    2. ``OTR_EXECUTION_MAX_MICROS`` -- the operator-configured ceiling the live
       sizing path also obeys (src.execution.live.sizing).
    3. ``EVAL_MAX_MICROS`` -- the evaluation-profile ceiling (default 40).
    4. ``DEFAULT_PAPER_MAX_MICROS`` (40).

    Steps 3-4 exist because ``ExecutionConfig.max_micros`` defaults to 1 on
    purpose: it is the fail-safe for an unarmed broker deployment. Inheriting
    that 1 into the *research* ledger silently sized every paper trade at one
    micro contract (e.g. $30 of risk against a $750 A+ budget), which made
    replay P&L roughly 1/25th of the intended scale while still looking
    internally consistent.
    """

    def _read(name: str):
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            return None
        try:
            return max(1, int(float(str(raw).strip())))
        except (TypeError, ValueError):
            return None

    for name in ("OTR_PAPER_MAX_MICROS", "OTR_EXECUTION_MAX_MICROS", "EVAL_MAX_MICROS"):
        value = _read(name)
        if value is not None:
            return value
    return DEFAULT_PAPER_MAX_MICROS


def cap_from_default() -> bool:
    """True when no explicit contract ceiling is configured anywhere."""
    return not any(
        str(os.getenv(name) or "").strip()
        for name in ("OTR_PAPER_MAX_MICROS", "OTR_EXECUTION_MAX_MICROS", "EVAL_MAX_MICROS")
    )


def size_whole_contract(
    setup: StrategySetup,
    requested_risk_dollars: float,
    *,
    max_micros_cap: int | None = None,
) -> ContractSizing:
    """Floor-size a paper trade to whole contracts using the same contract
    assumptions (tick size, point value) as the live/Nautilus execution path,
    then cap it at the same configured OTR_EXECUTION_MAX_MICROS ceiling live
    execution already enforces.

    Never forces a minimum of 1 contract: if the requested risk cannot fund
    even one contract -- before the cap is even considered -- sizing is
    rejected (sizeable=False) rather than silently over-risking the account.
    The cap can only ever reduce an already-sizeable quantity (it is always
    >= 1), so it never itself causes a CANNOT_SIZE_MGC rejection.
    """
    spec = micro_spec(setup.symbol)
    contract = execution_contract(setup.symbol)
    multiplier = float(spec.point_value)
    per_contract_risk = abs(float(setup.entry_price) - float(setup.stop_price)) * multiplier
    requested = max(0.0, float(requested_risk_dollars or 0.0))
    cap = int(max_micros_cap) if max_micros_cap is not None else default_max_micros_cap()
    cap = max(1, cap)
    from_default = max_micros_cap is None and cap_from_default()

    if per_contract_risk <= 0:
        return ContractSizing(False, 0, 0.0, per_contract_risk, multiplier, contract, cap, 0.0,
                              False, 0, from_default)

    uncapped_quantity = math.floor(requested / per_contract_risk)
    if uncapped_quantity < 1:
        return ContractSizing(False, 0, 0.0, per_contract_risk, multiplier, contract, cap, 0.0,
                              False, 0, from_default)

    quantity = min(uncapped_quantity, cap)
    actual_risk = quantity * per_contract_risk
    unused_risk = max(0.0, requested - actual_risk)
    return ContractSizing(
        True, int(quantity), float(actual_risk), per_contract_risk, multiplier, contract, cap, float(unused_risk),
        bool(quantity < uncapped_quantity), int(uncapped_quantity), from_default,
    )


_PENDING_BARS = {
    "1m": 6,
    "5m": 4,
    "15m": 3,
    "1h": 2,
}

_BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}

_MAX_PREENTRY_TARGET_PROGRESS = 0.75

# ---------------------------------------------------------------------------
# Execution-friction model for the research ledger.
#
# The historical OTR paper ledger is GROSS: a limit fills exactly at the
# planned price, a stop fills exactly at the stop, and nothing is charged for
# commissions or exchange fees. Every expectancy number derived from it is
# therefore an upper bound. REALISTIC adds the two frictions that actually move
# a Gold micro account: stop slippage (stops are market orders and they gap)
# and per-contract round-turn costs.
#
# GROSS remains selectable so pre-existing runs stay directly comparable; it is
# never applied silently to new work without saying so at startup.
# ---------------------------------------------------------------------------
COST_MODEL_GROSS = "GROSS"
COST_MODEL_REALISTIC = "REALISTIC"
COST_MODELS = (COST_MODEL_GROSS, COST_MODEL_REALISTIC)

# Mid-market all-in round-turn costs for one MGC contract at a discount
# futures broker (commission + exchange/clearing/NFA fees). Replace with your
# own broker's numbers before trusting any expectancy figure.
DEFAULT_ROUND_TURN_COMMISSION = 1.24
DEFAULT_ROUND_TURN_FEES = 0.36
# MGC tick = $0.10 = $1.00 of P&L per contract. One tick of stop slippage is
# the optimistic end for Gold outside the London/NY overlap.
DEFAULT_STOP_SLIPPAGE_TICKS = 1


@dataclass(frozen=True)
class PaperCostModel:
    model: str = COST_MODEL_GROSS
    slippage_ticks: int = DEFAULT_STOP_SLIPPAGE_TICKS
    round_turn_commission: float = DEFAULT_ROUND_TURN_COMMISSION
    round_turn_fees: float = DEFAULT_ROUND_TURN_FEES

    def __post_init__(self):
        if str(self.model).strip().upper() not in COST_MODELS:
            raise ValueError(f"Unknown paper cost model: {self.model!r}. Expected one of {COST_MODELS}.")

    @classmethod
    def from_env(cls) -> "PaperCostModel":
        raw = str(os.getenv("OTR_PAPER_COST_MODEL", COST_MODEL_GROSS) or COST_MODEL_GROSS).strip().upper()

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _int(name: str, default: int) -> int:
            try:
                return int(float(os.getenv(name, str(default))))
            except (TypeError, ValueError):
                return default

        return cls(
            model=raw if raw in COST_MODELS else COST_MODEL_GROSS,
            slippage_ticks=max(0, _int("OTR_PAPER_STOP_SLIPPAGE_TICKS", DEFAULT_STOP_SLIPPAGE_TICKS)),
            round_turn_commission=max(0.0, _float("OTR_PAPER_ROUND_TURN_COMMISSION", DEFAULT_ROUND_TURN_COMMISSION)),
            round_turn_fees=max(0.0, _float("OTR_PAPER_ROUND_TURN_FEES", DEFAULT_ROUND_TURN_FEES)),
        )

    @property
    def enabled(self) -> bool:
        return self.model == COST_MODEL_REALISTIC

    def exit_fill_price(self, *, direction: str, stop_hit: bool, level: float, market_price: float, tick: float) -> float:
        """Return the price a bracket exit would realistically fill at.

        Targets are resting limits and fill exactly at the level. Stops become
        market orders: they fill at the stop when price merely touches it, and
        at the (worse) market price when price gaps through -- plus the
        configured slippage allowance in both cases.
        """
        fill = float(level)
        if self.enabled and stop_hit and tick > 0:
            if direction == "bullish":
                fill = min(float(level), float(market_price))
                fill -= self.slippage_ticks * tick
            else:
                fill = max(float(level), float(market_price))
                fill += self.slippage_ticks * tick
            fill = round(fill / tick) * tick
        return fill


@dataclass
class PaperPosition:
    setup: StrategySetup
    status: str = "PENDING"
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    result_r: float | None = None
    result: str | None = None
    risk_dollars: float | None = None
    result_dollars: float | None = None
    guard_reason: str | None = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    max_favorable_price: float | None = None
    max_adverse_price: float | None = None
    # Whole-contract paper accounting (currently Gold/MGC only). None means
    # this trade predates the migration or is on a symbol not yet migrated;
    # such rows keep the legacy risk_dollars / risk_dollars*RR calculation.
    requested_risk_dollars: float | None = None
    actual_risk_dollars: float | None = None
    quantity: int | None = None
    per_contract_risk: float | None = None
    contract_multiplier: float | None = None
    execution_contract: str | None = None
    accounting_version: str | None = None
    max_micros_cap: int | None = None
    unused_risk_dollars: float | None = None
    # Sizing/cost transparency. Always populated for whole-contract trades so
    # an operator can tell "the strategy is small" apart from "the cap made it
    # small" and gross edge apart from net edge.
    cap_binding: bool = False
    uncapped_quantity: int | None = None
    cap_from_default: bool = False
    cost_model: str = COST_MODEL_GROSS
    result_dollars_gross: float | None = None
    commission_dollars: float | None = None
    fees_dollars: float | None = None
    slippage_dollars: float | None = None
    result_dollars_net: float | None = None
    net_result_r: float | None = None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pending_expiry(setup: StrategySetup) -> datetime:
    bars = _PENDING_BARS.get(setup.timeframe, 4)
    seconds = _BAR_SECONDS.get(setup.timeframe, 60) * bars
    return _aware_utc(setup.created_at) + timedelta(seconds=seconds)


def _preentry_target_progress(setup: StrategySetup, price: float) -> float:
    target_distance = abs(setup.target_price - setup.entry_price)
    if target_distance <= 0:
        return 0.0
    if setup.direction == "bullish" and price > setup.entry_price:
        return (price - setup.entry_price) / target_distance
    if setup.direction == "bearish" and price < setup.entry_price:
        return (setup.entry_price - price) / target_distance
    return 0.0


def _update_excursion(position: PaperPosition, price: float) -> None:
    """Track maximum favorable/adverse excursion in R while a trade is open."""
    setup = position.setup
    risk_distance = abs(float(setup.entry_price) - float(setup.stop_price))
    if risk_distance <= 0:
        return

    if setup.direction == "bullish":
        favorable = max(0.0, (float(price) - float(setup.entry_price)) / risk_distance)
        adverse = max(0.0, (float(setup.entry_price) - float(price)) / risk_distance)
        if position.max_favorable_price is None or price > position.max_favorable_price:
            position.max_favorable_price = float(price)
        if position.max_adverse_price is None or price < position.max_adverse_price:
            position.max_adverse_price = float(price)
    else:
        favorable = max(0.0, (float(setup.entry_price) - float(price)) / risk_distance)
        adverse = max(0.0, (float(price) - float(setup.entry_price)) / risk_distance)
        if position.max_favorable_price is None or price < position.max_favorable_price:
            position.max_favorable_price = float(price)
        if position.max_adverse_price is None or price > position.max_adverse_price:
            position.max_adverse_price = float(price)

    position.mfe_r = max(float(position.mfe_r or 0.0), favorable)
    position.mae_r = max(float(position.mae_r or 0.0), adverse)


def _invalidate_pending(
    position: PaperPosition,
    *,
    timestamp: datetime,
    price: float,
    result: str,
) -> None:
    position.status = "INVALIDATED"
    position.closed_at = timestamp
    position.exit_price = price
    position.result = result
    position.result_dollars = 0.0 if position.risk_dollars is not None else None


class PaperExecutor:
    """Research-only execution. Never sends orders to a broker."""

    def __init__(
        self,
        *,
        pending_expiry_enabled: bool = True,
        stale_preentry_enabled: bool = True,
        cost_model: PaperCostModel | None = None,
    ):
        self.positions: dict[str, PaperPosition] = {}
        self.closed: list[PaperPosition] = []
        self.pending_expiry_enabled = pending_expiry_enabled
        self.stale_preentry_enabled = stale_preentry_enabled
        self.cost_model = cost_model or PaperCostModel.from_env()

    def register_setup(
        self,
        setup: StrategySetup,
        *,
        risk_dollars: float | None = None,
        guard_reason: str | None = None,
        max_micros_cap: int | None = None,
    ) -> PaperPosition:
        # Defense in depth: even if upstream setup construction regresses, an
        # inverted stop/target is never allowed into the paper order book.
        geometry = validate_trade_geometry(
            setup.symbol,
            setup.direction,
            setup.entry_price,
            setup.stop_price,
            setup.target_price,
        )
        if not geometry.valid:
            raise ValueError(geometry.reason)
        position = PaperPosition(
            setup=setup,
            risk_dollars=risk_dollars,
            guard_reason=guard_reason,
        )

        if setup.symbol.upper() in WHOLE_CONTRACT_SYMBOLS and risk_dollars is not None:
            sizing = size_whole_contract(setup, risk_dollars, max_micros_cap=max_micros_cap)
            position.requested_risk_dollars = max(0.0, float(risk_dollars))
            position.per_contract_risk = sizing.per_contract_risk
            position.contract_multiplier = sizing.contract_multiplier
            position.execution_contract = sizing.execution_contract
            position.accounting_version = PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1
            position.max_micros_cap = sizing.max_micros_cap
            position.cap_binding = bool(sizing.cap_binding)
            position.uncapped_quantity = int(sizing.uncapped_quantity)
            position.cap_from_default = bool(sizing.cap_from_default)
            position.cost_model = self.cost_model.model
            if not sizing.sizeable:
                # Do not force a minimum of 1 contract. Reject with a clear,
                # queryable reason instead of silently over-risking, and never
                # add this to self.positions so on_price never touches it.
                closed_at = _aware_utc(setup.created_at)
                position.status = "INVALIDATED"
                position.closed_at = closed_at
                position.result = CANNOT_SIZE_RESULT
                position.result_dollars = 0.0
                position.quantity = 0
                position.actual_risk_dollars = 0.0
                position.unused_risk_dollars = position.requested_risk_dollars
                self.closed.append(position)
                return position
            position.quantity = sizing.quantity
            position.actual_risk_dollars = sizing.actual_risk_dollars
            position.unused_risk_dollars = sizing.unused_risk_dollars

        self.positions[setup.setup_id] = position
        return position

    def on_price(self, symbol: str, price: float, timestamp: datetime | None = None) -> list[PaperPosition]:
        timestamp = _aware_utc(timestamp or datetime.now(timezone.utc))
        changed: list[PaperPosition] = []

        for setup_id, position in list(self.positions.items()):
            setup = position.setup
            if setup.symbol != symbol:
                continue

            if position.status == "PENDING":
                # Live 5.x execution expires stale limits using replay market
                # time. The 4.8 shadow executor can disable this to reproduce
                # the older entry behavior without changing live execution.
                if self.pending_expiry_enabled and timestamp > _pending_expiry(setup):
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="EXPIRED_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                # Avoid fills that have already blown through the protective stop.
                invalid = (
                    price <= setup.stop_price
                    if setup.direction == "bullish"
                    else price >= setup.stop_price
                )
                if invalid:
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="INVALIDATED_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                # Operation 5.0+ cancels an entry after most of its objective has
                # already traded. The 4.8 shadow executor can intentionally turn
                # this off for an apples-to-apples strategy baseline.
                progress = _preentry_target_progress(setup, price)
                if self.stale_preentry_enabled and progress >= _MAX_PREENTRY_TARGET_PROGRESS:
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="STALE_MOVE_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                touched = (
                    price <= setup.entry_price
                    if setup.direction == "bullish"
                    else price >= setup.entry_price
                )
                if touched:
                    position.status = "OPEN"
                    position.opened_at = timestamp
                    position.max_favorable_price = float(setup.entry_price)
                    position.max_adverse_price = float(setup.entry_price)
                    changed.append(position)

            if position.status == "OPEN":
                _update_excursion(position, price)

                if setup.direction == "bullish":
                    stop_hit = price <= setup.stop_price
                    target_hit = price >= setup.target_price
                else:
                    stop_hit = price >= setup.stop_price
                    target_hit = price <= setup.target_price

                if stop_hit or target_hit:
                    position.status = "CLOSED"
                    position.closed_at = timestamp
                    level = float(setup.stop_price if stop_hit else setup.target_price)
                    position.exit_price = self.cost_model.exit_fill_price(
                        direction=str(setup.direction),
                        stop_hit=bool(stop_hit),
                        level=level,
                        market_price=float(price),
                        tick=tick_size_for(str(setup.symbol)),
                    )
                    position.result = "LOSS" if stop_hit else "WIN"
                    position.result_r = -1.0 if stop_hit else setup.risk_reward
                    if position.quantity:
                        # Realistic whole-contract P/L: quantity x actual
                        # point movement x contract multiplier, using the
                        # actual stop/target fill price.
                        point_move = abs(float(position.exit_price) - float(setup.entry_price))
                        signed_move = -point_move if stop_hit else point_move
                        quantity = float(position.quantity)
                        multiplier = float(position.contract_multiplier)
                        gross = signed_move * quantity * multiplier
                        position.result_dollars_gross = gross
                        if self.cost_model.enabled:
                            position.commission_dollars = quantity * float(self.cost_model.round_turn_commission)
                            position.fees_dollars = quantity * float(self.cost_model.round_turn_fees)
                            # Slippage is measured against the planned level so
                            # it is never double-counted with the gap-through
                            # fill already priced in above.
                            slipped_points = abs(float(position.exit_price) - level)
                            position.slippage_dollars = slipped_points * quantity * multiplier
                            position.result_dollars_net = (
                                gross - position.commission_dollars - position.fees_dollars
                            )
                            position.result_dollars = position.result_dollars_net
                        else:
                            position.commission_dollars = 0.0
                            position.fees_dollars = 0.0
                            position.slippage_dollars = 0.0
                            position.result_dollars_net = gross
                            position.result_dollars = gross
                        risk_basis = float(position.actual_risk_dollars or 0.0)
                        position.net_result_r = (
                            round(position.result_dollars_net / risk_basis, 6) if risk_basis > 0 else None
                        )
                    elif position.risk_dollars is not None:
                        position.result_dollars = (
                            -float(position.risk_dollars)
                            if stop_hit
                            else float(position.risk_dollars) * float(setup.risk_reward)
                        )
                        position.result_dollars_gross = position.result_dollars
                        position.result_dollars_net = position.result_dollars
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)

        return changed

    @property
    def open_count(self) -> int:
        return sum(1 for item in self.positions.values() if item.status == "OPEN")

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self.positions.values() if item.status == "PENDING")

    @property
    def total_r(self) -> float:
        return sum(item.result_r or 0.0 for item in self.closed)

    @property
    def total_dollars(self) -> float:
        return sum(item.result_dollars or 0.0 for item in self.closed)
