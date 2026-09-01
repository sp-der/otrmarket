from __future__ import annotations

import os
from pathlib import Path
import runpy

from src.dashboard import server_72 as base


VERIFY_MODES_72N = {"VERIFY", "VERIFICATION", "TEST"}
FULL_TRADE_WINDOW_72N = 5_000


def _verification_enabled_72n() -> bool:
    return os.getenv("OTR_TRADING_MODE", "").strip().upper() in VERIFY_MODES_72N


def _verify_risk_72n() -> float:
    raw = os.getenv("OTR_VERIFY_RISK_PER_TRADE", os.getenv("EVAL_RISK_PER_TRADE", "500"))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 500.0


def _install_dashboard_contract_72n() -> None:
    """Expose complete trade history and an honest VERIFY dashboard state."""
    from src.dashboard.queries import DashboardRepository

    marker = "_otr_dashboard_contract_72n"
    if getattr(DashboardRepository, marker, False):
        return

    original_recent_trades = DashboardRepository.recent_trades
    original_evaluation_snapshot = DashboardRepository.evaluation_snapshot

    def expanded_recent_trades(self, connection, limit=30):
        # /snapshot historically asked for only 30 total rows. Rejected and
        # invalidated attempts can fill that window and push real closed trades
        # out of the browser payload. Keep explicit callers unchanged, but give
        # the main dashboard a deep ledger so its WIN/LOSS/Open/Pending filters
        # can render the actual accepted trade history.
        effective_limit = FULL_TRADE_WINDOW_72N if limit == 30 else limit
        return original_recent_trades(self, connection, limit=effective_limit)

    def verification_aware_evaluation_snapshot(self, connection, runtime):
        snapshot = original_evaluation_snapshot(self, connection, runtime)
        mode = os.getenv("OTR_TRADING_MODE", "").strip().upper() or "EVAL"
        snapshot["trading_mode"] = mode
        if not _verification_enabled_72n():
            return snapshot

        risk = _verify_risk_72n()
        snapshot.update(
            profile="OTR_CONTINUOUS_VERIFY_7_2N",
            phase="VERIFY",
            status="VERIFY",
            reason=(
                "Continuous verification is active. Account/eval profit-loss governors are bypassed; "
                "strategy quality and one-active-position protection remain active."
            ),
            profit_target=0.0,
            profit_progress=0.0,
            target_met=False,
            continue_after_target=True,
            session_profit_cap=0.0,
            internal_daily_stop=0.0,
            max_trades_per_day=0,
            max_consecutive_losses=0,
            available_risk=risk,
            verify_risk_per_trade=risk,
            trading_mode="VERIFY",
        )
        return snapshot

    DashboardRepository.recent_trades = expanded_recent_trades
    DashboardRepository.evaluation_snapshot = verification_aware_evaluation_snapshot
    setattr(DashboardRepository, marker, True)
    print(
        f"Operation 7.2N dashboard: snapshot trade window expanded to {FULL_TRADE_WINDOW_72N}; "
        f"trading_mode={'VERIFY' if _verification_enabled_72n() else os.getenv('OTR_TRADING_MODE', 'EVAL')}",
        flush=True,
    )


def _patch_verification_asset_72n() -> None:
    static_dir = Path(__file__).resolve().parent / "static"
    path = static_dir / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    script_tag = '<script src="/market/assets/verification-mode72.js?v=7.2n" defer></script>'
    if script_tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{script_tag}\n</body>", 1)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = base.promoted_engine_module()
    # 7.2M only repairs actual EVAL mode. VERIFY intentionally skips that
    # repair so the verification engine can own its first-class bypass.
    base._repair_paper_eval_config_72m()
    base._audit_latest_eval_limit_block()
    base.apply_nondestructive_eval_reset()
    base.install_eval_history_filter()
    base._install_active_market_overview_stats_72k()
    _install_dashboard_contract_72n()
    base._patch_dashboard_html_72()
    _patch_verification_asset_72n()
    base._install_execution_routes()
    runpy.run_module("src.dashboard.server", run_name="__main__")


if __name__ == "__main__":
    main()
