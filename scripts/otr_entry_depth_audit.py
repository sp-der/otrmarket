#!/usr/bin/env python3
"""Automated Phase 6G accepted-order entry-depth audit, research only."""
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

from src.research.entry_depth import audit_run, summarize_entry_depth

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
SUPPORTED_STUDY = "entry-depth-v1"
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
    parser.add_argument("--output-dir", default="data/phase6-entry-depth")
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
    registered_setups = 0
    for run_database in run_databases:
        result = audit_run(run_database, historical_db)
        if result["end_time"] > HOLDOUT_START:
            raise SystemExit(f"STOP: run crosses final holdout firewall: {run_database.name}")
        registered_setups += int(result["registered_setups"])
        all_rows.extend(result["rows"])
        run_payloads.append({
            "run_id": result["run_id"],
            "start_time": result["start_time"],
            "end_time": result["end_time"],
            "registered_setups": result["registered_setups"],
            "summary": result["summary"],
        })

    summary = summarize_entry_depth(all_rows)
    payload = {
        "study_id": args.study,
        "study_type": "ACCEPTED_ORDER_ENTRY_DEPTH_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "registered_setups": registered_setups,
            "run_databases": [path.name for path in run_databases],
            "final_holdout_start": HOLDOUT_START,
            "final_holdout_accessed": False,
        },
        "methodology": {
            "population": "Only setup decisions that passed the gates far enough to register an order in the authoritative baseline OOS replays.",
            "variants": ["ORIGINAL", "FVG_SHALLOW_25", "FVG_MIDPOINT", "OTE_50", "OTE_62", "OTE_70_5", "OTE_79"],
            "geometry": "Alternative entries keep the original accepted thesis stop and target, are rounded to exchange ticks, and must preserve strategy-specific minimum R:R.",
            "no_chase": "A bullish limit above the decision close or bearish limit below the decision close is rejected as marketable/chasing.",
            "sequencing": "Variant orders begin after the setup decision close. The decision candle is never retroactively filled. Future ambiguous stop/target bars use STOP_FIRST through the existing counterfactual evaluator.",
            "account_feedback": "None. Each variant is an independent shadow diagnostic and does not alter recovery, cooldown, daily limits, or subsequent setup eligibility.",
            "interpretation": "Diagnostic R and fill/rescue rates identify a bounded entry-depth thesis. They are not portfolio P&L and cannot promote a production change by themselves.",
            "promotion": "Research only. A promising variant must become a pre-registered replay candidate and pass walk-forward/robustness before Phase 7 is reconsidered.",
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
        "registered_setups": registered_setups,
        "variant_rows": summary["rows"],
        "eligible_variant_rows": summary["eligible"],
        "by_variant": summary["by_variant"],
        "by_strategy": summary["by_strategy"],
        "by_timeframe": summary["by_timeframe"],
        "by_original_entry_type": summary["by_original_entry_type"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
        "next_decision": "COMPARE_BOUNDED_SHALLOW_ENTRY_VARIANTS; DO_NOT_CHANGE_PRODUCTION_FROM_POSTHOC_EVIDENCE",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
