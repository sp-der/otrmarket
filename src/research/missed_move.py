from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
import statistics
from pathlib import Path

from src.research.counterfactual import analyze_run, load_causal_bars


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(x) for x in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * fraction
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def summarize_missed_rows(rows: list[dict]) -> dict:
    minutes = [float(x["minutes_to_target"]) for x in rows if x.get("minutes_to_target") is not None]
    progress = [float(x["decision_progress_r"]) for x in rows if x.get("decision_progress_r") is not None]
    total = len(rows)
    return {
        "setups": total,
        "median_minutes_to_target": statistics.median(minutes) if minutes else None,
        "p25_minutes_to_target": _percentile(minutes, 0.25),
        "p75_minutes_to_target": _percentile(minutes, 0.75),
        "within_1m": sum(x <= 1 for x in minutes),
        "within_3m": sum(x <= 3 for x in minutes),
        "within_5m": sum(x <= 5 for x in minutes),
        "within_15m": sum(x <= 15 for x in minutes),
        "within_30m": sum(x <= 30 for x in minutes),
        "within_5m_pct": (sum(x <= 5 for x in minutes) / len(minutes) * 100.0) if minutes else None,
        "median_decision_progress_r": statistics.median(progress) if progress else None,
        "already_beyond_entry": sum(x > 0 for x in progress),
        "already_beyond_entry_pct": (sum(x > 0 for x in progress) / len(progress) * 100.0) if progress else None,
        "already_0_5r_or_more": sum(x >= 0.5 for x in progress),
        "already_1r_or_more": sum(x >= 1.0 for x in progress),
        "already_2r_or_more": sum(x >= 2.0 for x in progress),
    }


def _segment(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    return {name: summarize_missed_rows(values) for name, values in sorted(groups.items())}


def enrich_missed_moves(rows: list[dict], bars_by_symbol: dict[str, list[dict]]) -> list[dict]:
    close_times = {symbol: [bar["close_time"] for bar in bars] for symbol, bars in bars_by_symbol.items()}
    output = []
    for row in rows:
        if row.get("outcome") != "MISSED_MOVE_BEFORE_ENTRY":
            continue
        item = dict(row)
        try:
            event = datetime.fromisoformat(item["event_time"])
            terminal = datetime.fromisoformat(item["terminal_time"])
            item["minutes_to_target"] = max(0.0, (terminal - event).total_seconds() / 60.0)
        except Exception:
            item["minutes_to_target"] = None

        symbol = item.get("symbol")
        bars = bars_by_symbol.get(symbol, [])
        times = close_times.get(symbol, [])
        index = bisect_right(times, item.get("event_time") or "") - 1
        decision_close = float(bars[index]["close"]) if index >= 0 else None
        item["decision_close"] = decision_close

        try:
            entry = float(item["entry"])
            stop = float(item["stop"])
            risk = abs(entry - stop)
            if decision_close is None or risk <= 0:
                raise ValueError
            if item.get("direction") == "LONG":
                progress = (decision_close - entry) / risk
            else:
                progress = (entry - decision_close) / risk
            item["decision_progress_r"] = progress
            item["decision_entry_distance_r"] = abs(decision_close - entry) / risk
            planned_r = float(item.get("planned_r") or 0)
            item["decision_target_remaining_r"] = planned_r - progress
        except Exception:
            item["decision_progress_r"] = None
            item["decision_entry_distance_r"] = None
            item["decision_target_remaining_r"] = None
        output.append(item)
    return output


def audit_run(run_database: str | Path, historical_database: str | Path) -> dict:
    analysis = analyze_run(run_database, historical_database)
    bars = load_causal_bars(historical_database, analysis["start_time"], analysis["end_time"])
    missed = enrich_missed_moves(analysis["rows"], bars)
    return {
        "run_id": analysis["run_id"],
        "start_time": analysis["start_time"],
        "end_time": analysis["end_time"],
        "blocked_setups": analysis["summary"]["blocked_setups"],
        "missed_moves": missed,
    }


def summarize_study(runs: list[dict]) -> dict:
    rows = [row for run in runs for row in run["missed_moves"]]
    blocked = sum(int(run["blocked_setups"]) for run in runs)
    summary = summarize_missed_rows(rows)
    summary.update({
        "blocked_setups": blocked,
        "missed_move_before_entry": len(rows),
        "missed_move_share_pct": (len(rows) / blocked * 100.0) if blocked else None,
        "by_gate": _segment(rows, "gate"),
        "by_symbol": _segment(rows, "symbol"),
        "by_strategy": _segment(rows, "strategy"),
        "by_timeframe": _segment(rows, "timeframe"),
        "by_grade": _segment(rows, "grade"),
    })
    return {"summary": summary, "rows": rows}
