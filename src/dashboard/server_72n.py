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


def _number_key_72n(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _logical_trade_key_72n(trade: dict) -> tuple:
    """Identify the same historical fill across repeated replay runs.

    Setup IDs are UUID based, so replaying the same market moment can create a
    second database row for an otherwise identical trade. Use immutable market
    geometry and replay timestamps instead of setup_id for dashboard accounting.
    """
    return (
        str(trade.get("symbol") or "").upper(),
        str(trade.get("timeframe") or "").lower(),
        str(trade.get("direction") or "").lower(),
        str(trade.get("opened_at") or ""),
        str(trade.get("closed_at") or ""),
        _number_key_72n(trade.get("entry_price")),
        _number_key_72n(trade.get("stop_price")),
        _number_key_72n(trade.get("target_price")),
        _number_key_72n(trade.get("exit_price")),
        str(trade.get("result") or "").upper(),
        _number_key_72n(trade.get("result_r")),
    )


def _trade_record_quality_72n(trade: dict) -> tuple:
    """Prefer the duplicate copy with the most complete realized accounting."""
    dollars = trade.get("display_result_dollars")
    persisted = trade.get("result_dollars")
    risk = trade.get("risk_dollars")
    try:
        useful_dollars = int(dollars is not None and abs(float(dollars)) > 1e-9)
    except (TypeError, ValueError):
        useful_dollars = 0
    try:
        useful_persisted = int(persisted is not None and abs(float(persisted)) > 1e-9)
    except (TypeError, ValueError):
        useful_persisted = 0
    try:
        useful_risk = int(risk is not None and float(risk) > 0)
    except (TypeError, ValueError):
        useful_risk = 0
    return useful_persisted, useful_dollars, useful_risk, str(trade.get("updated_at") or "")


def _repair_legacy_trade_dollars_72n(trade: dict) -> dict:
    """Repair display-only P/L for legacy nonzero-R rows stored as $0.

    Older replay rows can contain result_r while result_dollars/risk_dollars are
    missing or zero. Those rows should not display a -1R loss as $0.00. This
    does not mutate SQLite or evaluation accounting; it only normalizes the
    dashboard view using the configured verification/base risk.
    """
    if str(trade.get("status") or "").upper() != "CLOSED":
        return trade
    try:
        rr = float(trade.get("result_r"))
    except (TypeError, ValueError):
        return trade
    if abs(rr) <= 1e-9:
        return trade

    try:
        displayed = trade.get("display_result_dollars")
        displayed_zero = displayed is None or abs(float(displayed)) <= 1e-9
    except (TypeError, ValueError):
        displayed_zero = True
    try:
        stored_risk = trade.get("risk_dollars")
        legacy_risk = stored_risk is None or float(stored_risk) <= 0
    except (TypeError, ValueError):
        legacy_risk = True

    if displayed_zero and legacy_risk:
        fallback_risk = _verify_risk_72n() if _verification_enabled_72n() else float(
            os.getenv("EVAL_RISK_PER_TRADE", "500") or 500
        )
        trade = dict(trade)
        trade["display_result_dollars"] = round(rr * max(0.0, fallback_risk), 2)
        trade["legacy_dollar_reconstruction"] = True
    return trade


def _dedupe_trade_rows_72n(trades: list[dict]) -> list[dict]:
    output: list[dict] = []
    positions: dict[tuple, int] = {}
    for raw in trades:
        trade = dict(raw)
        key = _logical_trade_key_72n(trade)
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(output)
            output.append(trade)
            continue
        if _trade_record_quality_72n(trade) > _trade_record_quality_72n(output[existing_index]):
            output[existing_index] = trade
    return [_repair_legacy_trade_dollars_72n(trade) for trade in output]


def _install_dashboard_contract_72n() -> None:
    """Expose complete, deduplicated trade history and an honest VERIFY state."""
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
        # can render the actual accepted trade history. Repeated replay runs can
        # create new UUID setup IDs for identical historical fills, so collapse
        # those copies before the dashboard or Overview statistics consume them.
        effective_limit = FULL_TRADE_WINDOW_72N if limit == 30 else limit
        rows = original_recent_trades(self, connection, limit=effective_limit)
        return _dedupe_trade_rows_72n(rows)

    def verification_aware_evaluation_snapshot(self, connection, runtime):
        snapshot = original_evaluation_snapshot(self, connection, runtime)
        mode = os.getenv("OTR_TRADING_MODE", "").strip().upper() or "EVAL"
        snapshot["trading_mode"] = mode
        if not _verification_enabled_72n():
            return snapshot

        risk = _verify_risk_72n()
        reference = self._parse_time(runtime.get("market_time")) or self._now()
        stats = self.trade_stats(connection, reference)
        run_pnl = float(stats.get("total_dollars") or 0.0)
        today_pnl = float(stats.get("today_dollars") or 0.0)
        starting_balance = float(snapshot.get("starting_balance") or 50_000.0)
        snapshot.update(
            profile="OTR_CONTINUOUS_VERIFY_7_2N",
            phase="VERIFY",
            status="VERIFY",
            reason=(
                "Continuous verification is active. Account/eval profit-loss governors are bypassed; "
                "strategy quality and one-active-position protection remain active."
            ),
            balance=round(starting_balance + run_pnl, 2),
            realized_pnl=round(run_pnl, 2),
            today_pnl=round(today_pnl, 2),
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
        "replay duplicate fingerprinting + legacy P/L normalization active; "
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
