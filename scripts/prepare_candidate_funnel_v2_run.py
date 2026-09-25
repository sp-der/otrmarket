from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

# Railway starts this file as "python scripts/prepare_candidate_funnel_v2_run.py".
# In that invocation Python puts /app/scripts on sys.path, not the repository
# root, so imports from src fail unless we add the project root explicitly.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.storage.database as database
from src.storage.database_concurrency80 import install as install_database_concurrency


EXPECTED_BASELINE_RUN_ID = "run-81-51ef9e9a34b2"
BASELINE_LABEL = "Full Week Baseline - Pre Candidate Funnel V2"
NEW_RUN_LABEL = "Candidate Funnel V2 - Same Week Validation"


def prepare_run() -> dict:
    install_database_concurrency()

    from src.research.run_archive81 import start_fresh_run81
    from src.research.run_dashboard81 import run_dashboard81
    from src.research.run_scope import current_run_id
    from src.storage.database import set_engine_state

    connection = database.get_connection()
    try:
        active_run_id = current_run_id(connection)

        # Idempotence across restarts/redeploys: once the expected baseline is
        # no longer active, this one-time operator script must never rotate
        # another run.
        if active_run_id != EXPECTED_BASELINE_RUN_ID:
            report = {
                "applied": False,
                "reason": "expected_baseline_not_active",
                "expected_run_id": EXPECTED_BASELINE_RUN_ID,
                "active_run_id": active_run_id,
                "current": run_dashboard81(connection),
            }
            print("OTR_FRESH_RUN_ROTATION=" + json.dumps(report, default=str), flush=True)
            return report

        before = run_dashboard81(connection)
        result = start_fresh_run81(
            connection,
            baseline_label=BASELINE_LABEL,
            new_label=NEW_RUN_LABEL,
        )
        connection.execute("DELETE FROM strategy_diagnostics")
        set_engine_state(connection, "eval_reset_excluded_setup_ids_72", "[]")
        connection.commit()

        after = run_dashboard81(connection)
        report = {
            "applied": True,
            "before": before,
            "rotation": result,
            "after": after,
        }
        print("OTR_FRESH_RUN_ROTATION=" + json.dumps(report, default=str), flush=True)
        return report
    finally:
        connection.close()


if __name__ == "__main__":
    prepare_run()
    runpy.run_module("src.dashboard.server_81", run_name="__main__")
