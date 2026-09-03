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


def _promote_engine_72r() -> str:
    """Patch the module that actually owns the 7.2 engine promotion hook.

    server_72r -> server_72q -> server_72n -> server_72. The prior 7.2R
    launcher patched server_72n one level too shallow, so server_72n.main()
    still called server_72.promoted_engine_module() and spawned src.main_72.
    """
    base.base.base.promoted_engine_module = lambda requested=None: "src.main_72r"
    return base.base.base.promoted_engine_module()


def main() -> None:
    base._normalize_verify_environment_72q()
    base._wipe_verify_test_state_72q()
    run_id = _install_verify_run_id_72r()
    engine_module = _promote_engine_72r()
    print(
        "Operation 7.2R supervisor: 7.2Q verification protections + GC 5m/15m momentum first-pullback recognition; "
        f"engine={engine_module} verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.base.main()


if __name__ == "__main__":
    main()
