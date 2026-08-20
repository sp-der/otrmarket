#!/usr/bin/env python3
"""Automated Phase 6F accepted-order lifecycle audit, research only."""
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

from src.research.pending_lifecycle import load_order_lifecycles, summarize_lifecycles

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
SUPPORTED_STUDY = "pending-lifecycle-v1"
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
    parser.add_argument("--baseline-work-dir", default="data/phase6-study-work-v2")
    parser.add_argument("--output-dir", default="data/phase6-pending-lifecycle")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    production_db = ROOT / "data/otrmarket.db"
    baseline_dir = (ROOT / args.baseline_work_dir).resolve()
    output_dir = (ROOT / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    before = sha256(production_db)
    if before != EXPECTED_PRODUCTION_DB_SHA256:
        raise SystemExit(f"STOP: production DB hash mismatch before study: {before}")

    run_databases = sorted(
        Path(path)
        for path in glob.glob(str(baseline_dir / "*baseline_15_8_4_2*-oos.db"))
    )
    if len(run_databases) != 4:
        raise SystemExit(
            f"STOP: expected 4 authoritative baseline OOS databases, found {len(run_databases)}"
        )

    run_payloads = []
    all_rows = []
    for run_database in run_databases:
        run, rows = load_order_lifecycles(run_database)
        if str(run.get("end_time")) > HOLDOUT_START:
            raise SystemExit(f"STOP: run crosses final holdout firewall: {run_database.name}")
        all_rows.extend(rows)
        run_payloads.append(
            {
                "run_id": run["run_id"],
                "start_time": run["start_time"],
                "end_time": run["end_time"],
                "summary": summarize_lifecycles(rows),
            }
        )

    summary = summarize_lifecycles(all_rows)
    payload = {
        "study_id": args.study,
        "study_type": "ACCEPTED_ORDER_PENDING_LIFECYCLE_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "run_databases": [path.name for path in run_databases],
            "final_holdout_start": HOLDOUT_START,
            "final_holdout_accessed": False,
        },
        "methodology": {
            "population": (
                "Only setups that passed the runtime gates far enough to create ORDER_STATE telemetry in the "
                "authoritative baseline OOS replay."
            ),
            "purpose": (
                "Separate protective gate blocking from accepted-order misses such as STALE_AT_REGISTRATION, "
                "TARGET_PROGRESS_75, PENDING_EXPIRED, and STOP_BREACHED_BEFORE_ENTRY."
            ),
            "account_state": "Post-hoc only; no order or account state is changed.",
            "promotion": "Diagnostic research only. Cannot change production or unlock Phase 7.",
        },
        "runs": run_payloads,
        "summary": summary,
        "orders": all_rows,
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
        checks.extend(
            [
                run_check([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]),
                run_check([sys.executable, "-m", "compileall", "-q", "src", "scripts"]),
                run_check(["node", "--check", "src/dashboard/static/research.js"]),
                run_check(["git", "diff", "--check"]),
            ]
        )
        failed = [check for check in checks if check["returncode"] != 0]
        if failed:
            print(
                json.dumps(
                    {"status": "FAILED_VERIFICATION", "output": str(output_file), "checks": checks},
                    indent=2,
                )
            )
            return 2

    after = sha256(production_db)
    if after != before:
        raise SystemExit(f"STOP: production DB changed during study: {before} -> {after}")

    report = {
        "status": "COMPLETE",
        "study_id": args.study,
        "output": str(output_file),
        "digest": payload["digest"],
        "registered_orders": summary["registered_orders"],
        "filled_orders": summary["filled_orders"],
        "filled_order_pct": summary["filled_order_pct"],
        "never_filled_orders": summary["never_filled_orders"],
        "cancelled_before_or_without_fill": summary["cancelled_before_or_without_fill"],
        "immediate_registration_stale": summary["immediate_registration_stale"],
        "target_progress_stale": summary["target_progress_stale"],
        "pending_expired": summary["pending_expired"],
        "stop_breached_before_entry": summary["stop_breached_before_entry"],
        "cancellation_reasons": summary["cancellation_reasons"],
        "median_cancellation_progress_to_target": summary["median_cancellation_progress_to_target"],
        "median_immediate_stale_progress_to_target": summary["median_immediate_stale_progress_to_target"],
        "by_strategy": summary["by_strategy"],
        "by_timeframe": summary["by_timeframe"],
        "by_symbol": summary["by_symbol"],
        "by_grade": summary["by_grade"],
        "by_entry_type": summary["by_entry_type"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
        "next_decision": "REVIEW_ACCEPTED_ORDER_LIFECYCLE_BEFORE_ANY_ENTRY_MODEL_CHANGE",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
