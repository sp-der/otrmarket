#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import glob
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.decision_bar import audit_run, summarize_study

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
SUPPORTED_STUDY = "decision-bar-latency-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_check(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    text = (result.stdout + "\n" + result.stderr).strip()
    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "tail": "\n".join(text.splitlines()[-12:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", required=True, choices=[SUPPORTED_STUDY])
    parser.add_argument("--historical-db", default="data/otr_historical.db")
    parser.add_argument("--baseline-work-dir", default="data/phase6-study-work-v2")
    parser.add_argument("--output-dir", default="data/phase6-decision-bar")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    production_db = ROOT / "data/otrmarket.db"
    historical_db = (ROOT / args.historical_db).resolve()
    baseline_dir = (ROOT / args.baseline_work_dir).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    before = sha256(production_db)
    if before != EXPECTED_PRODUCTION_DB_SHA256:
        raise SystemExit(f"STOP: production DB hash mismatch before study: {before}")
    if not historical_db.exists():
        raise SystemExit(f"STOP: historical database not found: {historical_db}")

    pattern = str(baseline_dir / "*baseline_15_8_4_2*-oos.db")
    run_databases = sorted(Path(path) for path in glob.glob(pattern))
    if len(run_databases) != 4:
        raise SystemExit(f"STOP: expected 4 authoritative baseline OOS databases, found {len(run_databases)}")

    runs = [audit_run(path, historical_db) for path in run_databases]
    study = summarize_study(runs)
    payload = {
        "study_id": args.study,
        "study_type": "DECISION_BAR_LATENCY_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "run_databases": [path.name for path in run_databases],
            "final_holdout_accessed": False,
        },
        "methodology": {
            "question": "Was the planned entry still physically available inside the bar that closed when the block decision was emitted?",
            "data": "Causal 1-minute OHLC only.",
            "ordering_limit": "OHLC cannot prove intrabar signal availability or order multiple price touches.",
            "sequence_safe": "Entry touched in the decision bar while neither stop nor target touched that same bar.",
            "promotion": "Diagnostic only; cannot change production rules or justify live intrabar execution by itself.",
        },
        "runs": [{"run_id": run["run_id"], "summary": run["summary"]} for run in runs],
        "summary": study["summary"],
        "next_research_lead": study["next_research_lead"],
        "phase7_ready": False,
        "production_ready": False,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    output_file = output_dir / f"{args.study}.json"
    temp = output_file.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(output_file)

    checks = []
    if not args.skip_tests:
        checks = [
            run_check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]),
            run_check([sys.executable, "-m", "compileall", "-q", "src", "scripts"]),
            run_check(["node", "--check", "src/dashboard/static/research.js"]),
            run_check(["git", "diff", "--check"]),
        ]
        if any(check["returncode"] != 0 for check in checks):
            print(json.dumps({"status": "FAILED_VERIFICATION", "output": str(output_file), "verification": checks}, indent=2))
            return 2

    after = sha256(production_db)
    if after != before:
        raise SystemExit(f"STOP: production DB changed during study: {before} -> {after}")

    s = study["summary"]
    report = {
        "status": "COMPLETE",
        "study_id": args.study,
        "output": str(output_file),
        "digest": payload["digest"],
        "setups": s["setups"],
        "decision_bar_found": s["decision_bar_found"],
        "entry_touch_same_bar": s["entry_touch_same_bar"],
        "entry_touch_same_bar_pct": s["entry_touch_same_bar_pct"],
        "sequence_safe_entry_touch": s["sequence_safe_entry_touch"],
        "sequence_safe_entry_touch_pct": s["sequence_safe_entry_touch_pct"],
        "ambiguous_entry_touch": s["ambiguous_entry_touch"],
        "no_entry_touch_same_bar": s["no_entry_touch_same_bar"],
        "no_entry_touch_same_bar_pct": s["no_entry_touch_same_bar_pct"],
        "median_decision_progress_r": s["median_decision_progress_r"],
        "by_timeframe": s["by_timeframe"],
        "by_gate": s["by_gate"],
        "by_symbol": s["by_symbol"],
        "next_research_lead": study["next_research_lead"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
