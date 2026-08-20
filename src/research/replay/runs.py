from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .schema import RUN_SCHEMA_SQL


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    git_commit: str
    capture_id: str
    markets: tuple[str, ...]
    contracts: tuple[str, ...]
    start_time: str
    end_time: str
    enabled_timeframes: tuple[str, ...]
    configuration: dict
    account_profile: dict
    engine_version: str = "Operation 7.0"
    replay_mode: str = "CANDLE_APPROXIMATE"
    fill_model: str = "SLIPPAGE_MODEL"
    ambiguity_policy: str = "STOP_FIRST"
    execution_config: dict = None
    pending_lifetime_bars: dict = field(default_factory=lambda: {"1m":15,"5m":8,"15m":4,"1h":2})
    parent_run_id: str | None = None
    created_at: str = ""


class ReplayRunStore:
    def __init__(self, database: str | Path, runs_dir: str | Path):
        self.database = Path(database).resolve()
        self.runs_dir = Path(runs_dir).resolve()

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(RUN_SCHEMA_SQL)

    def register(self, manifest: RunManifest) -> Path:
        created = manifest.created_at or datetime.now(timezone.utc).isoformat()
        run_root = self.runs_dir / manifest.run_id
        run_root.mkdir(parents=True, exist_ok=False)
        ledger = run_root / "data" / "otrmarket.db"
        ledger.parent.mkdir()
        with sqlite3.connect(self.database) as connection:
            connection.execute("""INSERT INTO backtest_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                manifest.run_id, manifest.parent_run_id, manifest.git_commit, manifest.capture_id,
                canonical_json(manifest.markets), canonical_json(manifest.contracts), manifest.start_time,
                manifest.end_time, canonical_json(manifest.enabled_timeframes), canonical_json(manifest.configuration),
                canonical_json(manifest.account_profile), manifest.engine_version, manifest.replay_mode,
                manifest.fill_model, manifest.ambiguity_policy, canonical_json(manifest.execution_config or {}),
                canonical_json(manifest.pending_lifetime_bars),
                created, "CREATED", None, str(ledger),
            ))
        return ledger

    def append_traces(self, run_id: str, traces: list[dict]) -> str:
        normalized = []
        with sqlite3.connect(self.database) as connection:
            for sequence, trace in enumerate(traces, 1):
                payload = dict(trace)
                normalized.append(payload)
                connection.execute("""INSERT INTO decision_traces(
                  run_id,sequence_no,event_time,event_type,symbol,timeframe,strategy_type,direction,setup_id,
                  setup_grade,catalyst_json,displacement_json,fvg_json,ote_json,smt_json,htf_context_json,
                  session_json,recovery_json,quality_score,risk_reward,decision,reason,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    run_id, sequence, payload.get("event_time",""), payload.get("event_type",""),
                    payload.get("symbol"), payload.get("timeframe"), payload.get("strategy_type"),
                    payload.get("direction"), payload.get("setup_id"), payload.get("setup_grade"),
                    canonical_json(payload.get("catalyst",{})), canonical_json(payload.get("displacement",{})),
                    canonical_json(payload.get("fvg",{})), canonical_json(payload.get("ote",{})),
                    canonical_json(payload.get("smt",{})), canonical_json(payload.get("htf_context",{})),
                    canonical_json(payload.get("session",{})), canonical_json(payload.get("recovery",{})),
                    payload.get("quality_score"), payload.get("risk_reward"), payload.get("decision"),
                    payload.get("reason"), canonical_json(payload),
                ))
            digest = hashlib.sha256(canonical_json(normalized).encode()).hexdigest()
            connection.execute("UPDATE backtest_runs SET status='COMPLETE',decision_digest=? WHERE run_id=?", (digest,run_id))
        return digest

    def persist_execution(self, run_id: str, trades: list[dict], equity: list[dict], blocks: list[dict]):
        with sqlite3.connect(self.database) as connection:
            for trade in trades:
                values = dict(trade)
                values["ambiguity_flags_json"] = canonical_json(values.pop("ambiguity_flags", []))
                values["payload_json"] = canonical_json(trade)
                columns = [row[1] for row in connection.execute("PRAGMA table_info(research_trades)") if row[1] != "payload_json"] + ["payload_json"]
                connection.execute(
                    f"INSERT INTO research_trades({','.join(columns)}) VALUES({','.join(':'+c for c in columns)})",
                    {column: values.get(column) for column in columns},
                )
            for sequence, point in enumerate(equity, 1):
                connection.execute("""INSERT INTO equity_curve(run_id,sequence_no,timestamp,event_type,balance,realized_pnl,
                  unrealized_pnl,equity,peak_balance,peak_equity,realized_drawdown,equity_drawdown,intrabar_approximate_drawdown,
                  drawdown_dollars,drawdown_percent,daily_pnl,session_pnl,daily_equity_low,session_equity_low,open_risk)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id,sequence,point["timestamp"],point["event_type"],
                  point["balance"],point["realized_pnl"],point["unrealized_pnl"],point["equity"],point["peak_balance"],
                  point["peak_equity"],point["realized_drawdown"],point["equity_drawdown"],point["intrabar_approximate_drawdown"],
                  point["drawdown_dollars"],point["drawdown_percent"],point["daily_pnl"],point["session_pnl"],
                  point["daily_equity_low"],point["session_equity_low"],point["open_risk"]))
            for block in blocks:
                connection.execute("INSERT INTO account_blocks(run_id,timestamp,setup_id,status,reason,snapshot_json) VALUES(?,?,?,?,?,?)",
                  (run_id,block["timestamp"],block.get("setup_id"),block["status"],block["reason"],canonical_json(block["snapshot"])))

    def persist_risk_audits(self, run_id: str, audits: list[dict]):
        with sqlite3.connect(self.database) as connection:
            for audit in audits:
                columns=[row[1] for row in connection.execute("PRAGMA table_info(risk_audits)") if row[1] not in {"audit_id","run_id","payload_json"}]
                connection.execute(f"INSERT INTO risk_audits(run_id,{','.join(columns)},payload_json) VALUES(?,{','.join('?' for _ in columns)},?)",
                  (run_id,*[audit.get(column) for column in columns],canonical_json(audit)))
