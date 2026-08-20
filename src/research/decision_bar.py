from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import statistics
from pathlib import Path

from src.research.counterfactual import load_causal_bars
from src.research.missed_move import audit_run as audit_missed_run


def _touches(bar: dict, price: float) -> bool:
    return float(bar["low"]) <= float(price) <= float(bar["high"])


def _segment(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    return {name: summarize(values) for name, values in sorted(groups.items())}


def summarize(rows: list[dict]) -> dict:
    exact = [row for row in rows if row.get("decision_bar_found")]
    entry_touch = [row for row in exact if row.get("entry_touch")]
    safe = [row for row in exact if row.get("sequence_safe_entry_touch")]
    ambiguous = [row for row in exact if row.get("ambiguous_entry_touch")]
    no_touch = [row for row in exact if not row.get("entry_touch")]
    progress = [float(row["decision_progress_r"]) for row in exact if row.get("decision_progress_r") is not None]
    return {
        "setups": len(rows),
        "decision_bar_found": len(exact),
        "entry_touch_same_bar": len(entry_touch),
        "entry_touch_same_bar_pct": (len(entry_touch) / len(exact) * 100.0) if exact else None,
        "sequence_safe_entry_touch": len(safe),
        "sequence_safe_entry_touch_pct": (len(safe) / len(exact) * 100.0) if exact else None,
        "ambiguous_entry_touch": len(ambiguous),
        "ambiguous_entry_touch_pct": (len(ambiguous) / len(exact) * 100.0) if exact else None,
        "no_entry_touch_same_bar": len(no_touch),
        "no_entry_touch_same_bar_pct": (len(no_touch) / len(exact) * 100.0) if exact else None,
        "decision_bar_target_touch": sum(bool(row.get("target_touch")) for row in exact),
        "decision_bar_stop_touch": sum(bool(row.get("stop_touch")) for row in exact),
        "median_decision_progress_r": statistics.median(progress) if progress else None,
    }


def enrich_decision_bar(rows: list[dict], bars_by_symbol: dict[str, list[dict]]) -> list[dict]:
    times = {symbol: [bar["close_time"] for bar in bars] for symbol, bars in bars_by_symbol.items()}
    output = []
    for source in rows:
        row = dict(source)
        symbol = row.get("symbol")
        bars = bars_by_symbol.get(symbol, [])
        close_times = times.get(symbol, [])
        event_time = row.get("event_time") or ""
        index = bisect_right(close_times, event_time) - 1
        bar = bars[index] if index >= 0 and close_times[index] == event_time else None
        if bar is None:
            row.update({
                "decision_bar_found": False,
                "entry_touch": None,
                "stop_touch": None,
                "target_touch": None,
                "sequence_safe_entry_touch": False,
                "ambiguous_entry_touch": False,
            })
            output.append(row)
            continue

        entry_touch = _touches(bar, float(row["entry"]))
        stop_touch = _touches(bar, float(row["stop"]))
        target_touch = _touches(bar, float(row["target"]))
        row.update({
            "decision_bar_found": True,
            "decision_bar_open": float(bar["open"]),
            "decision_bar_high": float(bar["high"]),
            "decision_bar_low": float(bar["low"]),
            "decision_bar_close": float(bar["close"]),
            "entry_touch": entry_touch,
            "stop_touch": stop_touch,
            "target_touch": target_touch,
            # With only OHLC we cannot order multiple touches. This bucket is
            # deliberately conservative: entry touched, while neither terminal
            # level touched in the same bar.
            "sequence_safe_entry_touch": bool(entry_touch and not stop_touch and not target_touch),
            "ambiguous_entry_touch": bool(entry_touch and (stop_touch or target_touch)),
        })
        output.append(row)
    return output


def audit_run(run_database: str | Path, historical_database: str | Path) -> dict:
    missed = audit_missed_run(run_database, historical_database)
    bars = load_causal_bars(historical_database, missed["start_time"], missed["end_time"])
    rows = enrich_decision_bar(missed["missed_moves"], bars)
    return {
        "run_id": missed["run_id"],
        "start_time": missed["start_time"],
        "end_time": missed["end_time"],
        "rows": rows,
        "summary": summarize(rows),
    }


def summarize_study(runs: list[dict]) -> dict:
    rows = [row for run in runs for row in run["rows"]]
    summary = summarize(rows)
    summary.update({
        "by_gate": _segment(rows, "gate"),
        "by_symbol": _segment(rows, "symbol"),
        "by_timeframe": _segment(rows, "timeframe"),
        "by_grade": _segment(rows, "grade"),
    })
    one_minute = summary["by_timeframe"].get("1m", {})
    safe_pct = one_minute.get("sequence_safe_entry_touch_pct")
    no_touch_pct = one_minute.get("no_entry_touch_same_bar_pct")
    if safe_pct is not None and safe_pct >= 25:
        lead = "COLLECT_SUBMINUTE_DATA_FOR_1M_INTRABAR_SIGNAL_AVAILABILITY"
    elif no_touch_pct is not None and no_touch_pct >= 50:
        lead = "RESEARCH_EARLIER_SIGNAL_FORMATION_OR_SHALLOWER_ENTRY_WITHOUT_CHASING"
    else:
        lead = "MIXED_TIMING_AND_ENTRY_DEPTH; REQUIRE_MORE_GRANULAR_DATA"
    return {"summary": summary, "rows": rows, "next_research_lead": lead}
