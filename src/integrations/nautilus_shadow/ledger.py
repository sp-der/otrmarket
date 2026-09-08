from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any

from src.research.execution.contracts import execution_contract

from .execution_parity import NautilusBracketResult, ShadowBracketIntent, simulate_gold_bracket
from .gold_replay import StoredGoldTick
from .parity import ExecutionSnapshot, ParityResult, compare_execution_snapshots


@dataclass(frozen=True)
class GoldTradeCandidate:
    setup_id: str
    timeframe: str
    direction: str
    created_at: datetime
    entry_price: float
    stop_price: float
    target_price: float
    risk_reward: float
    paper_status: str
    opened_at: datetime | None
    closed_at: datetime | None
    exit_price: float | None
    result: str | None
    result_r: float | None
    risk_dollars: float | None
    result_dollars: float | None

    def paper_snapshot(self) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            setup_id=self.setup_id,
            status=self.paper_status,
            entry_price=self.entry_price if self.opened_at is not None else None,
            exit_price=self.exit_price,
            result=self.result,
            result_r=self.result_r,
            result_dollars=self.result_dollars,
        )


@dataclass(frozen=True)
class ParityLedgerRecord:
    setup_id: str
    observed_at: str
    signal_contract: str
    execution_contract: str
    quantity: int
    paper_risk_dollars: float | None
    shadow_risk_dollars: float | None
    matched_trade_path: bool
    matched_full: bool
    difference_categories: tuple[str, ...]
    difference_details: tuple[str, ...]
    paper_snapshot: ExecutionSnapshot
    shadow_snapshot: ExecutionSnapshot
    shadow_orders_json: str
    note: str = ""


@dataclass(frozen=True)
class ParityRunError:
    setup_id: str
    error: str


@dataclass(frozen=True)
class ParityBatchReport:
    requested: int
    records: tuple[ParityLedgerRecord, ...]
    errors: tuple[ParityRunError, ...]

    @property
    def trade_path_matches(self) -> int:
        return sum(1 for item in self.records if item.matched_trade_path)

    @property
    def full_matches(self) -> int:
        return sum(1 for item in self.records if item.matched_full)


def _parse_time(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def ensure_parity_ledger(connection: sqlite3.Connection) -> None:
    """Create the additive shadow ledger without altering any OTR trade table."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS nautilus_shadow_parity (
            setup_id TEXT PRIMARY KEY,
            observed_at TEXT NOT NULL,
            signal_contract TEXT NOT NULL,
            execution_contract TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            paper_risk_dollars REAL,
            shadow_risk_dollars REAL,
            matched_trade_path INTEGER NOT NULL,
            matched_full INTEGER NOT NULL,
            difference_categories_json TEXT NOT NULL,
            difference_details_json TEXT NOT NULL,
            paper_snapshot_json TEXT NOT NULL,
            shadow_snapshot_json TEXT NOT NULL,
            shadow_orders_json TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_nautilus_shadow_parity_observed
        ON nautilus_shadow_parity(observed_at DESC);
        """
    )
    connection.commit()


def classify_parity_differences(result: ParityResult) -> tuple[str, ...]:
    categories: list[str] = []
    mapping = (
        ("setup_id:", "IDENTITY"),
        ("status:", "STATE"),
        ("entry_price:", "ENTRY"),
        ("exit_price:", "EXIT"),
        ("result:", "RESULT"),
        ("result_r:", "R_MULTIPLE"),
        ("result_dollars:", "PNL"),
    )
    for difference in result.differences:
        category = next((label for prefix, label in mapping if difference.startswith(prefix)), "OTHER")
        if category not in categories:
            categories.append(category)
    return tuple(categories)


def trade_path_match(categories: tuple[str, ...]) -> bool:
    """Ignore PNL-only variance caused by discrete MGC contract sizing."""
    return not any(category != "PNL" for category in categories)


def load_closed_gold_candidates(connection: sqlite3.Connection, *, limit: int = 20) -> list[GoldTradeCandidate]:
    bounded = max(1, min(int(limit), 500))
    rows = connection.execute(
        """
        SELECT
            p.setup_id,
            p.timeframe,
            p.direction,
            s.created_at,
            p.entry_price,
            p.stop_price,
            p.target_price,
            s.risk_reward,
            p.status,
            p.opened_at,
            p.closed_at,
            p.exit_price,
            p.result,
            p.result_r,
            p.risk_dollars,
            p.result_dollars
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.symbol = 'GC'
          AND p.status = 'CLOSED'
          AND p.opened_at IS NOT NULL
          AND p.closed_at IS NOT NULL
        ORDER BY COALESCE(p.closed_at, p.updated_at) DESC
        LIMIT ?
        """,
        (bounded,),
    ).fetchall()

    output: list[GoldTradeCandidate] = []
    for row in rows:
        output.append(
            GoldTradeCandidate(
                setup_id=str(row[0]),
                timeframe=str(row[1]),
                direction=str(row[2]),
                created_at=_parse_time(row[3]) or datetime.now(timezone.utc),
                entry_price=float(row[4]),
                stop_price=float(row[5]),
                target_price=float(row[6]),
                risk_reward=float(row[7]),
                paper_status=str(row[8]),
                opened_at=_parse_time(row[9]),
                closed_at=_parse_time(row[10]),
                exit_price=float(row[11]) if row[11] is not None else None,
                result=str(row[12]) if row[12] is not None else None,
                result_r=float(row[13]) if row[13] is not None else None,
                risk_dollars=float(row[14]) if row[14] is not None else None,
                result_dollars=float(row[15]) if row[15] is not None else None,
            )
        )
    return output


def _source_contract(source: str) -> str:
    value = str(source or "").strip()
    prefix = "ninjatrader:"
    return value[len(prefix):].strip() if value.lower().startswith(prefix) else ""


def resolve_signal_contract(connection: sqlite3.Connection, candidate: GoldTradeCandidate) -> str:
    """Find the NinjaTrader Gold contract feeding OTR when the setup was created.

    Timestamp filtering is done in Python rather than lexicographically in SQL so
    equivalent ISO timestamps using `Z`, offsets, or different fractional-second
    widths cannot select the wrong futures contract.
    """
    reference = candidate.created_at
    rows = connection.execute(
        """
        SELECT COALESCE(exchange_time, received_at), source
        FROM market_quotes
        WHERE symbol = 'GC' AND source LIKE 'ninjatrader:%'
        ORDER BY id DESC
        LIMIT 50000
        """
    ).fetchall()
    if not rows:
        return ""

    before: tuple[datetime, str] | None = None
    after: tuple[datetime, str] | None = None
    fallback = _source_contract(str(rows[0][1] or ""))
    for timestamp, source in rows:
        parsed = _parse_time(timestamp)
        contract = _source_contract(str(source or ""))
        if parsed is None or not contract:
            continue
        if parsed <= reference:
            if before is None or parsed > before[0]:
                before = (parsed, contract)
        else:
            if after is None or parsed < after[0]:
                after = (parsed, contract)

    if before is not None:
        return before[1]
    if after is not None:
        return after[1]
    return fallback


def load_candidate_ticks(
    connection: sqlite3.Connection,
    candidate: GoldTradeCandidate,
    *,
    signal_contract: str,
    post_seconds: int = 30,
    max_ticks: int = 50_000,
) -> list[StoredGoldTick]:
    """Load only quotes which existed after OTR created the setup.

    Starting at `created_at` is critical: allowing pre-setup quotes would let the
    Nautilus limit order fill before OTR had actually generated the trade, which
    creates a false execution mismatch.
    """
    start = candidate.created_at
    end_anchor = candidate.closed_at or candidate.opened_at or candidate.created_at
    end = end_anchor + timedelta(seconds=max(0, int(post_seconds)))
    source = f"ninjatrader:{signal_contract}" if signal_contract else None
    bounded = max(2, min(int(max_ticks), 50_000))

    if source:
        rows = connection.execute(
            """
            SELECT COALESCE(exchange_time, received_at), source, price, bid, ask
            FROM market_quotes
            WHERE symbol = 'GC' AND source = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (source, bounded),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT COALESCE(exchange_time, received_at), source, price, bid, ask
            FROM market_quotes
            WHERE symbol = 'GC' AND source LIKE 'ninjatrader:%'
            ORDER BY id DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()

    ticks: list[StoredGoldTick] = []
    for timestamp, row_source, price, bid, ask in reversed(rows):
        parsed = _parse_time(timestamp)
        if parsed is None or parsed < start or parsed > end:
            continue
        reference = price
        if reference is None:
            if bid is not None and ask is not None:
                reference = (float(bid) + float(ask)) / 2.0
            else:
                reference = bid if bid is not None else ask
        if reference is None:
            continue
        ticks.append(
            StoredGoldTick(
                timestamp=parsed,
                source=str(row_source or ""),
                price=float(reference),
                bid=float(bid) if bid is not None else None,
                ask=float(ask) if ask is not None else None,
            )
        )
    ticks.sort(key=lambda item: item.timestamp)
    return ticks


def shadow_quantity(candidate: GoldTradeCandidate) -> int:
    """Map paper risk to whole MGC contracts for a fair execution comparison."""
    stop_distance = abs(candidate.entry_price - candidate.stop_price)
    per_mgc_risk = stop_distance * 10.0
    if per_mgc_risk <= 0:
        raise ValueError("Gold candidate has zero stop distance")
    budget = float(candidate.risk_dollars or per_mgc_risk)
    return max(1, math.floor(budget / per_mgc_risk))


def persist_parity_record(connection: sqlite3.Connection, record: ParityLedgerRecord) -> None:
    ensure_parity_ledger(connection)
    connection.execute(
        """
        INSERT INTO nautilus_shadow_parity (
            setup_id, observed_at, signal_contract, execution_contract, quantity,
            paper_risk_dollars, shadow_risk_dollars, matched_trade_path, matched_full,
            difference_categories_json, difference_details_json,
            paper_snapshot_json, shadow_snapshot_json, shadow_orders_json, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(setup_id) DO UPDATE SET
            observed_at=excluded.observed_at,
            signal_contract=excluded.signal_contract,
            execution_contract=excluded.execution_contract,
            quantity=excluded.quantity,
            paper_risk_dollars=excluded.paper_risk_dollars,
            shadow_risk_dollars=excluded.shadow_risk_dollars,
            matched_trade_path=excluded.matched_trade_path,
            matched_full=excluded.matched_full,
            difference_categories_json=excluded.difference_categories_json,
            difference_details_json=excluded.difference_details_json,
            paper_snapshot_json=excluded.paper_snapshot_json,
            shadow_snapshot_json=excluded.shadow_snapshot_json,
            shadow_orders_json=excluded.shadow_orders_json,
            note=excluded.note
        """,
        (
            record.setup_id,
            record.observed_at,
            record.signal_contract,
            record.execution_contract,
            record.quantity,
            record.paper_risk_dollars,
            record.shadow_risk_dollars,
            int(record.matched_trade_path),
            int(record.matched_full),
            json.dumps(record.difference_categories),
            json.dumps(record.difference_details),
            json.dumps(asdict(record.paper_snapshot), sort_keys=True),
            json.dumps(asdict(record.shadow_snapshot), sort_keys=True),
            record.shadow_orders_json,
            record.note,
        ),
    )
    connection.commit()


def run_candidate_parity(
    connection: sqlite3.Connection,
    candidate: GoldTradeCandidate,
) -> ParityLedgerRecord:
    signal = resolve_signal_contract(connection, candidate)
    execution = execution_contract("GC", signal or None)
    ticks = load_candidate_ticks(connection, candidate, signal_contract=signal)
    if len(ticks) < 2:
        raise ValueError(
            f"Not enough retained post-setup Gold ticks for {candidate.setup_id}; "
            "the raw quote retention window may have rolled past this trade"
        )

    quantity = shadow_quantity(candidate)
    intent = ShadowBracketIntent(
        setup_id=candidate.setup_id,
        direction=candidate.direction,
        entry_price=candidate.entry_price,
        stop_price=candidate.stop_price,
        target_price=candidate.target_price,
        quantity=quantity,
        execution_contract=execution,
    )
    shadow: NautilusBracketResult = simulate_gold_bracket(ticks, intent)
    paper_snapshot = candidate.paper_snapshot()
    shadow_snapshot = shadow.to_snapshot()
    comparison = compare_execution_snapshots(paper_snapshot, shadow_snapshot)
    categories = classify_parity_differences(comparison)

    note = ""
    if categories == ("PNL",):
        note = "Trade path matches; dollar variance is from whole-MGC contract sizing versus paper risk budget."
    elif "PNL" in categories:
        note = "Dollar variance may include whole-MGC sizing in addition to the listed trade-path divergence."

    record = ParityLedgerRecord(
        setup_id=candidate.setup_id,
        observed_at=datetime.now(timezone.utc).isoformat(),
        signal_contract=signal,
        execution_contract=execution,
        quantity=quantity,
        paper_risk_dollars=candidate.risk_dollars,
        shadow_risk_dollars=shadow.actual_risk_dollars,
        matched_trade_path=trade_path_match(categories),
        matched_full=comparison.matched,
        difference_categories=categories,
        difference_details=tuple(comparison.differences),
        paper_snapshot=paper_snapshot,
        shadow_snapshot=shadow_snapshot,
        shadow_orders_json=json.dumps([asdict(item) for item in shadow.orders], sort_keys=True),
        note=note,
    )
    persist_parity_record(connection, record)
    return record


def run_recent_gold_parity(connection: sqlite3.Connection, *, limit: int = 10) -> ParityBatchReport:
    """Compare recent closed Gold trades without aborting on one stale tick window."""
    ensure_parity_ledger(connection)
    candidates = load_closed_gold_candidates(connection, limit=limit)
    records: list[ParityLedgerRecord] = []
    errors: list[ParityRunError] = []
    for candidate in candidates:
        try:
            records.append(run_candidate_parity(connection, candidate))
        except (RuntimeError, ValueError, sqlite3.Error) as exc:
            errors.append(ParityRunError(candidate.setup_id, str(exc)))
    return ParityBatchReport(
        requested=len(candidates),
        records=tuple(records),
        errors=tuple(errors),
    )
