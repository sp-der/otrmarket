from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid


def _json(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class DeterministicIds:
    def __init__(self, run_id):
        self.run_id = run_id
        self.context = ("", "", "", "")
        self.counter = defaultdict(int)

    def set_context(self, symbol, timeframe, strategy, event_time):
        self.context = (symbol, timeframe, strategy, event_time)

    def factory(self, strategy):
        def create():
            symbol, timeframe, _, event_time = self.context
            key = (symbol, timeframe, strategy, event_time)
            self.counter[key] += 1
            raw = "|".join(map(str, (self.run_id, symbol, timeframe, strategy, event_time, self.counter[key])))
            return uuid.UUID(hashlib.md5(raw.encode()).hexdigest())
        return create


def _install_ids(ids):
    from src.strategies import confluence, continuation, flexible_confluence, rejection_block, reversal
    for module, name in ((confluence,"ICT_CONFLUENCE"),(flexible_confluence,"ICT_OTE"),
                         (rejection_block,"REJECTION_BLOCK_10_10"),(reversal,"MSS_REVERSAL"),
                         (continuation,"TREND_CONTINUATION_REARM")):
        module.uuid4 = ids.factory(name)
    uuid.uuid4 = ids.factory("RUNTIME_SETUP")


def _trace_setup(setup, event_time):
    metadata = dict(setup.metadata or {})
    context = metadata.get("a_plus_context", {}) or {}
    displacement = setup.displacement.to_dict() if hasattr(setup.displacement, "to_dict") else _json(setup.displacement)
    fvg = setup.entry_fvg.to_dict() if hasattr(setup.entry_fvg, "to_dict") else _json(setup.entry_fvg)
    session = metadata.get("session_consistency", {}) or {}
    recovery = metadata.get("recovery_control_70", {}) or {}
    gate = metadata.get("execution_quality_gate", {}) or {}
    evaluation = metadata.get("evaluation_guard", {}) or {}
    return {
        "event_time": event_time, "event_type": "SETUP_DECISION", "symbol": setup.symbol,
        "timeframe": setup.timeframe, "strategy_type": metadata.get("strategy", "ICT_CONFLUENCE"),
        "direction": setup.direction, "setup_id": setup.setup_id,
        "setup_grade": context.get("quality_grade"), "catalyst": setup.trigger_details,
        "displacement": displacement, "fvg": fvg,
        "ote": {key: value for key, value in metadata.items() if "ote" in key.lower() or "entry" in key.lower()},
        "smt": {key: value for key, value in setup.trigger_details.items() if "smt" in key.lower()},
        "htf_context": context, "session": session, "recovery": recovery,
        "quality_score": context.get("quality_score"), "risk_reward": setup.risk_reward,
        "decision": setup.status, "reason": gate.get("reason") or metadata.get("tier_reason"),
        "requested_risk": evaluation.get("risk_cap_dollars"),
        "allowed_risk": evaluation.get("risk_dollars"),
        "planned_entry": setup.entry_price, "stop": setup.stop_price, "target": setup.target_price,
        "metadata": metadata,
    }


def execute(request: dict) -> dict:
    # The subprocess cwd is the run directory. Production's relative DB path
    # therefore resolves to this run's disposable ledger, never the repository.
    from src import main_70  # installs the authoritative Operation 7.0 chain
    runtime = main_70.runtime
    from src.strategies.models import Candle
    from src.research.replay.scheduler import pair_available, synchronized_candle_groups
    from src.research.execution.in_loop import InLoopResearchExecutor
    from src.research.execution.simulator import ExecutionConfig
    from src.research.execution.metrics import raw_metrics

    ids = DeterministicIds(request["run_id"])
    _install_ids(ids)
    exec_values=request.get("execution_config") or {}
    execution=ExecutionConfig(request.get("fill_model","SLIPPAGE_MODEL"),int(exec_values.get("slippage_ticks",1)),
      float(exec_values.get("round_turn_commission",2.50)),float(exec_values.get("round_turn_fees",.50)),
      request.get("ambiguity_policy","STOP_FIRST"))
    research_executor=InLoopResearchExecutor(request["run_id"],request.get("account_profile") or {},execution,
      request["replay_mode"],request.get("pending_lifetime_bars") or {"1m":15,"5m":8,"15m":4,"1h":2})
    runtime.paper=research_executor
    traces = []
    current_time = {"value": request["start_time"]}

    original_save_setup = runtime.save_setup
    original_upsert = runtime.upsert_paper_trade

    def save_setup(connection, setup):
        original_save_setup(connection, setup)
        traces.append(_trace_setup(setup, current_time["value"]))

    def save_diagnostic(connection, diagnostic):
        if not diagnostic:
            return
        traces.append({
            "event_time": current_time["value"], "event_type": "SCANNER_STATE",
            "symbol": diagnostic.get("symbol"), "timeframe": diagnostic.get("timeframe"),
            "strategy_type": diagnostic.get("strategy_name"), "direction": diagnostic.get("direction"),
            "setup_id": diagnostic.get("setup_id"), "decision": diagnostic.get("stage"),
            "reason": diagnostic.get("note"), "diagnostic": dict(diagnostic),
        })

    def upsert(connection, position, updated_at):
        original_upsert(connection, position, updated_at)
        traces.append({
            "event_time": str(updated_at), "event_type": "ORDER_STATE", "symbol": position.setup.symbol,
            "timeframe": position.setup.timeframe, "strategy_type": position.setup.metadata.get("strategy"),
            "direction": position.setup.direction, "setup_id": position.setup.setup_id,
            "decision": position.result or position.status, "reason": position.guard_reason,
            "risk_reward": position.setup.risk_reward, "status": position.status,
            "entry": position.setup.entry_price, "stop": position.setup.stop_price,
            "target": position.setup.target_price, "exit": position.exit_price,
            "cancellation": getattr(position,"cancellation_details",None),
        })

    runtime.save_setup = save_setup
    runtime.save_diagnostic = save_diagnostic
    runtime.upsert_paper_trade = upsert
    connection = runtime.get_connection()
    historical = sqlite3.connect(f"file:{Path(request['historical_db']).resolve()}?mode=ro", uri=True)
    historical.row_factory = sqlite3.Row
    try:
        mode = request["replay_mode"]
        args = [request["capture_id"], request["start_time"], request["end_time"]]
        contracts = request["contracts"]
        placeholders = ",".join("?" for _ in contracts)
        if mode == "TICK_EXACT":
            incomplete = historical.execute(f"SELECT COUNT(*) FROM historical_events WHERE capture_id=? AND contract IN ({placeholders}) AND exchange_timestamp>=? AND exchange_timestamp<=? AND (data_gap=1 OR integrity_status!='VALID' OR volume IS NULL)", [request["capture_id"], *contracts, request["start_time"], request["end_time"]]).fetchone()[0]
            if incomplete:
                raise ValueError(f"TICK_EXACT refused: {incomplete} incomplete events")
            rows = historical.execute(f"SELECT * FROM historical_events WHERE capture_id=? AND contract IN ({placeholders}) AND exchange_timestamp>=? AND exchange_timestamp<=? ORDER BY exchange_timestamp,CASE root_symbol WHEN 'NQ' THEN 0 WHEN 'ES' THEN 1 ELSE 2 END,contract,sequence_no", [request["capture_id"], *contracts, request["start_time"], request["end_time"]])
            for row in rows:
                current_time["value"] = row["exchange_timestamp"]
                ids.set_context(row["root_symbol"], "tick", "RUNTIME", current_time["value"])
                runtime.paper.set_quote(row["root_symbol"],row["bid"],row["ask"])
                runtime.process_price(connection, row["root_symbol"], row["last_price"], row["bid"], row["ask"], datetime.fromisoformat(row["exchange_timestamp"]))
        else:
            timeframes = request["enabled_timeframes"]
            tf_places = ",".join("?" for _ in timeframes)
            has_series = historical.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_series_bars'").fetchone() and historical.execute(
                "SELECT 1 FROM research_series_bars WHERE capture_id=? LIMIT 1", (request["capture_id"],)).fetchone()
            if has_series:
                rows = historical.execute(f"""SELECT cc.* FROM canonical_candles cc
                  JOIN research_series_bars rs ON rs.capture_id=cc.capture_id
                   AND rs.root_symbol=cc.root_symbol AND rs.open_time=cc.open_time AND rs.contract=cc.contract
                  WHERE cc.capture_id=? AND cc.contract IN ({placeholders}) AND cc.timeframe IN ({tf_places})
                   AND cc.close_time>=? AND cc.close_time<=? ORDER BY cc.close_time""",
                  [request["capture_id"], *contracts, *timeframes, request["start_time"], request["end_time"]]).fetchall()
            else:
                rows = historical.execute(f"SELECT * FROM canonical_candles WHERE capture_id=? AND contract IN ({placeholders}) AND timeframe IN ({tf_places}) AND close_time>=? AND close_time<=? ORDER BY close_time", [request["capture_id"], *contracts, *timeframes, request["start_time"], request["end_time"]]).fetchall()
            for close_time, group in synchronized_candle_groups(rows):
                current_time["value"] = close_time
                event_dt = datetime.fromisoformat(close_time)
                # Advance pending orders once per root using the finest closing bar.
                root_bars = {}
                for row in reversed(group):
                    root_bars[row["root_symbol"]] = row
                for root in ("NQ", "ES", "GC"):
                    if root in root_bars:
                        bar=root_bars[root]
                        for position in runtime.paper.on_candle(root,event_dt,bar["open"],bar["high"],bar["low"],bar["close"]):
                            runtime.upsert_paper_trade(connection, position, close_time)
                # Publish all candles first. No lower-timeframe evaluation can see an unclosed HTF bar.
                for row in group:
                    candle = Candle(row["root_symbol"], row["timeframe"], datetime.fromisoformat(row["open_time"]), event_dt,
                                    row["open"], row["high"], row["low"], row["close"], row["event_count"])
                    runtime.candles.history[(candle.symbol,candle.timeframe)].append(candle)
                histories = runtime.histories_snapshot()
                for row in group:
                    symbol, timeframe = row["root_symbol"], row["timeframe"]
                    ids.set_context(symbol, timeframe, "RUNTIME", close_time)
                    if symbol in {"NQ","ES"} and not pair_available(histories, symbol, timeframe, close_time):
                        traces.append({"event_time": close_time,"event_type":"SMT_SUPPRESSED","symbol":symbol,
                                       "timeframe":timeframe,"decision":"BLOCKED","reason":"Required paired NQ/ES candle is missing"})
                        continue
                    runtime.evaluate_strategy(connection, symbol, timeframe)
    finally:
        historical.close()
        connection.close()
    simulator=research_executor.simulator
    metrics,segments=raw_metrics(simulator.records,simulator.account.equity_points)
    return {"traces": traces, "trace_count": len(traces),"execution_trades":simulator.records,
      "equity":simulator.account.equity_points,"account_blocks":simulator.blocks,"risk_audits":simulator.risk_audits,
      "metrics":metrics,"segments":segments}


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    result = execute(request)
    Path(sys.argv[2]).write_text(json.dumps(result, sort_keys=True, default=_json))


if __name__ == "__main__":
    main()
