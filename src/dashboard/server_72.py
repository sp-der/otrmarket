from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import runpy

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


def _audit_latest_eval_limit_block() -> None:
    """Print the replay-market timestamp for the latest daily-slot rejection.

    This is intentionally read-only and runs before the normal fresh-eval reset
    hook, giving us one last audit breadcrumb before a requested replay wipe.
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
    """Add a read-only execution-health surface plus emergency stop controls.

    This is applied at process boot instead of rewriting the legacy dashboard
    template in-place, keeping Operation 7.2 isolated and easy to roll back.
    """
    path = Path(__file__).resolve().parent / "static" / "index.html"
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
        panel = '''      <section class="panel execution-safety-panel">
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

'''
        runtime_section = '      <section class="panel">\n        <div class="panel-head"><div><div class="section-kicker">RUNTIME</div><h2>Dashboard Endpoint</h2></div></div>'
        if runtime_section in text:
            text = text.replace(runtime_section, panel + runtime_section, 1)
            changed = True

    script_tag = '<script src="/market/assets/execution-safety.js?v=7.2" defer></script>'
    if script_tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{script_tag}\n</body>", 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = promoted_engine_module()
    _audit_latest_eval_limit_block()
    _patch_dashboard_html_72()
    _install_execution_routes()
    runpy.run_module("src.dashboard.server", run_name="__main__")


if __name__ == "__main__":
    main()
