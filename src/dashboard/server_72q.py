from __future__ import annotations

from datetime import datetime, timezone
import os

from src.dashboard import server_72n as base


VERIFY_RISK_MULTIPLIER_VARS = (
    "OTR_CORE_RISK_MULTIPLIER",
    "OTR_BTC_OFFHOURS_RISK_MULTIPLIER",
    "OTR_SUNDAY_GLOBEX_RISK_MULTIPLIER",
    "OTR_ASIA_RISK_MULTIPLIER",
    "OTR_LONDON_RISK_MULTIPLIER",
    "OTR_PREMARKET_RISK_MULTIPLIER",
    "OTR_AFTERNOON_RISK_MULTIPLIER",
    "OTR_LATE_RISK_MULTIPLIER",
)


def _normalize_verify_environment_72q() -> None:
    if not base._verification_enabled_72n():
        return
    for name in VERIFY_RISK_MULTIPLIER_VARS:
        os.environ[name] = "1.0"


def _install_verify_run_id_72q() -> str:
    """Give this VERIFY deployment a stable run ID inherited by the engine.

    Railway exposes a deployment ID that remains stable across process restarts
    of the same deployment. Local/dev runs fall back to a UTC launch stamp.
    """
    if not base._verification_enabled_72n():
        return ""
    existing = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    if existing:
        return existing
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
    token = deployment[:12] if deployment else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"7.2Q-{token}"
    os.environ["OTR_VERIFY_RUN_ID"] = run_id
    return run_id


def main() -> None:
    _normalize_verify_environment_72q()
    run_id = _install_verify_run_id_72q()
    base.base.promoted_engine_module = lambda: "src.main_72q"
    print(
        "Operation 7.2Q supervisor: clean VERIFY runtime, no loss-pruning hooks; "
        f"engine=src.main_72q verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
