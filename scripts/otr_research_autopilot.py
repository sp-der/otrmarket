#!/usr/bin/env python3
"""OTR research autopilot for bounded, research-only follow-up studies."""
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

from src.research.counterfactual import analyze_run, summarize, HOLDOUT_START

EXPECTED_PRODUCTION_DB_SHA256 = "801b763fa788486f0ee682bbf4033417078f4da85cf85e861c7f620013cad116"
SUPPORTED_STUDY = "blocked-counterfactual-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_check(command: list[str], cwd: Path) -> dict:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
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
    parser.add_argument("--output-dir", default="data/phase6-counterfactual")
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
        raise SystemExit(
            f"STOP: expected 4 authoritative baseline OOS databases, found {len(run_databases)}"
        )

    runs = [analyze_run(path, historical_db) for path in run_databases]
    all_rows = [row for run in runs for row in run["rows"]]
    payload = {
        "study_id": args.study,
        "study_type": "BLOCKED_TRADE_COUNTERFACTUAL_POSTHOC",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "capture_id": "databento-glbx-20260501-20260818-v1",
            "baseline": "Operation 7.0 / BASELINE_15_8_4_2",
            "sample_role": "WALK_FORWARD_OOS_ONLY",
            "run_databases": [path.name for path in run_databases],
            "final_holdout_start": HOLDOUT_START,
            "final_holdout_accessed": False,
        },
        "methodology": {
            "execution": (
                "Independent shadow outcomes only; blocked setups do not mutate account/recovery state."
            ),
            "entry": (
                "First future 1m causal bar after the blocking decision that touches planned entry."
            ),
            "ambiguity": "STOP_FIRST on bars touching stop and target.",
            "horizon": (
                "Timeframe-bounded causal horizon capped at the originating OOS run end."
            ),
            "costs": (
                "No dollar P&L claim. Diagnostic R only; fees/slippage/account interactions are not applied."
            ),
            "promotion": (
                "Research lead only. This study cannot change or promote production rules."
            ),
        },
        "runs": [
            {
                "run_id": run["run_id"],
                "start_time": run["start_time"],
                "end_time": run["end_time"],
                "summary": run["summary"],
            }
            for run in runs
        ],
        "summary": summarize(all_rows),
        "setups": all_rows,
        "phase7_ready": False,
        "production_ready": False,
    }

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload["digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    output_file = output_dir / f"{args.study}.json"
    temp_file = output_file.with_suffix(".json.tmp")
    temp_file.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temp_file.replace(output_file)

    checks = []
    if not args.skip_tests:
        checks.append(
            run_check(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                ROOT,
            )
        )
        checks.append(
            run_check([sys.executable, "-m", "compileall", "-q", "src", "scripts"], ROOT)
        )
        checks.append(run_check(["node", "--check", "src/dashboard/static/research.js"], ROOT))
        checks.append(run_check(["git", "diff", "--check"], ROOT))
        failed = [check for check in checks if check["returncode"] != 0]
        if failed:
            print(
                json.dumps(
                    {
                        "status": "FAILED_VERIFICATION",
                        "output": str(output_file),
                        "checks": checks,
                    },
                    indent=2,
                )
            )
            return 2

    after = sha256(production_db)
    if after != before:
        raise SystemExit(f"STOP: production DB changed during study: {before} -> {after}")

    summary = payload["summary"]
    report = {
        "status": "COMPLETE",
        "study_id": args.study,
        "output": str(output_file),
        "digest": payload["digest"],
        "blocked_setups": summary["blocked_setups"],
        "geometry_eligible": summary["geometry_eligible"],
        "resolved_counterfactuals": summary["resolved_counterfactuals"],
        "shadow_winners_blocked": summary["shadow_winners_blocked"],
        "shadow_losses_prevented": summary["shadow_losses_prevented"],
        "resolved_win_rate": summary["resolved_win_rate"],
        "diagnostic_net_r": summary["diagnostic_net_r"],
        "outcomes": summary["outcomes"],
        "gate_findings": summary["by_gate"],
        "final_holdout": "UNTOUCHED",
        "production_db_sha256_before": before,
        "production_db_sha256_after": after,
        "verification": checks,
        "next_decision": "REVIEW_COUNTERFACTUAL_EVIDENCE_BEFORE_ANY_NEW_CANDIDATE",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
