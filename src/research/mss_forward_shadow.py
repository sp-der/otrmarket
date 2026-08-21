from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from src.execution.paper import PaperExecutor, PaperPosition
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.storage.intelligence import ensure_intelligence_schema, upsert_shadow_trade


BASELINE_PROFILE = "MSS_FORWARD_BASELINE_V1"
CANDIDATE_PROFILE = "MSS_FORWARD_FVG_SHALLOW25_V1"
CANDIDATE_VARIANT = "FVG_SHALLOW_25"
NY = ZoneInfo("America/New_York")


def _aware(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ensure_forward_shadow_schema(connection: sqlite3.Connection) -> None:
    ensure_intelligence_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS mss_forward_shadow_pairs (
            source_setup_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            baseline_setup_id TEXT NOT NULL,
            candidate_setup_id TEXT NOT NULL,
            candidate_variant TEXT NOT NULL,
            original_entry REAL NOT NULL,
            candidate_entry REAL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            source_risk_dollars REAL,
            quote_price REAL,
            quote_bid REAL,
            quote_ask REAL,
            candidate_eligible INTEGER NOT NULL DEFAULT 0,
            candidate_reject_reason TEXT,
            candidate_risk_reward REAL,
            build_profile TEXT NOT NULL DEFAULT 'MSS_FORWARD_SHADOW_V1'
        );
        CREATE INDEX IF NOT EXISTS idx_mss_forward_shadow_created
        ON mss_forward_shadow_pairs(created_at);
        """
    )
    connection.commit()


def fvg_shallow_25_entry(setup) -> float | None:
    fvg = getattr(setup, "entry_fvg", None)
    if fvg is None:
        return None
    try:
        lower = float(getattr(fvg, "lower"))
        upper = float(getattr(fvg, "upper"))
    except (TypeError, ValueError, AttributeError):
        return None
    if upper <= lower:
        return None
    width = upper - lower
    if setup.direction == "bullish":
        return upper - 0.25 * width
    if setup.direction == "bearish":
        return lower + 0.25 * width
    return None


def minimum_mss_rr(timeframe: str) -> float:
    # Operation 6.1+ permits selected 1m MSS reversal candidates at 1.25R.
    return 1.25 if str(timeframe) == "1m" else 1.50


def marketable_chase(direction: str, entry: float, *, price=None, bid=None, ask=None) -> bool | None:
    if direction == "bullish":
        reference = ask if ask is not None else price
        return None if reference is None else float(entry) >= float(reference)
    if direction == "bearish":
        reference = bid if bid is not None else price
        return None if reference is None else float(entry) <= float(reference)
    return None


def _clone_setup(setup, setup_id: str, profile: str, entry_type: str):
    clone = deepcopy(setup)
    clone.setup_id = setup_id
    clone.status = "PENDING"
    clone.metadata = deepcopy(getattr(setup, "metadata", {}) or {})
    clone.metadata["shadow_profile"] = profile
    clone.metadata["shadow_source_setup_id"] = setup.setup_id
    clone.metadata["forward_shadow"] = True
    clone.metadata["forward_shadow_candidate"] = entry_type
    clone.metadata["entry_type"] = entry_type
    clone.metadata["production_behavior_unchanged"] = True
    return clone


def _rejected_position(setup, risk_dollars, event_time, price, reason: str) -> PaperPosition:
    position = PaperPosition(
        setup=setup,
        status="INVALIDATED",
        closed_at=_aware(event_time),
        exit_price=float(price) if price is not None else None,
        result=reason,
        result_r=None,
        risk_dollars=risk_dollars,
        result_dollars=0.0 if risk_dollars is not None else None,
        guard_reason="Research-only forward shadow candidate was not eligible at decision time.",
    )
    return position


class MSSForwardShadowHarness:
    """Forward-only A/B observer for accepted MSS reversal setups.

    BASELINE mirrors the accepted Operation 7.0 entry. CANDIDATE preserves the
    same stop/target and substitutes a pre-registered 25% shallow FVG limit.
    Both books are research-only and never feed results back into the live
    evaluation, recovery, cooldown, or position ledgers.
    """

    def __init__(self) -> None:
        self.baseline = PaperExecutor()
        self.candidate = PaperExecutor()
        self.quotes: dict[str, dict] = {}

    def prepare_session(self, connection: sqlite3.Connection, event_time=None) -> int:
        """Close orphaned active shadow rows after a process restart.

        We do not pretend an in-flight shadow order survived a process restart.
        Closed historical shadow outcomes remain untouched.
        """
        ensure_forward_shadow_schema(connection)
        stamp = _aware(event_time or datetime.now(timezone.utc)).isoformat()
        cursor = connection.execute(
            """
            UPDATE shadow_trades
            SET status='INVALIDATED', result='INTERRUPTED_RESTART', result_r=NULL,
                result_dollars=0, closed_at=COALESCE(closed_at, ?), updated_at=?
            WHERE profile IN (?, ?) AND status IN ('PENDING','OPEN')
            """,
            (stamp, stamp, BASELINE_PROFILE, CANDIDATE_PROFILE),
        )
        connection.commit()
        return int(cursor.rowcount or 0)

    def on_price(self, connection, symbol, price, bid, ask, event_time) -> None:
        event_time = _aware(event_time)
        self.quotes[str(symbol)] = {
            "price": float(price) if price is not None else None,
            "bid": float(bid) if bid is not None else None,
            "ask": float(ask) if ask is not None else None,
            "event_time": event_time,
        }
        for profile, book in ((BASELINE_PROFILE, self.baseline), (CANDIDATE_PROFILE, self.candidate)):
            for position in book.on_price(symbol, float(price), event_time):
                upsert_shadow_trade(
                    connection,
                    position,
                    event_time.isoformat(),
                    profile=profile,
                    source_setup_id=position.setup.metadata.get("shadow_source_setup_id"),
                )

    def _already_registered(self, connection, source_setup_id: str) -> bool:
        ensure_forward_shadow_schema(connection)
        row = connection.execute(
            "SELECT 1 FROM mss_forward_shadow_pairs WHERE source_setup_id=? LIMIT 1",
            (source_setup_id,),
        ).fetchone()
        return row is not None

    def register_live_position(self, connection, position, updated_at) -> bool:
        setup = getattr(position, "setup", None)
        if setup is None:
            return False
        strategy = str((getattr(setup, "metadata", {}) or {}).get("strategy", ""))
        if strategy != "MSS_REVERSAL" or str(getattr(position, "status", "")).upper() != "PENDING":
            return False
        source_id = str(setup.setup_id)
        if self._already_registered(connection, source_id):
            return False

        event_time = _aware(updated_at or setup.created_at)
        quote = self.quotes.get(str(setup.symbol), {})
        price = quote.get("price")
        bid = quote.get("bid")
        ask = quote.get("ask")
        risk_dollars = getattr(position, "risk_dollars", None)

        baseline_id = f"mssfb1_{source_id}"
        candidate_id = f"mssfc1_{source_id}"
        baseline_setup = _clone_setup(
            setup, baseline_id, BASELINE_PROFILE,
            str((setup.metadata or {}).get("entry_type") or "BASELINE_CURRENT_ENTRY"),
        )
        baseline_position = self.baseline.register_setup(
            baseline_setup,
            risk_dollars=risk_dollars,
            guard_reason="Forward shadow baseline mirrors the accepted Operation 7.0 MSS entry.",
        )
        upsert_shadow_trade(
            connection,
            baseline_position,
            event_time.isoformat(),
            profile=BASELINE_PROFILE,
            source_setup_id=source_id,
        )

        candidate_raw = fvg_shallow_25_entry(setup)
        candidate_setup = _clone_setup(setup, candidate_id, CANDIDATE_PROFILE, CANDIDATE_VARIANT)
        eligible = False
        reject_reason = None
        candidate_entry = None
        candidate_rr = None

        if candidate_raw is None:
            reject_reason = "NO_VALID_ENTRY_FVG"
        else:
            candidate_entry, candidate_stop, candidate_target = normalize_trade_prices(
                setup.symbol,
                setup.direction,
                float(candidate_raw),
                float(setup.stop_price),
                float(setup.target_price),
            )
            candidate_setup.entry_price = float(candidate_entry)
            candidate_setup.stop_price = float(candidate_stop)
            candidate_setup.target_price = float(candidate_target)
            geometry = validate_trade_geometry(
                setup.symbol,
                setup.direction,
                candidate_setup.entry_price,
                candidate_setup.stop_price,
                candidate_setup.target_price,
            )
            if not geometry.valid:
                reject_reason = "INVALID_GEOMETRY"
            else:
                candidate_rr = float(geometry.risk_reward or 0.0)
                candidate_setup.risk_reward = candidate_rr
                if candidate_rr < minimum_mss_rr(setup.timeframe):
                    reject_reason = "RR_BELOW_CURRENT_MSS_FLOOR"
                else:
                    chase = marketable_chase(
                        setup.direction,
                        candidate_setup.entry_price,
                        price=price,
                        bid=bid,
                        ask=ask,
                    )
                    if chase is None:
                        reject_reason = "NO_LIVE_QUOTE_AT_REGISTRATION"
                    elif chase:
                        reject_reason = "CHASE_REJECTED"
                    elif candidate_setup.entry_price == float(setup.entry_price):
                        reject_reason = "SAME_AS_BASELINE"
                    else:
                        eligible = True

        if eligible:
            candidate_position = self.candidate.register_setup(
                candidate_setup,
                risk_dollars=risk_dollars,
                guard_reason=(
                    "Forward shadow candidate uses FVG shallow-25 entry only; "
                    "all live Operation 7.0 behavior remains unchanged."
                ),
            )
        else:
            candidate_position = _rejected_position(
                candidate_setup,
                risk_dollars,
                event_time,
                price,
                reject_reason or "INELIGIBLE",
            )

        upsert_shadow_trade(
            connection,
            candidate_position,
            event_time.isoformat(),
            profile=CANDIDATE_PROFILE,
            source_setup_id=source_id,
        )

        ensure_forward_shadow_schema(connection)
        connection.execute(
            """
            INSERT INTO mss_forward_shadow_pairs (
                source_setup_id, created_at, symbol, timeframe, direction,
                baseline_setup_id, candidate_setup_id, candidate_variant,
                original_entry, candidate_entry, stop_price, target_price,
                source_risk_dollars, quote_price, quote_bid, quote_ask,
                candidate_eligible, candidate_reject_reason, candidate_risk_reward
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                event_time.isoformat(),
                setup.symbol,
                setup.timeframe,
                setup.direction,
                baseline_id,
                candidate_id,
                CANDIDATE_VARIANT,
                float(setup.entry_price),
                float(candidate_entry) if candidate_entry is not None else None,
                float(setup.stop_price),
                float(setup.target_price),
                float(risk_dollars) if risk_dollars is not None else None,
                float(price) if price is not None else None,
                float(bid) if bid is not None else None,
                float(ask) if ask is not None else None,
                1 if eligible else 0,
                reject_reason,
                candidate_rr,
            ),
        )
        connection.commit()
        return True


def _book_metrics(rows: list[sqlite3.Row]) -> dict:
    registered = len(rows)
    filled = [row for row in rows if row["opened_at"]]
    terminal = [row for row in rows if str(row["result"] or "").upper() in {"WIN", "LOSS"}]
    wins = [row for row in terminal if str(row["result"] or "").upper() == "WIN"]
    losses = [row for row in terminal if str(row["result"] or "").upper() == "LOSS"]
    total_r = sum(float(row["result_r"] or 0.0) for row in terminal)
    total_dollars = sum(float(row["result_dollars"] or 0.0) for row in terminal)
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for row in sorted(terminal, key=lambda item: str(item["closed_at"] or "")):
        equity += float(row["result_r"] or 0.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "registered": registered,
        "filled": len(filled),
        "fill_pct": (len(filled) / registered * 100.0) if registered else None,
        "closed": len(terminal),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(terminal) * 100.0) if terminal else None,
        "net_r": total_r,
        "expectancy_r": (total_r / len(terminal)) if terminal else None,
        "simulated_dollars": total_dollars,
        "max_drawdown_r": max_dd,
        "instant_stops": sum(str(row["outcome_class"] or "") == "INSTANT_STOP" for row in terminal),
        "early_stops": sum(str(row["outcome_class"] or "") == "EARLY_STOP" for row in terminal),
        "interrupted": sum(str(row["result"] or "") == "INTERRUPTED_RESTART" for row in rows),
    }


def forward_shadow_snapshot(db_path: Path | str) -> dict:
    path = Path(db_path)
    if not path.exists():
        return {
            "profile": "MSS_FORWARD_SHADOW_V1",
            "status": "WAITING_FOR_DATA",
            "matched_setups": 0,
            "baseline": _book_metrics([]),
            "candidate": _book_metrics([]),
        }
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mss_forward_shadow_pairs'"
        ).fetchone()
        if table is None:
            return {
                "profile": "MSS_FORWARD_SHADOW_V1",
                "status": "WAITING_FOR_DATA",
                "matched_setups": 0,
                "baseline": _book_metrics([]),
                "candidate": _book_metrics([]),
            }
        pairs = connection.execute(
            "SELECT * FROM mss_forward_shadow_pairs ORDER BY created_at ASC"
        ).fetchall()
        baseline = connection.execute(
            "SELECT * FROM shadow_trades WHERE profile=? ORDER BY updated_at ASC",
            (BASELINE_PROFILE,),
        ).fetchall()
        candidate = connection.execute(
            "SELECT * FROM shadow_trades WHERE profile=? ORDER BY updated_at ASC",
            (CANDIDATE_PROFILE,),
        ).fetchall()
        baseline_metrics = _book_metrics(baseline)
        candidate_metrics = _book_metrics(candidate)
        trading_days = set()
        for pair in pairs:
            try:
                trading_days.add(_aware(pair["created_at"]).astimezone(NY).date().isoformat())
            except Exception:
                pass
        candidate_only_fills = 0
        baseline_by_source = {str(row["source_setup_id"]): row for row in baseline}
        candidate_by_source = {str(row["source_setup_id"]): row for row in candidate}
        for pair in pairs:
            source = str(pair["source_setup_id"])
            base_row = baseline_by_source.get(source)
            cand_row = candidate_by_source.get(source)
            if cand_row is not None and cand_row["opened_at"] and (base_row is None or not base_row["opened_at"]):
                candidate_only_fills += 1
        eligible = sum(bool(pair["candidate_eligible"]) for pair in pairs)
        first = pairs[0]["created_at"] if pairs else None
        last = pairs[-1]["created_at"] if pairs else None
        ready_for_review = len(pairs) >= 20 and len(trading_days) >= 5
        return {
            "profile": "MSS_FORWARD_SHADOW_V1",
            "status": "REVIEW_READY" if ready_for_review else "COLLECTING_FRESH_DATA",
            "candidate_variant": CANDIDATE_VARIANT,
            "matched_setups": len(pairs),
            "candidate_eligible": eligible,
            "candidate_rejected": len(pairs) - eligible,
            "candidate_only_fills": candidate_only_fills,
            "trading_days": len(trading_days),
            "first_setup_at": first,
            "last_setup_at": last,
            "review_gate": {"minimum_matched_setups": 20, "minimum_trading_days": 5},
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
            "comparison": {
                "net_r_delta": candidate_metrics["net_r"] - baseline_metrics["net_r"],
                "expectancy_r_delta": (
                    None if candidate_metrics["expectancy_r"] is None or baseline_metrics["expectancy_r"] is None
                    else candidate_metrics["expectancy_r"] - baseline_metrics["expectancy_r"]
                ),
                "fill_pct_delta": (
                    None if candidate_metrics["fill_pct"] is None or baseline_metrics["fill_pct"] is None
                    else candidate_metrics["fill_pct"] - baseline_metrics["fill_pct"]
                ),
                "max_drawdown_r_delta": candidate_metrics["max_drawdown_r"] - baseline_metrics["max_drawdown_r"],
            },
            "production_behavior_changed": False,
        }
    finally:
        connection.close()
