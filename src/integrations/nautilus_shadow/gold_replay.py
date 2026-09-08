from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class StoredGoldTick:
    timestamp: datetime
    source: str
    price: float
    bid: float | None = None
    ask: float | None = None

    @property
    def contract(self) -> str:
        prefix = "ninjatrader:"
        value = str(self.source or "").strip()
        if value.lower().startswith(prefix):
            return value[len(prefix):].strip()
        return ""


@dataclass(frozen=True)
class GoldReplayReport:
    contract: str
    contract_family: str
    instrument_id: str
    input_ticks: int
    first_event_ns: int
    last_event_ns: int
    engine_ran: bool
    authoritative: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        return _aware_utc(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("Gold replay tick is missing a timestamp")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return _aware_utc(datetime.fromisoformat(text))


def contract_family(contract: str) -> str:
    token = str(contract or "").strip().upper().replace("/", " ")
    first = token.split()[0] if token else ""
    if first.startswith("MGC"):
        return "MGC"
    if first.startswith("GC"):
        return "GC"
    return "GC"


def contract_multiplier(contract: str) -> int:
    return 10 if contract_family(contract) == "MGC" else 100


def _safe_contract_token(contract: str) -> str:
    token = "".join(ch for ch in str(contract or "").upper() if ch.isalnum())
    if token:
        return token
    return "GCSHADOW"


def load_latest_gold_ticks(
    connection: sqlite3.Connection,
    *,
    limit: int = 5_000,
    contract: str | None = None,
) -> list[StoredGoldTick]:
    """Load one coherent GC/MGC replay stream from OTR's normalized quote ledger.

    Strategy recognition remains normalized as GC. Contract identity is recovered
    from the existing `source=ninjatrader:<contract>` value written by bridge ingress.
    When no contract is requested, the most recently observed NinjaTrader Gold
    contract is selected so replay resets/rolls cannot accidentally mix contracts.
    """
    bounded = max(2, min(int(limit), 50_000))
    selected_source: str | None = None

    if contract:
        selected_source = f"ninjatrader:{contract.strip()}"
    else:
        row = connection.execute(
            """
            SELECT source
            FROM market_quotes
            WHERE symbol = 'GC' AND source LIKE 'ninjatrader:%'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            selected_source = str(row[0] or "")

    if selected_source:
        rows = connection.execute(
            """
            SELECT COALESCE(exchange_time, received_at), source, price, bid, ask
            FROM market_quotes
            WHERE symbol = 'GC' AND source = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (selected_source, bounded),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT COALESCE(exchange_time, received_at), source, price, bid, ask
            FROM market_quotes
            WHERE symbol = 'GC'
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()

    ticks: list[StoredGoldTick] = []
    for timestamp, source, price, bid, ask in reversed(rows):
        if price is None and bid is None and ask is None:
            continue
        reference = price
        if reference is None:
            if bid is not None and ask is not None:
                reference = (float(bid) + float(ask)) / 2.0
            else:
                reference = bid if bid is not None else ask
        ticks.append(
            StoredGoldTick(
                timestamp=_parse_time(timestamp),
                source=str(source or ""),
                price=float(reference),
                bid=float(bid) if bid is not None else None,
                ask=float(ask) if ask is not None else None,
            )
        )

    ticks.sort(key=lambda item: item.timestamp)
    return ticks


def replay_gold_ticks(ticks: Iterable[StoredGoldTick]) -> GoldReplayReport:
    """Run stored Gold quotes through a real Nautilus BacktestEngine.

    No OTR strategy or broker command is emitted here. Phase 2 proves that the
    exact replay clock/data stream can be represented by Nautilus independently
    before order/fill parity is introduced.
    """
    items = sorted(list(ticks), key=lambda item: item.timestamp)
    if len(items) < 2:
        raise ValueError("Nautilus Gold replay requires at least two ticks")

    try:
        from nautilus_trader.backtest.config import BacktestEngineConfig
        from nautilus_trader.backtest.engine import BacktestEngine
        from nautilus_trader.model import AssetClass
        from nautilus_trader.model import Currency
        from nautilus_trader.model import FuturesContract
        from nautilus_trader.model import InstrumentId
        from nautilus_trader.model import Price
        from nautilus_trader.model import Quantity
        from nautilus_trader.model import QuoteTick
        from nautilus_trader.model import Symbol
        from nautilus_trader.model.enums import AccountType, OmsType
        from nautilus_trader.model.identifiers import TraderId, Venue
        from nautilus_trader.model.objects import Money
    except ImportError as exc:
        raise RuntimeError(
            "NautilusTrader is not installed. Install requirements-nautilus.txt for shadow replay."
        ) from exc

    raw_contract = next((item.contract for item in reversed(items) if item.contract), "GC SHADOW")
    family = contract_family(raw_contract)
    token = _safe_contract_token(raw_contract)
    instrument_id = InstrumentId.from_str(f"{token}.OTR")

    first_ns = int(_aware_utc(items[0].timestamp).timestamp() * 1_000_000_000)
    last_ns = int(_aware_utc(items[-1].timestamp).timestamp() * 1_000_000_000)
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
        multiplier=Quantity.from_int(contract_multiplier(raw_contract)),
        lot_size=Quantity.from_int(1),
        ts_event=first_ns,
        ts_init=first_ns,
    )

    config = BacktestEngineConfig(
        trader_id=TraderId("OTR-SHADOW-001"),
        bypass_logging=True,
        run_analysis=False,
    )
    engine = BacktestEngine(config=config)
    venue = Venue("OTR")
    usd = Currency.from_str("USD")

    try:
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=usd,
            starting_balances=[Money(1_000_000, usd)],
        )
        engine.add_instrument(instrument)

        quotes = []
        previous_ns = 0
        for item in items:
            event_ns = int(_aware_utc(item.timestamp).timestamp() * 1_000_000_000)
            # Keep deterministic ordering even when NinjaTrader emits multiple
            # updates with the exact same timestamp.
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
                    bid_size=Quantity.from_int(1),
                    ask_size=Quantity.from_int(1),
                    ts_event=event_ns,
                    ts_init=event_ns,
                )
            )

        engine.add_data(quotes)
        engine.run()
    finally:
        engine.dispose()

    return GoldReplayReport(
        contract=raw_contract,
        contract_family=family,
        instrument_id=str(instrument_id),
        input_ticks=len(items),
        first_event_ns=first_ns,
        last_event_ns=last_ns,
        engine_ran=True,
        authoritative=False,
    )


def replay_latest_stored_gold(
    connection: sqlite3.Connection,
    *,
    limit: int = 5_000,
    contract: str | None = None,
) -> GoldReplayReport:
    ticks = load_latest_gold_ticks(connection, limit=limit, contract=contract)
    return replay_gold_ticks(ticks)
