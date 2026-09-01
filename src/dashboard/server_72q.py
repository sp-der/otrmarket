from __future__ import annotations

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
    # VERIFY measures the strategy brain in a constant risk unit. Session-based
    # risk multipliers are restored automatically when OTR_TRADING_MODE leaves
    # VERIFY because this mutation is process-local only.
    for name in VERIFY_RISK_MULTIPLIER_VARS:
        os.environ[name] = "1.0"


def main() -> None:
    _normalize_verify_environment_72q()
    # Clean canonical verification stack: no ledger-pruning hooks. Promote the
    # engine to 7.2Q, then reuse 7.2N's full-history/VERIFY dashboard contract.
    base.base.promoted_engine_module = lambda: "src.main_72q"
    print(
        "Operation 7.2Q supervisor: clean VERIFY runtime, no loss-pruning hooks; engine=src.main_72q",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
