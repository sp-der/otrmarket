#!/usr/bin/env python3
"""Automated Phase 6E scanner-stage latency audit, research only."""
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

from src.research.missed_move import audit_run
from src.research.stage_latency import load_scanner_states, enrich_stage_latency, summarize_stage_latency

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
SUPPORTED_STUDY = "stage-latency-v1"
HOLDOUT_START = "2026-08-07T00:00:00+00:00"


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
    parser.add_argument("--output-dir", default="data/phase6-stage-latency")
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

    run_databases = sorted(Path(path) for path in glob.glob(str(baseline_dir / "*baseline_15_8_4_2*-oos.db")))
    if len(run_databases) != 4:
        raise SystemExit(f"STOP: expected 4 authoritative baseline OOS databases, found {len(run_databases)}")

    run_payloads = []
    all_rows = []
    for run_database in run_databases:
        missed = audit_run(run_database, historical_db)
        states = load_scanner_states(run_database)
        enriched = enrich_stage_latency(missed["missed_moves"], states)
        all_rows.extend(enriched)
        run_payloads.append({
            "run_id": missed["run_id"],
            "start_time": missed["start_time"],
            "end_time": missed["end_time"],
            "missed_moves": len(enriched),
            "summary": summarize_stage_latency(enriched),
        })

    summary = summarize_stage_latency(all_rows)
    payload = {
        "study_id": args.study,
        "study_type": "SCANNER_STAGE_LATENCY_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "run_databases": [path.name for path in run_databases],
            "final_holdout_start": HOLDOUT_START,
            "final_holdout_accessed": False,
        },
        "methodology": {
            "population": "Only MISSED_MOVE_BEFORE_ENTRY setups from the prior counterfactual audit.",
            "episode_linkage": "Scanner states are linked by symbol/timeframe and bounded by the most recent WAIT_PD_ARRAY/WARMUP/EXPIRED scanner state before the blocking decision.",
            "latency": "Observed causal elapsed minutes in scanner stages before the setup decision. No setup gates are bypassed and no trade is simulated here.",
            "interpretation": "WAIT_SIGNAL/WAIT_DISPLACEMENT time indicates confirmation latency; WAIT_ENTRY_FVG/WAIT_QUALIFYING_FVG/WAIT_VALID_RR time indicates entry-formation or geometry latency.",
            "promotion": "Diagnostic research only. Cannot change production or unlock Phase 7.",
        },
        "runs": run_payloads,
        "summary": summary,
        "rows": all_rows,
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
        checks.extend([
            run_check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]),
            run_check([sys.executable, "-m", "compileall", "-q", "src", "scripts"]),
            run_check(["node", "--check", "src/dashboard/static/research.js"]),
            run_check(["git", "diff", "--check"]),
        ])
        failed = [check for check in checks if check["returncode"] != 0]
        if failed:
            print(json.dumps({"status": "FAILED_VERIFICATION", "output": str(output_file), "checks": checks}, indent=2))
            return 2

    after = sha256(production_db)
    if after != before:
        raise SystemExit(f"STOP: production DB changed during study: {before} -> {after}")

    report = {
        "status": "COMPLETE",
        "study_id": args.study,
        "output": str(output_file),
        "digest": payload["digest"],
        "setups": summary["setups"],
        "matched_stage_episodes": summary["matched_stage_episodes"],
        "matched_stage_episode_pct": summary["matched_stage_episode_pct"],
        "median_episode_minutes": summary["median_episode_minutes"],
        "median_pre_entry_minutes": summary["median_pre_entry_minutes"],
        "median_entry_search_minutes": summary["median_entry_search_minutes"],
        "dominant_stage_counts": summary["dominant_stage_counts"],
        "median_stage_dwell_minutes": summary["median_stage_dwell_minutes"],
        "primary_latency_bucket": summary["primary_latency_bucket"],
        "by_timeframe": summary["by_timeframe"],
        "by_gate": summary["by_gate"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
        "next_decision": "TARGET_THE_DOMINANT_PRE_DECISION_STAGE; DO_NOT_LOOSEN_PROTECTIVE_GATES",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
