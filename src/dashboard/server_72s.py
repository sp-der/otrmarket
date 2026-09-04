from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os

from src.dashboard import server_72r as base
from src.storage.database import get_connection, get_engine_state, set_engine_state


VERIFY_RUN_STATE_KEY_72S = "verify_test_run_id_72s"
VERIFY_WIPE_STATE_KEY_72S = "verify_test_wipe_token_72s"
VERIFY_MODES_72S = {"VERIFY", "VERIFICATION", "TEST"}


def _verification_enabled_72s() -> bool:
    return os.getenv("OTR_TRADING_MODE", "").strip().upper() in VERIFY_MODES_72S


def _stable_verify_run_id_72s() -> str:
    """Keep one test-run ID across deployments until the next VERIFY wipe.

    Operation 7.2Q/R used Railway deployment IDs as run IDs. That split one
    continuous replay test every time code was deployed, so Overview could omit
    trades that remained visible in Trade History. The wipe token is the real
    experiment boundary; deployments are only build changes inside that test.
    """
    if not _verification_enabled_72s():
        return ""

    wipe_token = os.getenv("OTR_VERIFY_WIPE_TOKEN", "").strip()
    connection = get_connection()
    try:
        saved_run = get_engine_state(connection, VERIFY_RUN_STATE_KEY_72S, "") or ""
        saved_wipe = get_engine_state(connection, VERIFY_WIPE_STATE_KEY_72S, "") or ""

        if wipe_token and (wipe_token != saved_wipe or not saved_run):
            digest = hashlib.sha256(wipe_token.encode("utf-8")).hexdigest()[:12]
            run_id = f"VERIFY-{digest}"
            set_engine_state(connection, VERIFY_RUN_STATE_KEY_72S, run_id)
            set_engine_state(connection, VERIFY_WIPE_STATE_KEY_72S, wipe_token)
        elif saved_run:
            run_id = saved_run
        else:
            deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
            token = deployment[:12] if deployment else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = f"VERIFY-{token}"
            set_engine_state(connection, VERIFY_RUN_STATE_KEY_72S, run_id)
            if wipe_token:
                set_engine_state(connection, VERIFY_WIPE_STATE_KEY_72S, wipe_token)
    finally:
        connection.close()

    os.environ["OTR_VERIFY_RUN_ID"] = run_id
    return run_id


def _install_verify_calendar_contract_72s() -> None:
    """Make the calendar use the exact same logical current-run trade ledger."""
    from src.dashboard.queries import TRADING_TZ
    from src.dashboard.queries_59 import DashboardRepository

    marker = "_otr_verify_calendar_contract_72s"
    if getattr(DashboardRepository, marker, False):
        return

    original_daily = DashboardRepository.daily_realized_pnl

    def verify_daily_realized_pnl(self, connection):
        if not _verification_enabled_72s():
            return original_daily(self, connection)

        run_id = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
        if not run_id or not self._table_exists(connection, "verify_run_trades"):
            return []

        tagged_rows = connection.execute(
            "SELECT setup_id FROM verify_run_trades WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        tagged = {str(row[0]) for row in tagged_rows}
        if not tagged:
            return []

        # server_72n expands + deduplicates recent_trades. Calling it here keeps
        # the calendar, Trade History, and VERIFY totals on one logical ledger.
        trades = [
            trade
            for trade in self.recent_trades(connection, limit=1_000_000)
            if str(trade.get("setup_id") or "") in tagged
            and str(trade.get("status") or "").upper() == "CLOSED"
            and str(trade.get("result") or "").upper() in {"WIN", "LOSS"}
            and trade.get("closed_at")
        ]

        days = {}
        for trade in trades:
            closed_at = self._parse_time(trade.get("closed_at"))
            if closed_at is None:
                continue
            key = closed_at.astimezone(TRADING_TZ).date().isoformat()
            bucket = days.setdefault(
                key,
                {"date": key, "pnl": 0.0, "closed": 0, "wins": 0, "losses": 0},
            )
            bucket["pnl"] += float(trade.get("display_result_dollars") or 0.0)
            bucket["closed"] += 1
            result = str(trade.get("result") or "").upper()
            bucket["wins"] += int(result == "WIN")
            bucket["losses"] += int(result == "LOSS")

        return [days[key] for key in sorted(days)]

    DashboardRepository.daily_realized_pnl = verify_daily_realized_pnl
    setattr(DashboardRepository, marker, True)


def _promote_engine_72s() -> str:
    # server_72s -> 72r -> 72q -> 72n -> 72
    base.base.base.base.promoted_engine_module = lambda requested=None: "src.main_72s"
    return base.base.base.base.promoted_engine_module()


def main() -> None:
    base.base._normalize_verify_environment_72q()
    base.base._wipe_verify_test_state_72q()
    run_id = _stable_verify_run_id_72s()
    _install_verify_calendar_contract_72s()
    engine_module = _promote_engine_72s()
    print(
        "Operation 7.2S supervisor: stable wipe-scoped VERIFY run + unified deduped ledger accounting; "
        f"engine={engine_module} verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.base.base.main()


if __name__ == "__main__":
    main()
