from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import statistics

from src.research.counterfactual import HOLDOUT_START, evaluate_shadow, load_causal_bars
from src.research.pending_lifecycle import load_order_lifecycles
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry


STRATEGY_MIN_RR = {
    "ICT_CONFLUENCE": 1.0,
    "MSS_REVERSAL": 1.25,
    "TREND_CONTINUATION_REARM": 1.50,
    "REJECTION_BLOCK_10_10": 3.0,
}


def _direction(value: str | None) -> str:
    text = str(value or "").lower()
    if text in {"bullish", "long", "buy"}:
        return "bullish"
    if text in {"bearish", "short", "sell"}:
        return "bearish"
    return text


def _finite(value) -> bool:
    try:
        return value is not None and float(value) == float(value)
    except (TypeError, ValueError):
        return False


def _setup_decisions(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute(
        "SELECT sequence_no,payload_json FROM decision_traces "
        "WHERE event_type='SETUP_DECISION' ORDER BY sequence_no"
    ):
        payload = json.loads(row["payload_json"])
        setup_id = payload.get("setup_id")
        if not setup_id:
            continue
        payload["_sequence_no"] = int(row["sequence_no"])
        grouped[str(setup_id)].append(payload)
    return dict(grouped)


def load_registered_setups(run_database: str | Path) -> tuple[dict, list[dict]]:
    path = Path(run_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run_row = connection.execute("SELECT * FROM backtest_runs LIMIT 1").fetchone()
        if run_row is None:
            raise ValueError(f"missing backtest_runs row: {path.name}")
        run = dict(run_row)
        if run.get("status") != "COMPLETE":
            raise ValueError(f"run is not COMPLETE: {path.name}")
        if str(run.get("end_time") or "") > HOLDOUT_START:
            raise ValueError(f"run crosses final holdout firewall: {path.name}")

        decisions = _setup_decisions(connection)
        first_order_sequence: dict[str, int] = {}
        for row in connection.execute(
            "SELECT sequence_no,payload_json FROM decision_traces "
            "WHERE event_type='ORDER_STATE' ORDER BY sequence_no"
        ):
            payload = json.loads(row["payload_json"])
            setup_id = payload.get("setup_id")
            if setup_id:
                first_order_sequence.setdefault(str(setup_id), int(row["sequence_no"]))
    finally:
        connection.close()

    _, lifecycle_rows = load_order_lifecycles(path)
    lifecycle = {str(row["setup_id"]): row for row in lifecycle_rows}
    output = []
    for setup_id, sequence_no in sorted(first_order_sequence.items(), key=lambda pair: pair[1]):
        eligible = [
            item for item in decisions.get(setup_id, [])
            if int(item.get("_sequence_no", -1)) <= sequence_no
        ]
        if not eligible:
            continue
        trace = dict(eligible[-1])
        trace["lifecycle"] = lifecycle.get(setup_id, {})
        output.append(trace)
    return run, output


def _value(mapping: dict | None, *names):
    mapping = mapping or {}
    for name in names:
        if name in mapping and _finite(mapping.get(name)):
            return float(mapping[name])
    return None


def candidate_entries(trace: dict) -> list[tuple[str, float]]:
    original = trace.get("planned_entry")
    fvg = trace.get("fvg") if isinstance(trace.get("fvg"), dict) else {}
    displacement = trace.get("displacement") if isinstance(trace.get("displacement"), dict) else {}
    direction = _direction(trace.get("direction"))
    candidates: list[tuple[str, float]] = []
    if _finite(original):
        candidates.append(("ORIGINAL", float(original)))

    lower = _value(fvg, "lower", "low")
    upper = _value(fvg, "upper", "high")
    if lower is not None and upper is not None and upper > lower:
        width = upper - lower
        shallow = upper - 0.25 * width if direction == "bullish" else lower + 0.25 * width
        candidates.extend([
            ("FVG_SHALLOW_25", shallow),
            ("FVG_MIDPOINT", (lower + upper) / 2.0),
        ])

    low = _value(displacement, "low")
    high = _value(displacement, "high")
    if low is not None and high is not None and high > low:
        move = high - low
        for label, retracement in (
            ("OTE_50", 0.50),
            ("OTE_62", 0.62),
            ("OTE_70_5", 0.705),
            ("OTE_79", 0.79),
        ):
            price = high - retracement * move if direction == "bullish" else low + retracement * move
            candidates.append((label, price))
    return candidates


def _decision_close(trace: dict, bars: list[dict]) -> float | None:
    event_time = trace.get("event_time")
    if not event_time or not bars:
        return None
    close_times = [str(row["close_time"]) for row in bars]
    index = bisect_right(close_times, str(event_time)) - 1
    if index < 0:
        return None
    return float(bars[index]["close"])


def evaluate_entry_variants(trace: dict, bars: list[dict], run_end: str) -> list[dict]:
    direction = _direction(trace.get("direction"))
    symbol = str(trace.get("symbol") or "")
    strategy = str(trace.get("strategy_type") or "ICT_CONFLUENCE")
    stop = trace.get("stop")
    target = trace.get("target")
    if direction not in {"bullish", "bearish"} or symbol not in {"NQ", "ES", "GC"}:
        return []
    if not all(_finite(value) for value in (stop, target)):
        return []

    decision_close = _decision_close(trace, bars)
    if decision_close is None:
        return []
    floor = STRATEGY_MIN_RR.get(strategy, 1.0)
    original = float(trace.get("planned_entry")) if _finite(trace.get("planned_entry")) else None
    lifecycle = trace.get("lifecycle") or {}
    seen_prices: set[float] = set()
    output = []

    for variant, raw_entry in candidate_entries(trace):
        entry, norm_stop, norm_target = normalize_trade_prices(
            symbol, direction, float(raw_entry), float(stop), float(target)
        )
        if entry in seen_prices:
            continue
        seen_prices.add(entry)
        geometry = validate_trade_geometry(symbol, direction, entry, norm_stop, norm_target)
        marketable_chase = (
            entry > decision_close if direction == "bullish" else entry < decision_close
        )
        eligible = bool(geometry.valid and float(geometry.risk_reward or 0.0) >= floor and not marketable_chase)
        base = {
            "setup_id": trace.get("setup_id"),
            "event_time": trace.get("event_time"),
            "symbol": symbol,
            "timeframe": trace.get("timeframe"),
            "strategy": strategy,
            "direction": direction,
            "grade": trace.get("setup_grade"),
            "quality_score": trace.get("quality_score"),
            "original_entry_type": (trace.get("metadata") or {}).get("entry_type"),
            "variant": variant,
            "entry": entry,
            "stop": norm_stop,
            "target": norm_target,
            "decision_close": decision_close,
            "risk_reward": float(geometry.risk_reward or 0.0) if geometry.valid else None,
            "minimum_rr": floor,
            "marketable_chase": marketable_chase,
            "eligible": eligible,
            "original_cancel_reason": lifecycle.get("cancellation_reason"),
            "original_filled": bool(lifecycle.get("filled")),
            "original_entry": original,
            "shallower_than_original": (
                None if original is None else
                entry > original if direction == "bullish" else entry < original
            ),
        }
        if not eligible:
            output.append({**base, "outcome": "INELIGIBLE", "resolved": False, "r_outcome": None})
            continue

        synthetic = dict(trace)
        synthetic.update({
            "planned_entry": entry,
            "stop": norm_stop,
            "target": norm_target,
            "direction": direction,
            "strategy_type": strategy,
        })
        result = evaluate_shadow(synthetic, bars, run_end)
        output.append({**base, **{
            key: result.get(key)
            for key in (
                "outcome", "resolved", "r_outcome", "entry_time", "terminal_time",
                "planned_r", "mfe_r", "mae_r", "ambiguous_bar"
            )
        }})
    return output


def _summary(rows: list[dict]) -> dict:
    eligible = [row for row in rows if row.get("eligible")]
    filled = [row for row in eligible if row.get("entry_time")]
    resolved = [row for row in eligible if row.get("resolved")]
    wins = [row for row in resolved if row.get("outcome") == "WIN"]
    losses = [row for row in resolved if row.get("outcome") != "WIN"]
    stale_source = [
        row for row in eligible
        if row.get("original_cancel_reason") in {"STALE_AT_REGISTRATION", "TARGET_PROGRESS_75"}
    ]
    rescued = [row for row in stale_source if row.get("entry_time")]
    rescued_resolved = [row for row in rescued if row.get("resolved")]
    rescued_wins = [row for row in rescued_resolved if row.get("outcome") == "WIN"]
    outcomes: dict[str, int] = defaultdict(int)
    for row in eligible:
        outcomes[str(row.get("outcome") or "UNKNOWN")] += 1
    return {
        "rows": len(rows),
        "eligible": len(eligible),
        "filled": len(filled),
        "fill_pct": (len(filled) / len(eligible) * 100.0) if eligible else None,
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "resolved_win_rate": (len(wins) / len(resolved) * 100.0) if resolved else None,
        "diagnostic_net_r": sum(float(row.get("r_outcome") or 0.0) for row in resolved),
        "median_planned_r": statistics.median([
            float(row["planned_r"]) for row in eligible if _finite(row.get("planned_r"))
        ]) if any(_finite(row.get("planned_r")) for row in eligible) else None,
        "stale_source_setups": len(stale_source),
        "stale_source_rescued_to_entry": len(rescued),
        "stale_source_rescue_pct": (len(rescued) / len(stale_source) * 100.0) if stale_source else None,
        "stale_source_resolved": len(rescued_resolved),
        "stale_source_wins": len(rescued_wins),
        "stale_source_resolved_win_rate": (
            len(rescued_wins) / len(rescued_resolved) * 100.0
            if rescued_resolved else None
        ),
        "outcomes": dict(sorted(outcomes.items())),
    }


def _segment(rows: list[dict], key: str) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "UNKNOWN")].append(row)
    return {name: _summary(values) for name, values in sorted(grouped.items())}


def summarize_entry_depth(rows: list[dict]) -> dict:
    return {
        **_summary(rows),
        "by_variant": _segment(rows, "variant"),
        "by_strategy": _segment(rows, "strategy"),
        "by_timeframe": _segment(rows, "timeframe"),
        "by_symbol": _segment(rows, "symbol"),
        "by_original_entry_type": _segment(rows, "original_entry_type"),
    }


def audit_run(run_database: str | Path, historical_database: str | Path) -> dict:
    run, traces = load_registered_setups(run_database)
    bars_by_symbol = load_causal_bars(historical_database, run["start_time"], run["end_time"])
    rows = []
    for trace in traces:
        rows.extend(evaluate_entry_variants(trace, bars_by_symbol.get(str(trace.get("symbol")), []), run["end_time"]))
    return {
        "run_id": run["run_id"],
        "start_time": run["start_time"],
        "end_time": run["end_time"],
        "registered_setups": len(traces),
        "rows": rows,
        "summary": summarize_entry_depth(rows),
    }
