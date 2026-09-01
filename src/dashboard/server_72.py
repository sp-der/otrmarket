from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import runpy

from src.risk.eval_history72 import apply_nondestructive_eval_reset, install_eval_history_filter
from src.storage.database import get_connection


LEGACY_ENGINE_MODULES = {
    "",
    "src.main_58",
    "src.main_59",
    "src.main_61",
    "src.main_62",
    "src.main_63",
    "src.main_64",
    "src.main_65",
    "src.main_66",
    "src.main_67",
    "src.main_68",
    "src.main_69",
    "src.main_70",
    "src.main_71",
}


def promoted_engine_module(requested: str | None = None) -> str:
    value = (requested if requested is not None else os.getenv("OTR_ENGINE_MODULE", "")).strip()
    if value in LEGACY_ENGINE_MODULES:
        return "src.main_72"
    return value


def _env_truthy_72(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_number_72(name: str) -> float | None:
    value = os.getenv(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _repair_paper_eval_config_72m() -> None:
    """Repair the legacy million-value bypass only for PAPER EVAL runs.

    During replay calibration, old Railway values used 1,000,000 as a sentinel
    to effectively disable multiple evaluation limits. Operation 7.2L now has
    first-class unlimited trade-count semantics via zero, so those sentinels are
    both misleading in the dashboard and unsafe as an evaluation model.

    This repair is deliberately scoped to PAPER execution plus EVAL mode. It
    never rewrites funded/live broker configuration, and it only activates when
    the old bypass is actually detected or the evaluation guard was disabled.
    """
    execution_mode = os.getenv("OTR_EXECUTION_MODE", "PAPER").strip().upper()
    trading_mode = os.getenv("OTR_TRADING_MODE", "").strip().upper()
    phase = os.getenv("EVAL_PHASE", "EVALUATION").strip().upper()
    eval_mode = trading_mode in {"EVAL", "EVALUATION"} or (
        not trading_mode and phase.startswith("EVALUATION")
    )
    if execution_mode != "PAPER" or not eval_mode:
        return

    sentinel_names = (
        "EVAL_MAX_LOSS",
        "EVAL_FIRM_DAILY_LOSS",
        "EVAL_INTERNAL_DAILY_STOP",
        "EVAL_MLL_SAFETY_BUFFER",
        "EVAL_MAX_TRADES_PER_DAY",
        "EVAL_MAX_CONSECUTIVE_LOSSES",
        "OTR_EVAL_MAX_TRADES_DAY",
        "OTR_EVAL_MAX_TRADES_SESSION",
        "OTR_CALIBRATION_MAX_TRADES_DAY",
        "OTR_BASE_WIN_LOCK_DOLLARS",
    )
    has_legacy_sentinel = any(
        value is not None and abs(value) >= 100_000
        for value in (_env_number_72(name) for name in sentinel_names)
    )
    guard_value = os.getenv("EVAL_GUARD_ENABLED")
    guard_disabled = guard_value is not None and not _env_truthy_72(guard_value)
    if not has_legacy_sentinel and not guard_disabled:
        return

    restored = {
        "EVAL_GUARD_ENABLED": "1",
        "EVAL_MAX_LOSS": "2000",
        "EVAL_FIRM_DAILY_LOSS": "1200",
        "EVAL_RISK_PER_TRADE": "500",
        "EVAL_MIN_RISK_PER_TRADE": "100",
        "EVAL_INTERNAL_DAILY_STOP": "750",
        "EVAL_MLL_SAFETY_BUFFER": "400",
        "EVAL_MAX_TRADES_PER_DAY": "0",
        "EVAL_MAX_CONSECUTIVE_LOSSES": "2",
        "EVAL_MAX_CONCURRENT": "1",
        "EVAL_SESSION_PROFIT_CAP": "1500",
        "EVAL_CONTINUE_AFTER_TARGET": "0",
        "OTR_EVAL_MAX_TRADES_DAY": "0",
        "OTR_EVAL_MAX_TRADES_SESSION": "0",
        "OTR_CALIBRATION_MAX_TRADES_DAY": "0",
        "OTR_BASE_WIN_LOCK_DOLLARS": "0",
    }
    os.environ.update(restored)
    print(
        "Operation 7.2M paper-eval repair: restored 50K evaluation risk rails; "
        "trade count remains unlimited via zero-valued caps.",
        flush=True,
    )


def _install_execution_routes() -> None:
    dashboard = importlib.import_module("src.dashboard.app")
    expected_path = "/market/api/execution/status"
    if any(getattr(route, "path", None) == expected_path for route in dashboard.app.routes):
        return
    from src.execution.live.api import build_router

    dashboard.app.include_router(
        build_router(
            require_http_auth=dashboard.require_http_auth,
            require_bridge_key=dashboard.require_bridge_key,
        )
    )


def _install_active_market_overview_stats_72k() -> None:
    """Keep Overview metrics aligned with the markets allowed to trade.

    Operation 7.2I made GC the only active strategy symbol by default, while
    preserving NQ/ES history in SQLite. The Gold-only UI hid those old rows,
    but DashboardRepository.trade_stats still counted them, so Total R, win
    rate, and P/L could disagree with the visible Trades page.

    Patch only the 7.2 dashboard process. Historical rows stay untouched and
    will automatically return to the Overview if those symbols are explicitly
    re-enabled through OTR_ACTIVE_STRATEGY_SYMBOLS later.
    """
    from src.dashboard.queries import DashboardRepository, TRADING_TZ
    from src.runtime.session import ACTIVE_STRATEGY_SYMBOLS

    marker = "_otr_active_market_overview_stats_72k"
    if getattr(DashboardRepository, marker, False):
        return

    original_trade_stats = DashboardRepository.trade_stats

    def active_market_trade_stats(self, connection, reference_time=None):
        symbols = {str(symbol).upper() for symbol in ACTIVE_STRATEGY_SYMBOLS if str(symbol).strip()}
        if not symbols:
            return original_trade_stats(self, connection, reference_time)

        defaults = self.trade_stats_empty()
        if not self._table_exists(connection, "paper_trades"):
            return defaults

        trades = [
            trade
            for trade in self.recent_trades(connection, limit=1_000_000)
            if str(trade.get("symbol") or "").upper() in symbols
        ]

        closed_results = [
            trade
            for trade in trades
            if trade.get("status") == "CLOSED" and trade.get("result_r") is not None
        ]
        wins = sum(1 for trade in closed_results if trade.get("result") == "WIN")
        losses = sum(1 for trade in closed_results if trade.get("result") == "LOSS")
        invalidated = sum(1 for trade in trades if trade.get("status") == "INVALIDATED")
        pending = sum(1 for trade in trades if trade.get("status") == "PENDING")
        open_count = sum(1 for trade in trades if trade.get("status") == "OPEN")
        total_r = sum(float(trade.get("result_r") or 0.0) for trade in closed_results)
        total_dollars = sum(
            float(trade.get("display_result_dollars") or 0.0)
            for trade in closed_results
            if trade.get("display_result_dollars") is not None
        )
        positive_r = sum(
            float(trade["result_r"])
            for trade in closed_results
            if float(trade["result_r"]) > 0
        )
        negative_r = abs(
            sum(
                float(trade["result_r"])
                for trade in closed_results
                if float(trade["result_r"]) < 0
            )
        )

        def chronology_key(trade):
            parsed = self._parse_time(
                trade.get("closed_at") or trade.get("opened_at") or trade.get("updated_at")
            )
            return parsed.timestamp() if parsed is not None else 0.0

        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for trade in sorted(closed_results, key=chronology_key):
            running += float(trade.get("result_r") or 0.0)
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)

        reference_time = reference_time or self._now()
        if reference_time.tzinfo is None:
            from datetime import timezone
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        trading_date = reference_time.astimezone(TRADING_TZ).date()
        today_r = 0.0
        today_dollars = 0.0
        for trade in closed_results:
            closed_at = self._parse_time(trade.get("closed_at"))
            if closed_at is not None and closed_at.astimezone(TRADING_TZ).date() == trading_date:
                today_r += float(trade.get("result_r") or 0.0)
                today_dollars += float(trade.get("display_result_dollars") or 0.0)

        return {
            "closed": len(closed_results),
            "wins": wins,
            "losses": losses,
            "invalidated": invalidated,
            "pending": pending,
            "open": open_count,
            "win_rate": (wins / len(closed_results) * 100.0) if closed_results else None,
            "total_r": total_r,
            "avg_r": (total_r / len(closed_results)) if closed_results else None,
            "profit_factor": (
                positive_r / negative_r
                if negative_r > 0
                else (None if positive_r == 0 else positive_r)
            ),
            "max_drawdown_r": max_drawdown,
            "today_r": today_r,
            "total_dollars": total_dollars,
            "today_dollars": today_dollars,
        }

    DashboardRepository.trade_stats = active_market_trade_stats
    setattr(DashboardRepository, marker, True)
    print(
        "Operation 7.2K dashboard accounting: Overview metrics limited to active strategy symbols "
        f"{sorted(ACTIVE_STRATEGY_SYMBOLS)}",
        flush=True,
    )


def _audit_latest_eval_limit_block() -> None:
    """Print the replay-market timestamp for the latest daily-slot rejection.

    This is intentionally read-only and runs before a requested counter reset,
    giving us one last audit breadcrumb while preserving all historical rows.
    """
    connection = get_connection()
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_setups'"
        ).fetchone()
        if not exists:
            return
        rows = connection.execute(
            """
            SELECT symbol, timeframe, status, created_at, payload_json
            FROM strategy_setups
            WHERE status = 'QUALITY_BLOCKED'
            ORDER BY created_at DESC
            LIMIT 250
            """
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row[4] or "{}")
                reason = str(
                    payload.get("metadata", {})
                    .get("execution_quality_gate", {})
                    .get("reason")
                    or ""
                )
            except (TypeError, ValueError):
                reason = ""
            if "daily primary-trade limit reached" not in reason.lower():
                continue
            print(
                "REPLAY AUDIT latest eval-slot block: "
                f"market_time={row[3]} symbol={row[0]} timeframe={row[1]} reason={reason}",
                flush=True,
            )
            return
    finally:
        connection.close()


def _patch_dashboard_html_72() -> None:
    """Add execution safety UI and keep Operation 7.2 dashboard assets current."""
    static_dir = Path(__file__).resolve().parent / "static"
    path = static_dir / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    changed = False

    css_tag = '<link rel="stylesheet" href="/market/assets/execution-safety.css?v=7.2">'
    if css_tag not in text and "</head>" in text:
        text = text.replace("</head>", f"  {css_tag}\n</head>", 1)
        changed = True

    panel_marker = '<div class="section-kicker">RUNTIME</div><h2>Dashboard Endpoint</h2>'
    if 'id="executionModeStatus"' not in text and panel_marker in text:
        panel = """      <section class="panel execution-safety-panel">
        <div class="panel-head">
          <div><div class="section-kicker">OPERATION 7.2</div><h2>Execution Safety</h2></div>
          <span id="executionTransmissionStatus" class="tiny-chip">LOCKED</span>
        </div>
        <div class="execution-safety-grid">
          <div class="execution-safety-card"><span>Mode</span><strong id="executionModeStatus">--</strong></div>
          <div class="execution-safety-card"><span>Arming</span><strong id="executionArmStatus">--</strong></div>
          <div class="execution-safety-card"><span>Account</span><strong id="executionAccountStatus">--</strong></div>
          <div class="execution-safety-card"><span>Reconciliation</span><strong id="executionReconciliationStatus">--</strong></div>
          <div class="execution-safety-card"><span>Bridge heartbeat</span><strong id="executionBridgeHeartbeat">--</strong></div>
          <div class="execution-safety-card"><span>Active commands</span><strong id="executionQueueStatus">0</strong></div>
          <div class="execution-safety-card"><span>Kill switch</span><strong id="executionKillStatus">--</strong></div>
          <div class="execution-safety-card"><span>Broker transmission</span><strong>Fail closed</strong></div>
        </div>
        <div class="execution-safety-actions">
          <button id="executionKillEngage" class="execution-danger-button" type="button">Engage Kill Switch</button>
          <button id="executionKillReset" class="execution-reset-button" type="button">Reset Kill Switch</button>
        </div>
        <div id="executionSafetyNote" class="execution-safety-note muted">Loading execution safety state.</div>
      </section>

"""
        runtime_section = '      <section class="panel">\n        <div class="panel-head"><div><div class="section-kicker">RUNTIME</div><h2>Dashboard Endpoint</h2></div></div>'
        if runtime_section in text:
            text = text.replace(runtime_section, panel + runtime_section, 1)
            changed = True

    app_old = '<script src="/market/assets/app.js?v=4.5" defer></script>'
    app_new = '<script src="/market/assets/app.js?v=7.2m" defer></script>'
    if app_old in text:
        text = text.replace(app_old, app_new, 1)
        changed = True

    scanner_old = '<script src="/market/assets/scanner-decision-live.js?v=6.4" defer></script>'
    scanner_new = '<script src="/market/assets/scanner-decision-live.js?v=7.2k" defer></script>'
    if scanner_old in text:
        text = text.replace(scanner_old, scanner_new, 1)
        changed = True

    script_tag = '<script src="/market/assets/execution-safety.js?v=7.2" defer></script>'
    if script_tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{script_tag}\n</body>", 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")

    app_path = static_dir / "app.js"
    if app_path.exists():
        app_text = app_path.read_text(encoding="utf-8")
        old_trade_limit = '  $("evalTrades").textContent = `${e.trades_today || 0} / ${e.max_trades_per_day || 0}`;'
        new_trade_limit = '''  const evalTradeLimit = Number(e.max_trades_per_day || 0);
  $("evalTrades").textContent = evalTradeLimit > 0
    ? `${e.trades_today || 0} / ${evalTradeLimit}`
    : `${e.trades_today || 0} / Unlimited`;'''
        if old_trade_limit in app_text:
            app_path.write_text(
                app_text.replace(old_trade_limit, new_trade_limit, 1),
                encoding="utf-8",
            )


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = promoted_engine_module()
    _repair_paper_eval_config_72m()
    _audit_latest_eval_limit_block()
    # Operation 7.2G intercepts OTR_RESET_EVAL_TOKEN before the legacy
    # supervisor sees it. Existing trade/history rows stay intact while their
    # setup IDs become prior-run history for new eval accounting.
    apply_nondestructive_eval_reset()
    install_eval_history_filter()
    _install_active_market_overview_stats_72k()
    _patch_dashboard_html_72()
    _install_execution_routes()
    runpy.run_module("src.dashboard.server", run_name="__main__")


if __name__ == "__main__":
    main()
