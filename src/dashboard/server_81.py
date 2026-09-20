from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse

from src.dashboard import server_80 as base
from src.integrations.nautilus_shadow.ledger import ensure_parity_ledger, run_recent_gold_parity
from src.integrations.vibe_research.routes import install_vibe_research_routes
from src.execution.paper import PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1
from src.otr8.execution_policy81 import FULL_RISK_DOLLARS, REDUCED_RISK_DOLLARS
from src.research.conversion_funnel81 import conversion_funnel81
from src.research.run_scope import ENGINE_VERSION, OPERATION_VERSION, current_run_id, rotate_run_id
from src.risk.evaluation import EvaluationConfig
from src.storage.database import get_connection, get_engine_state, set_engine_state


RUN_RESET_STATE_KEY_81 = "operation81_run_reset_generation"
# Explicit-intent reset: a destructive reset only ever runs when an operator
# sets this env var to a new, non-empty value for this deploy. Missing/empty
# means "preserve all data" (the safe default). This replaces the old design
# where bumping a hardcoded generation string in a code commit was enough to
# silently DELETE active trading tables on the next boot.
RUN_RESET_TOKEN_ENV_81 = "OTR_OPERATION81_RESET_TOKEN"
RUN_RESET_TABLES_81 = (
    "paper_trades",
    "strategy_setups",
    "strategy_diagnostics",
    "decision_traces_80",
    "verify_run_trades",
    "training_decisions_72t",
    "training_trades_72t",
    "training_trade_metrics_72t",
    "training_counterfactuals_72t",
    "training_shadow_72t",
    "verify_active_run_72s",
)


def _promote_engine_81() -> str:
    base.core72.promoted_engine_module = lambda requested=None: "src.main_81"
    return base.core72.promoted_engine_module()


def _reset_active_replay_progress_81() -> dict[str, int]:
    """Explicit-intent overnight scorecard reset for Operation 8.1.

    This performs a destructive reset ONLY when the operator sets
    OTR_OPERATION81_RESET_TOKEN to a new, non-empty value on this deploy.
    A missing/empty token preserves all data (the safe default). Applying the
    same token twice is a no-op: the applied token is recorded in
    engine_state so restarts never repeat the reset. The reset also refuses
    to run while a PENDING or OPEN paper position exists, since deleting
    paper_trades out from under a live position would corrupt tracking.

    Trading/run state is cleared so Overview, EVAL accounting, conversion
    telemetry, scanner state and the trade list begin at zero. Long-lived
    learning evidence is deliberately preserved: market_quotes, candles,
    counterfactual_setups, market_lessons, learning_feature_stats,
    trade_intelligence and shadow history.
    """
    token = (os.getenv(RUN_RESET_TOKEN_ENV_81) or "").strip()
    if not token:
        print(
            f"Operation 8.1 overnight replay reset: {RUN_RESET_TOKEN_ENV_81} not set; preserving all data.",
            flush=True,
        )
        return {}

    connection = get_connection()
    try:
        previous = get_engine_state(connection, RUN_RESET_STATE_KEY_81, "") or ""
        if previous == token:
            print(
                "Operation 8.1 overnight replay reset: token already applied; preserving the current run.",
                flush=True,
            )
            return {}

        if base.legacy._table_exists_72t(connection, "paper_trades"):
            open_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM paper_trades WHERE status IN ('PENDING','OPEN')"
                ).fetchone()[0]
            )
            if open_count:
                print(
                    "Operation 8.1 overnight replay reset REFUSED: "
                    f"{open_count} PENDING/OPEN paper position(s) exist; resolve them before "
                    "applying a new reset token. No data was changed.",
                    flush=True,
                )
                return {}

        counts: dict[str, int] = {}
        for table in RUN_RESET_TABLES_81:
            if not base.legacy._table_exists_72t(connection, table):
                continue
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            connection.execute(f"DELETE FROM {table}")

        # A completely fresh active run must not inherit old run-membership IDs.
        for key in (
            "verify_test_run_id_72s",
            "verify_test_wipe_token_72s",
            "last_verify_wipe_token_72q",
        ):
            connection.execute("DELETE FROM engine_state WHERE key=?", (key,))
        set_engine_state(connection, "eval_reset_excluded_setup_ids_72", "[]")
        set_engine_state(connection, RUN_RESET_STATE_KEY_81, token)
        # A wiped scorecard is genuinely a new research run; give Research Lab
        # a fresh run_id so it never pools the cleared generation with the
        # trades that come after it.
        new_run_id = rotate_run_id(connection)
        connection.commit()

        summary = ", ".join(f"{table}={count}" for table, count in counts.items()) or "no prior run rows"
        print(
            "Operation 8.1 EXPLICIT OVERNIGHT REPLAY RESET applied: "
            + summary
            + f"; new research run_id={new_run_id}"
            + "; preserved candles, market quotes, counterfactual learning, market lessons, feature stats, intelligence and shadow history.",
            flush=True,
        )
        return counts
    finally:
        connection.close()


def _install_conversion_api_81() -> None:
    from src.dashboard import app as dashboard

    path = f"{dashboard.BASE_PATH}/api/otr81/conversion"
    if any(getattr(route, "path", None) == path for route in dashboard.app.routes):
        return

    async def conversion_snapshot(request: Request):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return conversion_funnel81(connection, symbol="GC")
        finally:
            connection.close()

    dashboard.app.add_api_route(
        path,
        conversion_snapshot,
        methods=["GET"],
        name="gold_execution_conversion_81",
    )


def _json_list(value: str | None) -> list:
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return loaded if isinstance(loaded, list) else []


def _nautilus_parity_snapshot_81(connection, *, recent_limit: int = 20) -> dict:
    ensure_parity_ledger(connection)
    totals = connection.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(matched_trade_path), 0),
            COALESCE(SUM(matched_full), 0)
        FROM nautilus_shadow_parity
        """
    ).fetchone()
    rows = connection.execute(
        """
        SELECT
            setup_id, observed_at, signal_contract, execution_contract, quantity,
            paper_risk_dollars, shadow_risk_dollars, matched_trade_path, matched_full,
            difference_categories_json, difference_details_json, note
        FROM nautilus_shadow_parity
        ORDER BY observed_at DESC
        LIMIT ?
        """,
        (max(1, min(int(recent_limit), 100)),),
    ).fetchall()
    return {
        "authoritative": False,
        "mode": "NAUTILUS_SHADOW_DIAGNOSTIC",
        "records": int(totals[0] or 0),
        "trade_path_matches": int(totals[1] or 0),
        "full_matches": int(totals[2] or 0),
        "recent": [
            {
                "setup_id": row[0],
                "observed_at": row[1],
                "signal_contract": row[2],
                "execution_contract": row[3],
                "quantity": int(row[4] or 0),
                "paper_risk_dollars": row[5],
                "shadow_risk_dollars": row[6],
                "matched_trade_path": bool(row[7]),
                "matched_full": bool(row[8]),
                "categories": _json_list(row[9]),
                "details": _json_list(row[10]),
                "note": row[11] or "",
            }
            for row in rows
        ],
    }


def _parity_page_html_81() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>OTR Nautilus Parity</title>
<style>
  :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
  body { margin:0; background:#090909; color:#f4f4f4; }
  main { max-width:1100px; margin:0 auto; padding:28px 18px 60px; }
  h1 { margin:0 0 6px; font-size:clamp(26px,5vw,44px); letter-spacing:-.04em; }
  .sub { color:#999; margin-bottom:22px; max-width:760px; line-height:1.5; }
  .controls { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
  button, input { border:1px solid #333; background:#151515; color:#fff; border-radius:10px; padding:10px 13px; font:inherit; }
  button { cursor:pointer; font-weight:700; }
  button:hover { background:#222; }
  input { width:74px; }
  .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin-bottom:18px; }
  .card { background:#111; border:1px solid #252525; border-radius:14px; padding:15px; }
  .label { color:#8c8c8c; font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
  .value { font-size:28px; font-weight:800; margin-top:5px; }
  table { width:100%; border-collapse:collapse; background:#101010; border:1px solid #252525; }
  th,td { padding:10px; border-bottom:1px solid #242424; text-align:left; vertical-align:top; font-size:13px; }
  th { color:#999; font-size:11px; text-transform:uppercase; letter-spacing:.06em; }
  .ok { color:#76e59a; } .bad { color:#ff8b8b; } .muted { color:#888; }
  pre { white-space:pre-wrap; overflow-wrap:anywhere; background:#0e0e0e; border:1px solid #252525; padding:12px; border-radius:12px; }
  @media(max-width:700px){ .grid{grid-template-columns:1fr;} table{display:block; overflow-x:auto;} }
</style>
</head>
<body>
<main>
  <h1>Nautilus Parity</h1>
  <div class="sub">Gold replay referee. OTR remains authoritative. This page only replays closed OTR trades through NautilusTrader and records where execution paths agree or diverge.</div>
  <div class="controls">
    <label>Recent closed Gold trades <input id="limit" type="number" min="1" max="25" value="10" /></label>
    <button id="run">Run parity</button>
    <button id="refresh">Refresh ledger</button>
  </div>
  <div class="grid">
    <div class="card"><div class="label">Ledger Records</div><div class="value" id="records">0</div></div>
    <div class="card"><div class="label">Trade Path Matches</div><div class="value" id="paths">0</div></div>
    <div class="card"><div class="label">Full Matches</div><div class="value" id="full">0</div></div>
  </div>
  <div id="status" class="sub">Ready.</div>
  <table>
    <thead><tr><th>Setup</th><th>Contracts</th><th>Path</th><th>Full</th><th>Differences</th><th>Risk</th></tr></thead>
    <tbody id="rows"></tbody>
  </table>
  <h3>Last Run</h3><pre id="result">No parity run yet.</pre>
</main>
<script>
const $ = (id) => document.getElementById(id);
function esc(v){ return String(v ?? '').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c])); }
async function load(){
  const r=await fetch('/market/api/otr81/nautilus-parity',{credentials:'same-origin'});
  if(!r.ok) throw new Error('Summary HTTP '+r.status);
  const d=await r.json();
  $('records').textContent=d.records; $('paths').textContent=d.trade_path_matches; $('full').textContent=d.full_matches;
  $('rows').innerHTML=(d.recent||[]).map(x=>`<tr><td>${esc(x.setup_id)}</td><td>${esc(x.signal_contract)} → ${esc(x.execution_contract)} × ${x.quantity}</td><td class="${x.matched_trade_path?'ok':'bad'}">${x.matched_trade_path?'MATCH':'DIFF'}</td><td class="${x.matched_full?'ok':'bad'}">${x.matched_full?'MATCH':'DIFF'}</td><td>${esc((x.categories||[]).join(', ')||'none')}<div class="muted">${esc(x.note||'')}</div></td><td>$${esc(x.paper_risk_dollars)} → $${esc(x.shadow_risk_dollars)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">No parity records yet.</td></tr>';
}
async function run(){
  const limit=Math.max(1,Math.min(25,Number($('limit').value)||10));
  $('run').disabled=true; $('status').textContent='Running Nautilus shadow comparison…';
  try{
    const r=await fetch('/market/api/otr81/nautilus-parity/run?limit='+limit,{method:'POST',credentials:'same-origin'});
    const d=await r.json(); if(!r.ok) throw new Error(JSON.stringify(d));
    $('result').textContent=JSON.stringify(d,null,2);
    $('status').textContent=`Compared ${d.records} of ${d.requested} requested trades. ${d.skipped.length} skipped.`;
    await load();
  }catch(e){ $('status').textContent='Parity run failed: '+e.message; }
  finally{ $('run').disabled=false; }
}
$('run').addEventListener('click',run); $('refresh').addEventListener('click',()=>load().catch(e=>$('status').textContent=e.message));
load().catch(e=>$('status').textContent=e.message);
</script>
</body></html>"""


def _install_nautilus_parity_api_81() -> None:
    from src.dashboard import app as dashboard

    summary_path = f"{dashboard.BASE_PATH}/api/otr81/nautilus-parity"
    run_path = f"{summary_path}/run"
    page_path = f"{dashboard.BASE_PATH}/nautilus-parity"
    existing = {getattr(route, "path", None) for route in dashboard.app.routes}
    if summary_path in existing and run_path in existing and page_path in existing:
        return

    def parity_summary(request: Request):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return _nautilus_parity_snapshot_81(connection)
        finally:
            connection.close()

    def parity_run(request: Request, limit: int = 10):
        dashboard.require_http_auth(request)
        bounded = max(1, min(int(limit), 25))
        connection = get_connection()
        try:
            report = run_recent_gold_parity(connection, limit=bounded)
            return {
                "authoritative": False,
                "requested": report.requested,
                "records": len(report.records),
                "trade_path_matches": report.trade_path_matches,
                "full_matches": report.full_matches,
                "mismatches": [
                    {
                        "setup_id": item.setup_id,
                        "categories": list(item.difference_categories),
                        "details": list(item.difference_details),
                        "note": item.note,
                    }
                    for item in report.records
                    if not item.matched_full
                ],
                "skipped": [
                    {"setup_id": item.setup_id, "error": item.error}
                    for item in report.errors
                ],
            }
        finally:
            connection.close()

    def parity_page(request: Request):
        dashboard.require_http_auth(request)
        return HTMLResponse(_parity_page_html_81())

    if summary_path not in existing:
        dashboard.app.add_api_route(summary_path, parity_summary, methods=["GET"], name="nautilus_parity_summary_81")
    if run_path not in existing:
        dashboard.app.add_api_route(run_path, parity_run, methods=["POST"], name="nautilus_parity_run_81")
    if page_path not in existing:
        dashboard.app.add_api_route(page_path, parity_page, methods=["GET"], name="nautilus_parity_page_81", response_class=HTMLResponse)


def _install_connection_fallback_81() -> None:
    """Keep the full dashboard live behind Vercel rewrites.

    Vercel serves /gold correctly and proxies the HTTP APIs, but its current rewrite
    path does not preserve the dashboard websocket upgrade. Operation 8.1 therefore
    polls the already-working snapshot endpoint and feeds that payload into the same
    renderer app.js uses for websocket snapshots. This keeps markets, P/L, trades,
    scanner, queue, EVAL cards and connection status updating without a websocket.
    """
    path = Path(__file__).resolve().parent / "static" / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    old = '<script src="/market/assets/connection-poll81.js?v=8.1-live1" defer></script>'
    tag = '<script src="/market/assets/connection-poll81.js?v=8.1-live2" defer></script>'
    if old in text:
        text = text.replace(old, tag)
    elif tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{tag}\n</body>", 1)
    path.write_text(text, encoding="utf-8")


def _startup_risk_envelope_81() -> dict:
    """Print the resolved Operation 8.1 risk envelope once at boot.

    Read-only: reports already-resolved configuration, never mutates it, and
    never prints secrets (broker/provider credentials are never read here).
    A misconfigured envelope is surfaced as a WARN log line, never a crash.
    """
    config = EvaluationConfig.from_env()
    execution_mode = os.getenv("OTR_EXECUTION_MODE", "PAPER").strip().upper()
    broker_armed = base.core72._env_truthy_72(os.getenv("OTR_EXECUTION_ARMED"))

    envelope = {
        "EVAL_GUARD_ENABLED": config.enabled,
        "EVAL_RISK_PER_TRADE": config.risk_per_trade,
        "EVAL_MIN_RISK_PER_TRADE": config.min_risk_per_trade,
        "EVAL_INTERNAL_DAILY_STOP": config.internal_daily_stop,
        "EVAL_FIRM_DAILY_LOSS": config.firm_daily_loss_limit,
        "EVAL_MAX_CONSECUTIVE_LOSSES": config.max_consecutive_losses,
        "EVAL_MAX_CONCURRENT": config.max_concurrent_positions,
        "EVAL_SESSION_PROFIT_CAP": config.session_profit_cap,
        "EVAL_CONTINUE_AFTER_TARGET": config.continue_after_target,
        "OTR_EXECUTION_MODE": execution_mode,
    }
    policy_targets = {
        "a_plus_target_dollars": FULL_RISK_DOLLARS,
        "a_target_dollars": REDUCED_RISK_DOLLARS,
        "gold_momentum_pullback_72r_max_dollars": REDUCED_RISK_DOLLARS,
    }

    print(
        "Operation 8.1 STARTUP RISK ENVELOPE: "
        + ", ".join(f"{key}={value}" for key, value in envelope.items())
        + f", broker_armed={broker_armed}"
        + f", a_plus_target=${policy_targets['a_plus_target_dollars']:.2f}"
        + f", a_target=${policy_targets['a_target_dollars']:.2f}"
        + f", gold_momentum_pullback_72r_max=${policy_targets['gold_momentum_pullback_72r_max_dollars']:.2f}",
        flush=True,
    )

    warnings: list[str] = []
    if config.risk_per_trade < FULL_RISK_DOLLARS:
        warning = (
            f"EVAL_RISK_PER_TRADE=${config.risk_per_trade:.2f} caps the A+ setup below its "
            f"Operation 8.1 target of ${FULL_RISK_DOLLARS:.2f}."
        )
        warnings.append(warning)
        print(f"Operation 8.1 RISK ENVELOPE WARN: {warning}", flush=True)

    return {"envelope": envelope, "broker_armed": broker_armed, "policy_targets": policy_targets, "warnings": warnings}


def _startup_research_integrity_81() -> dict:
    """Print the active research run identity and paper-accounting cutover.

    Read-only. Lets a deploy be verified from logs alone: which run new
    trades are being tagged into, and whether whole-contract MGC accounting
    (vs. the legacy theoretical risk_dollars model) is active for this build.
    """
    connection = get_connection()
    try:
        run_id = current_run_id(connection)
    finally:
        connection.close()

    print(
        "Operation 8.1 RESEARCH INTEGRITY: "
        f"run_id={run_id}, engine_version={ENGINE_VERSION}, operation_version={OPERATION_VERSION}, "
        f"paper_accounting_version={PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1} (GC only; "
        "pre-migration rows keep their original theoretical accounting and are never rewritten).",
        flush=True,
    )
    return {
        "run_id": run_id,
        "engine_version": ENGINE_VERSION,
        "operation_version": OPERATION_VERSION,
        "paper_accounting_version": PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
    }


def main() -> None:
    # Apply the user-requested clean overnight scorecard before the inherited
    # supervisor creates the next run and starts the 8.1 strategy engine.
    reset_counts = _reset_active_replay_progress_81()

    try:
        _startup_risk_envelope_81()
    except Exception as exc:  # Diagnostics must never block startup.
        print(
            f"Operation 8.1 startup risk envelope diagnostic failed non-fatally: {type(exc).__name__}: {exc}",
            flush=True,
        )

    try:
        _startup_research_integrity_81()
    except Exception as exc:  # Diagnostics must never block startup.
        print(
            f"Operation 8.1 startup research integrity diagnostic failed non-fatally: {type(exc).__name__}: {exc}",
            flush=True,
        )

    # server_80 still owns the proven dashboard/API/UI setup. Replace only its
    # engine promotion hook so the same supervisor launches Operation 8.1, then
    # add the 8.1 candidate-to-fill conversion microscope alongside it.
    base._promote_engine_80 = _promote_engine_81
    _install_conversion_api_81()
    _install_nautilus_parity_api_81()
    _install_connection_fallback_81()
    install_vibe_research_routes()
    print(
        "Operation 8.1 supervisor: Operation 8.0 dashboard + Gold Execution Conversion engine; "
        "first-touch zones, registration-time entry life, dynamic R:R, $750/$500 eval sizing, "
        "detected->qualified->selected->registered->filled conversion telemetry, authenticated "
        "Nautilus replay parity diagnostics, and Vercel-safe full snapshot rendering fallback enabled; "
        f"overnight_reset_rows={sum(reset_counts.values())}.",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
