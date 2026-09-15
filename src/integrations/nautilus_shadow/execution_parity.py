from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from math import isclose
from typing import Iterable

from .gold_replay import StoredGoldTick, _aware_utc, _safe_contract_token, contract_family, contract_multiplier
from .parity import ExecutionSnapshot


@dataclass(frozen=True)
class ShadowBracketIntent:
    setup_id: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    quantity: int = 1
    execution_contract: str = "MGC"

    def validate(self) -> None:
        direction = self.direction.lower()
        if direction not in {"bullish", "bearish"}:
            raise ValueError(f"Unsupported shadow direction: {self.direction}")
        if int(self.quantity) < 1:
            raise ValueError("Shadow bracket quantity must be at least one contract")
        if direction == "bullish" and not (self.stop_price < self.entry_price < self.target_price):
            raise ValueError("Bullish shadow bracket requires stop < entry < target")
        if direction == "bearish" and not (self.target_price < self.entry_price < self.stop_price):
            raise ValueError("Bearish shadow bracket requires target < entry < stop")


@dataclass(frozen=True)
class NautilusOrderState:
    role: str
    side: str
    order_type: str
    status: str
    price: float | None
    trigger_price: float | None
    avg_fill_price: float | None
    filled_quantity: float


@dataclass(frozen=True)
class NautilusBracketResult:
    setup_id: str
    contract: str
    contract_family: str
    multiplier: int
    quantity: int
    status: str
    entry_fill_price: float | None
    exit_fill_price: float | None
    result: str | None
    result_r: float | None
    result_dollars: float | None
    actual_risk_dollars: float
    orders: tuple[NautilusOrderState, ...]
    authoritative: bool = False

    def to_snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            setup_id=self.setup_id,
            status=self.status,
            entry_price=self.entry_fill_price,
            exit_price=self.exit_fill_price,
            result=self.result,
            result_r=self.result_r,
            result_dollars=self.result_dollars,
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["orders"] = [asdict(order) for order in self.orders]
        return payload


def _value_or_call(value, name: str, default=None):
    attribute = getattr(value, name, default)
    return attribute() if callable(attribute) else attribute


def _enum_name(value) -> str:
    name = _value_or_call(value, "name")
    return str(name if name is not None else value).split(".")[-1].upper()


def _float_value(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (float, int, Decimal)):
        return float(value)
    as_double = getattr(value, "as_double", None)
    if callable(as_double):
        return float(as_double())
    as_decimal = getattr(value, "as_decimal", None)
    if callable(as_decimal):
        return float(as_decimal())
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _order_role(order, *, entry_side: str) -> str:
    parent = _value_or_call(order, "parent_order_id")
    if parent is None:
        return "ENTRY"
    order_type = _enum_name(_value_or_call(order, "order_type"))
    side = _enum_name(_value_or_call(order, "side"))
    if order_type in {"STOP_MARKET", "STOP_LIMIT"}:
        return "STOP"
    if side != entry_side:
        return "TARGET"
    return "CHILD"


def _coerce_contract(intent: ShadowBracketIntent, ticks: list[StoredGoldTick]) -> str:
    requested = str(intent.execution_contract or "").strip().upper()
    if requested:
        return requested
    source_contract = next((item.contract for item in reversed(ticks) if item.contract), "MGC")
    parts = source_contract.upper().split(maxsplit=1)
    return f"MGC {parts[1]}" if len(parts) == 2 else "MGC"


def simulate_gold_bracket(
    ticks: Iterable[StoredGoldTick],
    intent: ShadowBracketIntent,
) -> NautilusBracketResult:
    """Run one accepted OTR-style Gold limit bracket through NautilusTrader.

    The result is observational only. This function never reaches NinjaTrader,
    the OTR execution gateway, or a broker account.
    """
    intent.validate()
    items = sorted(list(ticks), key=lambda item: item.timestamp)
    if len(items) < 2:
        raise ValueError("Nautilus execution parity requires at least two Gold ticks")

    try:
        from nautilus_trader.backtest.config import BacktestEngineConfig
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.model.data import QuoteTick
        from nautilus_trader.model.enums import AccountType, AssetClass, OmsType, OrderSide, OrderType
        from nautilus_trader.model.identifiers import InstrumentId, Venue
        from nautilus_trader.model.instruments import FuturesContract
        from nautilus_trader.model.objects import Currency, Money, Price, Quantity
        from nautilus_trader.model import Symbol
        from nautilus_trader.trading.strategy import Strategy
    except ImportError as exc:
        raise RuntimeError(
            f"NautilusTrader execution shadow API import failed: {exc}. Install requirements-nautilus.txt."
        ) from exc

    contract = _coerce_contract(intent, items)
    family = contract_family(contract)
    multiplier = contract_multiplier(contract)
    token = _safe_contract_token(contract)
    instrument_id = InstrumentId.from_str(f"{token}.OTR")

    first_ns = int(_aware_utc(items[0].timestamp).timestamp() * 1_000_000_000)
    activation_ns = int((_aware_utc(items[0].timestamp) - timedelta(days=366)).timestamp() * 1_000_000_000)
    expiration_ns = int((_aware_utc(items[-1].timestamp) + timedelta(days=366)).timestamp() * 1_000_000_000)

    instrument = FuturesContract(
        instrument_id=instrument_id,
        raw_symbol=Symbol(token),
        asset_class=AssetClass.COMMODITY,
        underlying=family,
        activation_ns=activation_ns,
        expiration_ns=expiration_ns,
        currency=Currency.from_str("USD"),
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        multiplier=Quantity.from_int(multiplier),
        lot_size=Quantity.from_int(1),
        ts_event=first_ns,
        ts_init=first_ns,
    )

    entry_side = OrderSide.BUY if intent.direction.lower() == "bullish" else OrderSide.SELL
    entry_side_name = "BUY" if intent.direction.lower() == "bullish" else "SELL"

    class OTRBracketShadowStrategy(Strategy):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        def on_start(self) -> None:
            self.subscribe_quote_ticks(instrument_id)

        def on_quote_tick(self, quote) -> None:
            if self.submitted or quote.instrument_id != instrument_id:
                return
            self.submitted = True
            bracket = self.order_factory.bracket(
                instrument_id=instrument_id,
                order_side=entry_side,
                quantity=Quantity.from_int(int(intent.quantity)),
                entry_order_type=OrderType.LIMIT,
                entry_price=Price.from_str(f"{intent.entry_price:.1f}"),
                tp_price=Price.from_str(f"{intent.target_price:.1f}"),
                sl_trigger_price=Price.from_str(f"{intent.stop_price:.1f}"),
            )
            self.submit_order_list(bracket)

    config = BacktestEngineConfig()
    engine = BacktestEngine(config=config)
    usd = Currency.from_str("USD")
    try:
        engine.add_venue(
            venue=Venue("OTR"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=usd,
            starting_balances=[Money(1_000_000, usd)],
            reject_stop_orders=False,
        )
        engine.add_instrument(instrument)
        engine.add_strategy(OTRBracketShadowStrategy())

        quotes = []
        previous_ns = 0
        for item in items:
            event_ns = int(_aware_utc(item.timestamp).timestamp() * 1_000_000_000)
            if event_ns <= previous_ns:
                event_ns = previous_ns + 1
            previous_ns = event_ns
            bid = float(item.bid) if item.bid is not None else float(item.price)
            ask = float(item.ask) if item.ask is not None else float(item.price)
            if bid > ask:
                bid, ask = ask, bid
            quotes.append(
                QuoteTick(
                    instrument_id=instrument.id,
                    bid_price=Price.from_str(f"{bid:.1f}"),
                    ask_price=Price.from_str(f"{ask:.1f}"),
                    bid_size=Quantity.from_int(max(1, int(intent.quantity))),
                    ask_size=Quantity.from_int(max(1, int(intent.quantity))),
                    ts_event=event_ns,
                    ts_init=event_ns,
                )
            )
        engine.add_data(quotes)
        engine.run()

        raw_orders = list(engine.cache.orders())
        order_states: list[NautilusOrderState] = []
        entry_order = None
        for order in raw_orders:
            role = _order_role(order, entry_side=entry_side_name)
            if role == "ENTRY":
                entry_order = order
            avg_fill = _float_value(_value_or_call(order, "avg_px"))
            if avg_fill is not None and isclose(avg_fill, 0.0, abs_tol=1e-12):
                avg_fill = None
            order_states.append(
                NautilusOrderState(
                    role=role,
                    side=_enum_name(_value_or_call(order, "side")),
                    order_type=_enum_name(_value_or_call(order, "order_type")),
                    status=_enum_name(_value_or_call(order, "status")),
                    price=_float_value(_value_or_call(order, "price")),
                    trigger_price=_float_value(_value_or_call(order, "trigger_price")),
                    avg_fill_price=avg_fill,
                    filled_quantity=float(_float_value(_value_or_call(order, "filled_qty")) or 0.0),
                )
            )

        entry_fill = None
        if entry_order is not None:
            entry_fill = _float_value(_value_or_call(entry_order, "avg_px"))
            if entry_fill is not None and isclose(entry_fill, 0.0, abs_tol=1e-12):
                entry_fill = None

        positions = list(engine.cache.positions())
        position = positions[-1] if positions else None
        is_closed = bool(_value_or_call(position, "is_closed", False)) if position is not None else False
        exit_fill = (
            _float_value(_value_or_call(position, "avg_px_close"))
            if position is not None and is_closed
            else None
        )

        if entry_fill is None:
            status = "PENDING"
        elif not is_closed:
            status = "OPEN"
        else:
            status = "CLOSED"

        result = None
        result_r = None
        result_dollars = None
        risk_distance = abs(float(intent.entry_price) - float(intent.stop_price))
        actual_risk_dollars = risk_distance * float(multiplier) * int(intent.quantity)

        if status == "CLOSED" and exit_fill is not None and entry_fill is not None and risk_distance > 0:
            signed_move = (
                float(exit_fill) - float(entry_fill)
                if intent.direction.lower() == "bullish"
                else float(entry_fill) - float(exit_fill)
            )
            result_r = signed_move / risk_distance
            result_dollars = signed_move * float(multiplier) * int(intent.quantity)
            result = "WIN" if result_dollars > 0 else "LOSS" if result_dollars < 0 else "FLAT"

        order_states.sort(key=lambda state: (state.role, state.order_type, state.side))
        return NautilusBracketResult(
            setup_id=intent.setup_id,
            contract=contract,
            contract_family=family,
            multiplier=multiplier,
            quantity=int(intent.quantity),
            status=status,
            entry_fill_price=entry_fill,
            exit_fill_price=exit_fill,
            result=result,
            result_r=result_r,
            result_dollars=result_dollars,
            actual_risk_dollars=actual_risk_dollars,
            orders=tuple(order_states),
            authoritative=False,
        )
    finally:
        engine.dispose()
