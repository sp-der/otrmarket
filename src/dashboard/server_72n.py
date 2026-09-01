from __future__ import annotations

import json
import os
from pathlib import Path
import runpy

from src.dashboard import server_72 as base


VERIFY_MODES_72N = {"VERIFY", "VERIFICATION", "TEST"}
FULL_TRADE_WINDOW_72N = 5_000
_VERIFY_RUN_BASELINE_ROWID_72N: int | None = None
_VERIFY_RUN_STARTED_MARKET_TIME_72N: str | None = None
_VERIFY_RUN_ENGINE_72N: str | None = None


def _verification_enabled_72n() -> bool:
    return os.getenv("OTR_TRADING_MODE", "").strip().upper() in VERIFY_MODES_72N


def _verify_run_id_72n() -> str:
    return os.getenv("OTR_VERIFY_RUN_ID", "").strip()


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


def _reset_verify_run_state_72n() -> None:
    global _VERIFY_RUN_BASELINE_ROWID_72N
    global _VERIFY_RUN_STARTED_MARKET_TIME_72N
    global _VERIFY_RUN_ENGINE_72N
    _VERIFY_RUN_BASELINE_ROWID_72N = None
    _VERIFY_RUN_STARTED_MARKET_TIME_72N = None
    _VERIFY_RUN_ENGINE_72N = None


def _verify_build_label_72n(engine_module: str) -> str:
    token = str(engine_module or "").rsplit("_", 1)[-1].upper()
    if token.startswith("72"):
        return f"7.2{token[2:]}"
    return token or "VERIFY"


def _ensure_verify_run_boundary_72n(self, connection, runtime: dict) -> tuple[int, str | None, str]:
    """Keep rowid as a dev fallback; production VERIFY uses explicit run tags."""
    global _VERIFY_RUN_BASELINE_ROWID_72N
    global _VERIFY_RUN_STARTED_MARKET_TIME_72N
    global _VERIFY_RUN_ENGINE_72N

    if _VERIFY_RUN_BASELINE_ROWID_72N is None:
        if self._table_exists(connection, "paper_trades"):
            row = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM paper_trades").fetchone()
            _VERIFY_RUN_BASELINE_ROWID_72N = int(row[0] if row else 0)
        else:
            _VERIFY_RUN_BASELINE_ROWID_72N = 0
        _VERIFY_RUN_ENGINE_72N = os.getenv("OTR_ENGINE_MODULE", "src.main_72n")
        print(
            "VERIFY RUN 7.2 boundary captured: "
            f"run_id={_verify_run_id_72n() or 'rowid-fallback'} "
            f"baseline_rowid={_VERIFY_RUN_BASELINE_ROWID_72N} "
            f"engine={_VERIFY_RUN_ENGINE_72N}",
            flush=True,
        )

    market_time = runtime.get("market_time")
    if _VERIFY_RUN_STARTED_MARKET_TIME_72N is None and market_time:
        _VERIFY_RUN_STARTED_MARKET_TIME_72N = market_time

    return (
        int(_VERIFY_RUN_BASELINE_ROWID_72N or 0),
        _VERIFY_RUN_STARTED_MARKET_TIME_72N,
        str(_VERIFY_RUN_ENGINE_72N or "src.main_72n"),
    )


def _verify_run_snapshot_72n(self, connection, runtime: dict) -> dict:
    baseline, started_market_time, engine_module = _ensure_verify_run_boundary_72n(self, connection, runtime)
    build = _verify_build_label_72n(engine_module)
    tagged_run_id = _verify_run_id_72n()
    defaults = {
        "run_id": tagged_run_id or f"{build}-ROW-{baseline}",
        "build": build,
        "engine_module": engine_module,
        "baseline_rowid": baseline,
        "started_market_time": started_market_time,
        "accepted": 0,
        "closed": 0,
        "wins": 0,
        "losses": 0,
        "pending": 0,
        "open": 0,
        "win_rate": None,
        "total_r": 0.0,
        "avg_r": None,
        "profit_factor": None,
        "max_drawdown_r": 0.0,
        "max_drawdown_dollars": 0.0,
        "total_dollars": 0.0,
        "today_r": 0.0,
        "today_dollars": 0.0,
        "strategy_breakdown": {},
    }
    if not self._table_exists(connection, "paper_trades"):
        return defaults

    # A production VERIFY deployment always gets an explicit run ID from
    # server_72q. Until the engine tags its first trade, report a clean 0/0
    # instead of falling back to historical rows.
    if tagged_run_id and not self._table_exists(connection, "verify_run_trades"):
        return defaults

    risk_expr = "p.risk_dollars" if self._column_exists(connection, "paper_trades", "risk_dollars") else "NULL AS risk_dollars"
    dollars_expr = "p.result_dollars" if self._column_exists(connection, "paper_trades", "result_dollars") else "NULL AS result_dollars"
    guard_expr = "p.guard_reason" if self._column_exists(connection, "paper_trades", "guard_reason") else "NULL AS guard_reason"
    has_setups = self._table_exists(connection, "strategy_setups")
    setup_join = "LEFT JOIN strategy_setups s ON s.setup_id = p.setup_id" if has_setups else ""
    payload_expr = "s.payload_json AS payload_json" if has_setups else "NULL AS payload_json"

    if tagged_run_id:
        run_join = "JOIN verify_run_trades vrt ON vrt.setup_id = p.setup_id AND vrt.run_id = ?"
        where_clause = "1 = 1"
        params = (tagged_run_id,)
    else:
        run_join = ""
        where_clause = "p.rowid > ?"
        params = (baseline,)

    rows = connection.execute(
        f"""
        SELECT p.rowid AS run_rowid, p.setup_id, p.symbol, p.timeframe, p.direction, p.status,
               p.entry_price, p.stop_price, p.target_price, p.opened_at, p.closed_at,
               p.exit_price, p.result, p.result_r, {risk_expr}, {dollars_expr}, {guard_expr},
               p.updated_at, {payload_expr}
        FROM paper_trades p
        {run_join}
        {setup_join}
        WHERE {where_clause}
        ORDER BY p.rowid ASC
        """,
        params,
    ).fetchall()

    fallback_risk = _verify_risk_72n()
    prepared: list[dict] = []
    for row in rows:
        trade = dict(row)
        trade["display_result_dollars"] = self._display_result_dollars(row, fallback_risk)
        strategy = "UNKNOWN"
        payload_raw = trade.pop("payload_json", None)
        if payload_raw:
            try:
                payload = json.loads(payload_raw)
            except (TypeError, json.JSONDecodeError):
                payload = {}
            metadata = payload.get("metadata") if isinstance(payload, dict) else {}
            if isinstance(metadata, dict):
                strategy = str(metadata.get("strategy") or "ICT_CONFLUENCE").upper()
        trade["strategy"] = strategy
        prepared.append(trade)

    logical = _dedupe_trade_rows_72n(prepared)
    accepted = [trade for trade in logical if str(trade.get("status") or "").upper() in {"PENDING", "OPEN", "CLOSED"}]
    closed = [
        trade for trade in accepted
        if str(trade.get("status") or "").upper() == "CLOSED" and trade.get("result_r") is not None
    ]
    wins = sum(1 for trade in closed if str(trade.get("result") or "").upper() == "WIN")
    losses = sum(1 for trade in closed if str(trade.get("result") or "").upper() == "LOSS")
    total_r = sum(float(trade.get("result_r") or 0.0) for trade in closed)
    total_dollars = sum(float(trade.get("display_result_dollars") or 0.0) for trade in closed)
    positive_r = sum(float(trade.get("result_r") or 0.0) for trade in closed if float(trade.get("result_r") or 0.0) > 0)
    negative_r = abs(sum(float(trade.get("result_r") or 0.0) for trade in closed if float(trade.get("result_r") or 0.0) < 0))

    running_r = peak_r = max_dd_r = 0.0
    running_dollars = peak_dollars = max_dd_dollars = 0.0
    for trade in closed:
        running_r += float(trade.get("result_r") or 0.0)
        peak_r = max(peak_r, running_r)
        max_dd_r = max(max_dd_r, peak_r - running_r)
        running_dollars += float(trade.get("display_result_dollars") or 0.0)
        peak_dollars = max(peak_dollars, running_dollars)
        max_dd_dollars = max(max_dd_dollars, peak_dollars - running_dollars)

    strategy_breakdown: dict[str, int] = {}
    for trade in accepted:
        strategy = str(trade.get("strategy") or "UNKNOWN")
        strategy_breakdown[strategy] = strategy_breakdown.get(strategy, 0) + 1

    defaults.update(
        accepted=len(accepted),
        closed=len(closed),
        wins=wins,
        losses=losses,
        pending=sum(1 for trade in accepted if str(trade.get("status") or "").upper() == "PENDING"),
        open=sum(1 for trade in accepted if str(trade.get("status") or "").upper() == "OPEN"),
        win_rate=(wins / len(closed) * 100.0) if closed else None,
        total_r=round(total_r, 4),
        avg_r=(total_r / len(closed)) if closed else None,
        profit_factor=(positive_r / negative_r) if negative_r > 0 else (None if positive_r == 0 else positive_r),
        max_drawdown_r=round(max_dd_r, 4),
        max_drawdown_dollars=round(max_dd_dollars, 2),
        total_dollars=round(total_dollars, 2),
        today_r=round(total_r, 4),
        today_dollars=round(total_dollars, 2),
        strategy_breakdown=strategy_breakdown,
    )
    return defaults


def _install_dashboard_contract_72n() -> None:
    from src.dashboard.queries import DashboardRepository

    marker = "_otr_dashboard_contract_72n"
    if getattr(DashboardRepository, marker, False):
        return

    original_recent_trades = DashboardRepository.recent_trades
    original_evaluation_snapshot = DashboardRepository.evaluation_snapshot

    def expanded_recent_trades(self, connection, limit=30):
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
        run_stats = _verify_run_snapshot_72n(self, connection, runtime)
        run_pnl = float(run_stats.get("total_dollars") or 0.0)
        starting_balance = float(snapshot.get("starting_balance") or 50_000.0)
        snapshot.update(
            profile="OTR_CONTINUOUS_VERIFY_7_2N",
            phase="VERIFY",
            status="VERIFY",
            reason=(
                "Continuous verification is active. Dashboard performance is scoped to this tagged VERIFY run; "
                "all historical trades remain preserved in Trade History."
            ),
            balance=round(starting_balance + run_pnl, 2),
            realized_pnl=round(run_pnl, 2),
            today_pnl=round(run_pnl, 2),
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
            verify_run=run_stats,
        )
        return snapshot

    DashboardRepository.recent_trades = expanded_recent_trades
    DashboardRepository.evaluation_snapshot = verification_aware_evaluation_snapshot
    setattr(DashboardRepository, marker, True)
    print(
        f"Operation 7.2N dashboard: snapshot trade window expanded to {FULL_TRADE_WINDOW_72N}; "
        "replay duplicate fingerprinting + legacy P/L normalization + tagged run-scoped VERIFY stats active; "
        f"trading_mode={'VERIFY' if _verification_enabled_72n() else os.getenv('OTR_TRADING_MODE', 'EVAL')}",
        flush=True,
    )


def _patch_verification_asset_72n() -> None:
    static_dir = Path(__file__).resolve().parent / "static"
    path = static_dir / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    script_tag = '<script src="/market/assets/verification-mode72.js?v=7.2q-run2" defer></script>'
    if script_tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{script_tag}\n</body>", 1)
        path.write_text(text, encoding="utf-8")


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = base.promoted_engine_module()
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
