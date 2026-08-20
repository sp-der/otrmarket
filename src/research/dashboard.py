from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from src.research.execution.metrics import raw_metrics
from src.research.historical.coverage import coverage_rows


def _json(value: Any, fallback: Any = None) -> Any:
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {} if fallback is None else fallback


def _finite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


class ResearchDashboardRepository:
    """Read-only projection of immutable Backtest Lab stores.

    Connections use SQLite's ``mode=ro`` URI so a dashboard request cannot
    create a database, journal, table, trigger, or production-side mutation.
    """

    MANIFEST_JSON = {
        "markets_json": "markets",
        "contracts_json": "contracts",
        "timeframes_json": "enabled_timeframes",
        "configuration_json": "configuration",
        "account_profile_json": "account_profile",
        "execution_config_json": "execution_config",
        "pending_lifetime_bars_json": "pending_lifetime_bars",
    }
    TRACE_JSON = {
        "catalyst_json": "catalyst",
        "displacement_json": "displacement",
        "fvg_json": "fvg",
        "ote_json": "ote",
        "smt_json": "smt",
        "htf_context_json": "htf_context",
        "session_json": "session_detail",
        "recovery_json": "recovery_detail",
        "payload_json": "payload",
    }

    def __init__(self, runs_database: str | Path, historical_database: str | Path):
        self.runs_database = Path(runs_database).resolve()
        self.historical_database = Path(historical_database).resolve()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        if not path.is_file():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _rows(connection: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def _manifest(self, row: sqlite3.Row | dict) -> dict:
        item = dict(row)
        for source, target in self.MANIFEST_JSON.items():
            item[target] = _json(item.pop(source, None), [] if target in {"markets", "contracts", "enabled_timeframes"} else {})
        profile = item.get("account_profile") or {}
        item["profile_verification"] = profile.get("profile_verification", "UNKNOWN")
        item["not_valid_for_strategy_evaluation"] = self._capture_incomplete(item.get("capture_id"))
        return _finite(item)

    def _capture_incomplete(self, capture_id: str | None) -> bool:
        if not capture_id or not self.historical_database.is_file():
            return True
        try:
            with self._connect(self.historical_database) as connection:
                incomplete = connection.execute(
                    "SELECT COUNT(*) FROM canonical_candles WHERE capture_id=? AND (completeness_state!='COMPLETE' OR gap_state!='NONE')",
                    (capture_id,),
                ).fetchone()[0]
                findings = connection.execute(
                    "SELECT COUNT(*) FROM integrity_findings WHERE capture_id=? AND severity IN ('WARNING','ERROR')",
                    (capture_id,),
                ).fetchone()[0]
                return bool(incomplete or findings)
        except sqlite3.Error:
            return True

    def list_runs(self) -> list[dict]:
        if not self.runs_database.is_file():
            return []
        with self._connect(self.runs_database) as connection:
            if not self._exists(connection, "backtest_runs"):
                return []
            runs = connection.execute("SELECT * FROM backtest_runs ORDER BY created_at DESC").fetchall()
            result = []
            for row in runs:
                manifest = self._manifest(row)
                trades = self._trade_rows(connection, manifest["run_id"])
                equity = self._equity_rows(connection, manifest["run_id"])
                metrics, _ = raw_metrics(trades, equity)
                manifest["metrics"] = _finite(metrics)
                result.append(manifest)
            return result

    def run_detail(self, run_id: str) -> dict | None:
        if not self.runs_database.is_file():
            return None
        with self._connect(self.runs_database) as connection:
            row = connection.execute("SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                return None
            manifest = self._manifest(row)
            trades = self._trade_rows(connection, run_id)
            equity = self._equity_rows(connection, run_id)
            metrics, segments = raw_metrics(trades, equity)
            profile = manifest.get("account_profile") or {}
            start = float(profile.get("starting_balance") or 0)
            end = float(equity[-1]["balance"]) if equity else start
            closed = [trade for trade in trades if trade.get("status") == "CLOSED"]
            costs = {
                "commissions": sum(float(t.get("commission") or 0) for t in closed),
                "fees": sum(float(t.get("fees") or 0) for t in closed),
                "adverse_slippage": sum(float(t.get("adverse_slippage_cost") or 0) for t in closed),
                "price_improvement": sum(float(t.get("price_improvement") or 0) for t in closed),
            }
            metrics["return_percent"] = (100 * (end - start) / start) if start else None
            return _finite({
                "manifest": manifest,
                "metrics": metrics,
                "segments": segments,
                "costs": costs,
                "account_status": self._account_status(profile, equity, trades),
                "counts": {
                    "trades": len(trades),
                    "decisions": self._count(connection, "decision_traces", run_id),
                    "blocks": self._count(connection, "account_blocks", run_id),
                },
            })

    @staticmethod
    def _count(connection: sqlite3.Connection, table: str, run_id: str) -> int:
        if not ResearchDashboardRepository._exists(connection, table):
            return 0
        return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0])

    def _trade_rows(self, connection: sqlite3.Connection, run_id: str) -> list[dict]:
        if not self._exists(connection, "research_trades"):
            return []
        rows = self._rows(connection, "SELECT * FROM research_trades WHERE run_id=? ORDER BY signal_time,trade_id", (run_id,))
        for row in rows:
            row["ambiguity_flags"] = _json(row.pop("ambiguity_flags_json", None), [])
            row["payload"] = _json(row.pop("payload_json", None), {})
            row["costs"] = float(row.get("commission") or 0) + float(row.get("fees") or 0)
        return _finite(rows)

    def trades(self, run_id: str, filters: dict[str, str] | None = None) -> list[dict]:
        with self._connect(self.runs_database) as connection:
            rows = self._trade_rows(connection, run_id)
        for key, value in (filters or {}).items():
            if value and value.lower() != "all":
                if key == "result":
                    rows = [row for row in rows if ("WIN" if (row.get("net_pnl") or 0) > 0 else "LOSS" if (row.get("net_pnl") or 0) < 0 else row.get("status")) == value]
                else:
                    rows = [row for row in rows if str(row.get(key) or "UNKNOWN") == value]
        return rows

    def trade_detail(self, run_id: str, trade_id: str) -> dict | None:
        with self._connect(self.runs_database) as connection:
            trades = [row for row in self._trade_rows(connection, run_id) if row["trade_id"] == trade_id]
            if not trades:
                return None
            trade = trades[0]
            setup_id = trade.get("setup_id")
            traces = self._trace_rows(connection, run_id, "setup_id=?", (setup_id,)) if setup_id else []
            audits = self._rows(connection, "SELECT * FROM risk_audits WHERE run_id=? AND setup_id=? ORDER BY audit_id", (run_id, setup_id)) if self._exists(connection, "risk_audits") else []
            for audit in audits:
                audit["payload"] = _json(audit.pop("payload_json", None), {})
            return _finite({"trade": trade, "reasoning": traces, "risk_audits": audits})

    def _trace_rows(self, connection: sqlite3.Connection, run_id: str, extra: str = "", args: tuple = ()) -> list[dict]:
        if not self._exists(connection, "decision_traces"):
            return []
        clause = f" AND {extra}" if extra else ""
        rows = self._rows(connection, f"SELECT * FROM decision_traces WHERE run_id=?{clause} ORDER BY sequence_no", (run_id, *args))
        for row in rows:
            for source, target in self.TRACE_JSON.items():
                row[target] = _json(row.pop(source, None), {})
        return _finite(rows)

    def decisions(self, run_id: str, filters: dict[str, str] | None = None) -> list[dict]:
        with self._connect(self.runs_database) as connection:
            rows = self._trace_rows(connection, run_id)
        aliases = {"grade": "setup_grade", "symbol": "symbol"}
        for key, value in (filters or {}).items():
            if value and value.lower() != "all":
                field = aliases.get(key, key)
                rows = [row for row in rows if str(row.get(field) or "UNKNOWN") == value]
        return rows

    def blocked_setups(self, run_id: str) -> list[dict]:
        blocked_words = ("BLOCK", "CANCEL", "EXPIRE", "SUPPRESS", "INVALID", "STALE")
        result = []
        for row in self.decisions(run_id):
            state = f"{row.get('decision', '')} {row.get('event_type', '')}".upper()
            if any(word in state for word in blocked_words):
                result.append(row)
        return result

    def pending_expirations(self, run_id: str) -> dict:
        rows = [row for row in self.blocked_setups(run_id) if "EXPIRE" in f"{row.get('decision','')} {row.get('event_type','')} {row.get('reason','')}".upper()]
        groups: dict[str, dict] = {}
        for row in rows:
            timeframe = row.get("timeframe") or "UNKNOWN"
            group = groups.setdefault(timeframe, {"timeframe": timeframe, "count": 0, "structure_valid": 0, "unknown": 0})
            group["count"] += 1
            payload = row.get("payload") or {}
            state = payload.get("structure_valid_at_expiration", "UNKNOWN")
            group["structure_valid"] += int(state is True or state == "TRUE")
            group["unknown"] += int(state in (None, "UNKNOWN"))
        return {"items": rows, "by_timeframe": list(groups.values())}

    def risk_audits(self, run_id: str) -> list[dict]:
        with self._connect(self.runs_database) as connection:
            if not self._exists(connection, "risk_audits"):
                return []
            rows = self._rows(connection, "SELECT * FROM risk_audits WHERE run_id=? ORDER BY audit_id", (run_id,))
        for row in rows:
            row["payload"] = _json(row.pop("payload_json", None), {})
            row["no_reapplication"] = row.get("source") == "OPERATION_7_FINAL_ALLOWED_RISK_NO_REAPPLICATION"
        return _finite(rows)

    def recovery_timeline(self, run_id: str) -> list[dict]:
        rows = self.decisions(run_id)
        return [row for row in rows if row.get("recovery_detail") or "RECOVERY" in f"{row.get('event_type','')} {row.get('decision','')} {row.get('reason','')}".upper()]

    def _equity_rows(self, connection: sqlite3.Connection, run_id: str) -> list[dict]:
        if not self._exists(connection, "equity_curve"):
            return []
        return _finite(self._rows(connection, "SELECT * FROM equity_curve WHERE run_id=? ORDER BY sequence_no", (run_id,)))

    def equity(self, run_id: str) -> list[dict]:
        with self._connect(self.runs_database) as connection:
            return self._equity_rows(connection, run_id)

    @staticmethod
    def _account_status(profile: dict, equity: list[dict], trades: list[dict]) -> dict:
        last = equity[-1] if equity else {}
        return {
            "starting_balance": profile.get("starting_balance"),
            "ending_balance": last.get("balance", profile.get("starting_balance")),
            "profit_target": profile.get("profit_target"),
            "maximum_loss": profile.get("maximum_loss"),
            "trailing_loss_amount": profile.get("trailing_loss_amount"),
            "trailing_loss_basis": profile.get("trailing_loss_basis", "UNKNOWN"),
            "peak_equity": last.get("peak_equity"),
            "open_risk": last.get("open_risk", 0),
            "session_pnl": last.get("session_pnl", 0),
            "daily_pnl": last.get("daily_pnl", 0),
            "trade_count": len([row for row in trades if row.get("fill_time")]),
            "consecutive_losses": None,
            "profile_verification": profile.get("profile_verification", "UNKNOWN"),
        }

    def coverage(self, capture_id: str | None = None) -> dict:
        if not self.historical_database.is_file():
            return {"rows": [], "findings": [], "warning": "Historical research store is unavailable."}
        with self._connect(self.historical_database) as connection:
            rows = coverage_rows(connection, capture_id)
            args = (capture_id,) if capture_id else ()
            where = "WHERE capture_id=?" if capture_id else ""
            findings = self._rows(connection, f"SELECT * FROM integrity_findings {where} ORDER BY detected_at,finding_id", args) if self._exists(connection, "integrity_findings") else []
        incomplete = not rows or any(row["coverage_percentage"] < 100 or row["gaps"] for row in rows) or bool(findings)
        return _finite({
            "rows": rows,
            "findings": findings,
            "incomplete": incomplete,
            "warning": "INCOMPLETE RETAINED DATA — NOT VALID FOR STRATEGY EVALUATION" if incomplete else None,
        })
