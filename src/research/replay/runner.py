from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys

from .runs import ReplayRunStore, RunManifest
from src.research.execution.account import reference_account_profile


class ReplayRunner:
    def __init__(self, run_store: ReplayRunStore, repository_root: str | Path):
        self.store = run_store
        self.repository_root = Path(repository_root).resolve()

    def run(self, manifest: RunManifest, historical_db: str | Path) -> dict:
        if manifest.replay_mode not in {"TICK_EXACT", "CANDLE_APPROXIMATE"}:
            raise ValueError("Replay mode must be explicit")
        baseline={"1m":15,"5m":8,"15m":4,"1h":2}
        lifetimes={**baseline,**(manifest.pending_lifetime_bars or {})}
        manifest=replace(manifest,account_profile=reference_account_profile(manifest.account_profile),pending_lifetime_bars=lifetimes)
        ledger = self.store.register(manifest)
        run_root = ledger.parent.parent
        request_path, result_path = run_root / "request.json", run_root / "result.json"
        request = {
            **asdict(manifest), "historical_db": str(Path(historical_db).resolve()),
            "contracts": list(manifest.contracts), "enabled_timeframes": list(manifest.enabled_timeframes),
        }
        request_path.write_text(json.dumps(request, sort_keys=True))
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.repository_root)
        for key, value in manifest.configuration.items():
            if key.startswith("EVAL_") or key.startswith("OTR_"):
                environment[key] = str(value)
        completed = subprocess.run(
            [sys.executable, "-m", "src.research.replay.worker", str(request_path), str(result_path)],
            cwd=run_root, env=environment, text=True, capture_output=True,
        )
        if completed.returncode:
            raise RuntimeError(f"Replay worker failed: {completed.stderr}")
        result = json.loads(result_path.read_text())
        result["decision_digest"] = self.store.append_traces(manifest.run_id, result["traces"])
        self.store.persist_execution(manifest.run_id,result.get("execution_trades",[]),result.get("equity",[]),result.get("account_blocks",[]))
        self.store.persist_risk_audits(manifest.run_id,result.get("risk_audits",[]))
        import hashlib
        from .runs import canonical_json
        result["execution_digest"]=hashlib.sha256(canonical_json(result.get("execution_trades",[])).encode()).hexdigest()
        result["equity_digest"]=hashlib.sha256(canonical_json(result.get("equity",[])).encode()).hexdigest()
        result["ledger_path"] = str(ledger)
        return result
