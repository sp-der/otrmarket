from __future__ import annotations

from datetime import datetime, timezone
import os

from src.dashboard import server_72q as base


def _install_verify_run_id_72r() -> str:
    if not base.base._verification_enabled_72n():
        return ""
    existing = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    if existing.startswith("7.2R-"):
        return existing
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
    token = deployment[:12] if deployment else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"7.2R-{token}"
    os.environ["OTR_VERIFY_RUN_ID"] = run_id
    return run_id


def main() -> None:
    base._normalize_verify_environment_72q()
    base._wipe_verify_test_state_72q()
    run_id = _install_verify_run_id_72r()
    base.base.promoted_engine_module = lambda: "src.main_72r"
    print(
        "Operation 7.2R supervisor: 7.2Q verification protections + GC 5m/15m momentum first-pullback recognition; "
        f"engine=src.main_72r verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.base.main()


if __name__ == "__main__":
    main()
