#!/usr/bin/env python3
"""Automated post-hoc audit of blocked setups that moved to target before retracing to entry."""
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

from src.research.missed_move import audit_run, summarize_study

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
STUDY_ID = "missed-move-latency-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(command: list[str]) -> dict:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    text = (result.stdout + "\n" + result.stderr).strip()
    return {
        "command": " ".join(command),
        "returncode": result.returncode,
        "tail": "\n".join(text.splitlines()[-12:]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", default=STUDY_ID, choices=[STUDY_ID])
    parser.add_argument("--historical-db", default="data/otr_historical.db")
    parser.add_argument("--baseline-work-dir", default="data/phase6-study-work-v2")
    parser.add_argument("--output-dir", default="data/phase6-missed-move")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    production_db = ROOT / "data/otrmarket.db"
    before = sha256(production_db)
    if before != EXPECTED_PRODUCTION_DB_SHA256:
        raise SystemExit(f"STOP: production DB hash mismatch before study: {before}")

    historical = (ROOT / args.historical_db).resolve()
    baseline = (ROOT / args.baseline_work_dir).resolve()
    pattern = str(baseline / "*baseline_15_8_4_2*-oos.db")
    run_databases = sorted(Path(path) for path in glob.glob(pattern))
    if len(run_databases) != 4:
        raise SystemExit(f"STOP: expected 4 baseline OOS DBs, found {len(run_databases)}")

    runs = [audit_run(path, historical) for path in run_databases]
    result = summarize_study(runs)
    payload = {
        "study_id": args.study,
        "study_type": "MISSED_MOVE_TIMING_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "final_holdout_accessed": False,
            "run_databases": [path.name for path in run_databases],
        },
        "methodology": {
            "purpose": "Measure how quickly blocked setups reach target before any future retrace to planned entry.",
            "decision_price": "Last causal 1m close at or before the blocking decision.",
            "progress_r": "Directional distance from planned entry to decision price divided by planned stop risk.",
            "promotion": "Diagnostic only. Does not loosen gates or change production behavior.",
        },
        "summary": result["summary"],
        "rows": result["rows"],
        "phase7_ready": False,
        "production_ready": False,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["digest"] = hashlib.sha256(canonical.encode()).hexdigest()

    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{args.study}.json"
    output.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")

    checks = []
    if not args.skip_tests:
        checks = [
            check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]),
            check([sys.executable, "-m", "compileall", "-q", "src", "scripts"]),
            check(["node", "--check", "src/dashboard/static/research.js"]),
            check(["git", "diff", "--check"]),
        ]
        if any(item["returncode"] != 0 for item in checks):
            print(json.dumps({"status": "FAILED_VERIFICATION", "checks": checks, "output": str(output)}, indent=2))
            return 2

    after = sha256(production_db)
    if after != before:
        raise SystemExit(f"STOP: production DB changed: {before} -> {after}")

    summary = result["summary"]
    report = {
        "status": "COMPLETE",
        "study_id": args.study,
        "blocked_setups": summary["blocked_setups"],
        "missed_move_before_entry": summary["missed_move_before_entry"],
        "missed_move_share_pct": summary["missed_move_share_pct"],
        "median_minutes_to_target": summary["median_minutes_to_target"],
        "within_5m_pct": summary["within_5m_pct"],
        "median_decision_progress_r": summary["median_decision_progress_r"],
        "already_beyond_entry_pct": summary["already_beyond_entry_pct"],
        "already_0_5r_or_more": summary["already_0_5r_or_more"],
        "already_1r_or_more": summary["already_1r_or_more"],
        "already_2r_or_more": summary["already_2r_or_more"],
        "by_gate": summary["by_gate"],
        "by_timeframe": summary["by_timeframe"],
        "by_symbol": summary["by_symbol"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
        "output": str(output),
        "next_decision": "REVIEW_SIGNAL_TIMING_AND_ENTRY_DEPTH; DO_NOT_LOOSEN_PROTECTIVE_GATES",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
