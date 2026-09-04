from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os

from src.dashboard import server_72q as base
from src.storage.database import get_connection, get_engine_state, set_engine_state


VERIFY_RUN_STATE_KEY_72S = "verify_test_run_id_72s"
VERIFY_WIPE_STATE_KEY_72S = "verify_test_wipe_token_72s"


def _stable_verify_run_id_72s() -> str:
    if not base.base._verification_enabled_72n():
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
    from src.dashboard.queries import TRADING_TZ
    from src.dashboard.queries_59 import DashboardRepository

    marker = "_otr_verify_calendar_contract_72s"
    if getattr(DashboardRepository, marker, False):
        return
    original_daily = DashboardRepository.daily_realized_pnl

    def verify_daily_realized_pnl(self, connection):
        if not base.base._verification_enabled_72n():
            return original_daily(self, connection)

        run_id = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
        if not run_id or not self._table_exists(connection, "verify_run_trades"):
            return []
        tagged = {
            str(row[0])
            for row in connection.execute(
                "SELECT setup_id FROM verify_run_trades WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        if not tagged:
            return []

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


def _promote_engine_72r() -> str:
    """Legacy helper retained for the 7.2R supervisor regression contract."""
    base.base.base.promoted_engine_module = lambda requested=None: "src.main_72r"
    return base.base.base.promoted_engine_module()


def _promote_engine_72s() -> str:
    # server_72r -> server_72q -> server_72n -> server_72
    base.base.base.promoted_engine_module = lambda requested=None: "src.main_72s"
    return base.base.base.promoted_engine_module()


def main() -> None:
    base._normalize_verify_environment_72q()
    base._wipe_verify_test_state_72q()
    run_id = _stable_verify_run_id_72s()
    _install_verify_calendar_contract_72s()
    engine_module = _promote_engine_72s()
    print(
        "Operation 7.2S compatibility supervisor: stable wipe-scoped VERIFY run + unified deduped ledger accounting; "
        f"engine={engine_module} verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.base.main()


if __name__ == "__main__":
    main()
