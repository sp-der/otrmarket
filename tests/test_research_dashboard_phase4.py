from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import asyncio
from types import SimpleNamespace
import tempfile
import unittest

from src.research.dashboard import ResearchDashboardRepository
from src.research.historical.schema import SCHEMA_SQL
from src.research.replay.schema import RUN_SCHEMA_SQL


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def research_stores(tmp_path):
    runs = tmp_path / "runs.db"
    historical = tmp_path / "historical.db"
    with sqlite3.connect(runs) as con:
        con.executescript(RUN_SCHEMA_SQL)
        con.execute(
            "INSERT INTO backtest_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "demo-run", None, "b6edc2b", "incomplete-capture", '["NQ","ES"]',
                '["MNQ SEP26","MES SEP26"]', "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z",
                '["1m","5m","15m","1h"]', '{}', json.dumps({
                    "profile": "LUCID_PRO_50K", "profile_verification": "RESEARCH_REFERENCE_PROFILE",
                    "starting_balance": 50000, "profit_target": 3000, "maximum_loss": 2000,
                    "trailing_loss_basis": "INTRADAY_EQUITY",
                }), "Operation 7.0", "CANDLE_APPROXIMATE", "SLIPPAGE_MODEL", "STOP_FIRST",
                json.dumps({"slippage_ticks": 1, "round_turn_commission": 1.5, "fees": 0.5}),
                '{"1m":15,"5m":8,"15m":4,"1h":2}', "2026-01-03T00:00:00Z", "COMPLETE",
                "digest", str(tmp_path / "ledger.db"),
            ),
        )
        trade = {
            "trade_id": "trade-1", "run_id": "demo-run", "setup_id": "setup-1", "symbol": "NQ",
            "execution_contract": "MNQ SEP26", "strategy_type": "ICT / OTE", "timeframe": "5m",
            "session": "New York", "direction": "bullish", "setup_grade": "A", "quality_score": 9,
            "recovery_state": "NORMAL", "signal_time": "2026-01-01T14:30:00Z", "pending_time": "2026-01-01T14:30:00Z",
            "fill_time": "2026-01-01T14:31:00Z", "exit_time": "2026-01-01T14:35:00Z",
            "planned_entry": 20000, "actual_fill": 20000.25, "stop_price": 19995, "target_price": 20010,
            "exit_fill": 20010, "planned_rr": 2, "actual_entry_rr": 1.86, "realized_r": 1.8,
            "requested_risk": 250, "allowed_risk": 150, "per_contract_risk": 10.5, "quantity": 14,
            "actual_risk": 147, "unused_risk": 3, "gross_pnl": 273, "commission": 21, "fees": 7,
            "slippage_cost": 7, "adverse_slippage_cost": 7, "price_improvement": 0, "gap_slippage": 0,
            "net_pnl": 245, "mfe_points": 10, "mfe_dollars": 280, "mfe_r": 1.9,
            "mae_points": 2, "mae_dollars": 56, "mae_r": .38, "excursion_quality": "CANDLE_APPROXIMATE",
            "entry_reason": "OTE touch", "exit_reason": "TARGET", "fill_model": "SLIPPAGE_MODEL",
            "replay_mode": "CANDLE_APPROXIMATE", "ambiguity_policy": "STOP_FIRST",
            "ambiguity_flags_json": "[]", "status": "CLOSED", "payload_json": '{}',
        }
        cols = list(trade)
        con.execute(f"INSERT INTO research_trades({','.join(cols)}) VALUES({','.join('?' for _ in cols)})", tuple(trade.values()))
        con.execute("INSERT INTO equity_curve(run_id,sequence_no,timestamp,event_type,balance,realized_pnl,unrealized_pnl,equity,peak_balance,peak_equity,realized_drawdown,equity_drawdown,intrabar_approximate_drawdown,drawdown_dollars,drawdown_percent,daily_pnl,session_pnl,daily_equity_low,session_equity_low,open_risk) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("demo-run", 1, "2026-01-01T14:32:00Z", "MARK", 50000, 0, -80, 49920, 50000, 50000, 0, 80, 100, 80, .16, 0, 0, 49920, 49920, 147))
        con.execute("INSERT INTO equity_curve(run_id,sequence_no,timestamp,event_type,balance,realized_pnl,unrealized_pnl,equity,peak_balance,peak_equity,realized_drawdown,equity_drawdown,intrabar_approximate_drawdown,drawdown_dollars,drawdown_percent,daily_pnl,session_pnl,daily_equity_low,session_equity_low,open_risk) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("demo-run", 2, "2026-01-01T14:35:00Z", "EXIT", 50245, 245, 0, 50245, 50245, 50245, 0, 0, 0, 0, 0, 245, 245, 49920, 49920, 0))
        trace_base = ("demo-run", 1, "2026-01-01T14:30:00Z", "SETUP_DETECTED", "NQ", "5m", "ICT / OTE", "bullish", "setup-1", "A")
        con.execute("INSERT INTO decision_traces(run_id,sequence_no,event_time,event_type,symbol,timeframe,strategy_type,direction,setup_id,setup_grade,catalyst_json,displacement_json,fvg_json,ote_json,smt_json,htf_context_json,session_json,recovery_json,quality_score,risk_reward,decision,reason,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*trace_base, '{"liquidity_sweep":true}', '{"ratio":1.7}', '{"valid":true}', '{"valid":true}', '{"confirmed":true}', '{"bias":"bullish"}', '{"name":"New York"}', '{"state":"NORMAL"}', 9, 2, "ACCEPTED", None, '{}'))
        con.execute("INSERT INTO decision_traces(run_id,sequence_no,event_time,event_type,symbol,timeframe,strategy_type,direction,setup_id,setup_grade,catalyst_json,displacement_json,fvg_json,ote_json,smt_json,htf_context_json,session_json,recovery_json,quality_score,risk_reward,decision,reason,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("demo-run", 2, "2026-01-01T15:00:00Z", "PENDING_EXPIRED", "ES", "15m", "ICT / OTE", "bearish", "setup-2", "B+", '{}','{}','{}','{}','{}','{}','{}','{}', 8, 1.2, "EXPIRED", "PENDING_EXPIRED", '{"bars_elapsed":4,"configured_max_bars":4,"structure_valid_at_expiration":"UNKNOWN"}'))
        con.execute("INSERT INTO risk_audits(run_id,setup_id,base_risk,evaluation_available_risk,session_multiplier,grade_multiplier,strategy_multiplier,entry_location_multiplier,lifetime_multiplier,recovery_multiplier,pacing_multiplier,final_allowed_risk,per_contract_risk,quantity,actual_risk,unused_risk,source,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("demo-run", "setup-1", 250, 500, 1, 1, 1, 1, 1, .6, 1, 150, 10.5, 14, 147, 3, "OPERATION_7_FINAL_ALLOWED_RISK_NO_REAPPLICATION", '{}'))
    with sqlite3.connect(historical) as con:
        con.executescript(SCHEMA_SQL)
        con.execute("INSERT INTO capture_sessions VALUES(?,?,?,?,?,?,?,?)", ("incomplete-capture", "IMPORT", "IMPORT", "2026-01-01", "2026-01-02", "2026-01-03", "retained", 1))
        con.execute("INSERT INTO instrument_roots VALUES(?,?,?,?,?,?)", ("MNQ", "NQ", "MICRO", .25, 2, .5))
        con.execute("INSERT INTO contracts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("MNQ SEP26", "MNQ", "NQ", "MICRO", "2026-09", "KNOWN", .25, 2, .5, None, None, None, None, "", "OTR"))
        con.execute("INSERT INTO historical_events(capture_id,sequence_no,root_symbol,contract,size_class,exchange_timestamp,last_price,bid,ask,volume,source,ingested_at,data_gap,integrity_status,source_event_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("incomplete-capture", 1, "NQ", "MNQ SEP26", "MICRO", "2026-01-01T00:00:00Z", 20000, None, None, 1, "IMPORT", "2026-01-03", 1, "GAPPED", "1"))
        con.execute("INSERT INTO canonical_candles(capture_id,contract,root_symbol,timeframe,open_time,close_time,open,high,low,close,volume,event_count,completeness_state,source_coverage,gap_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("incomplete-capture", "MNQ SEP26", "NQ", "1m", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", 20000, 20001, 19999, 20000, 1, 1, "INCOMPLETE", "PARTIAL", "GAP"))
        con.execute("INSERT INTO integrity_findings(capture_id,root_symbol,contract,timeframe,start_time,end_time,finding_type,severity,details,detected_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("incomplete-capture", "NQ", "MNQ SEP26", "1m", "2026-01-01", "2026-01-01", "MISSING_PERIOD", "WARNING", "retained import incomplete", "2026-01-03"))
    return runs, historical


def check_run_listing_detail_metrics_segments_and_empty_states(research_stores, tmp_path):
    repo = ResearchDashboardRepository(*research_stores)
    runs = repo.list_runs()
    assert runs[0]["run_id"] == "demo-run"
    assert runs[0]["not_valid_for_strategy_evaluation"] is True
    detail = repo.run_detail("demo-run")
    assert detail["metrics"]["net_pnl"] == 245
    assert detail["segments"]["symbol"]["NQ"]["total_trades"] == 1
    assert detail["segments"]["direction"]["bullish"]["profit_factor"] is None
    assert ResearchDashboardRepository(tmp_path / "missing.db", tmp_path / "missing-h.db").list_runs() == []


def check_equity_drawdown_trade_decision_block_expiration_risk_and_coverage(research_stores):
    repo = ResearchDashboardRepository(*research_stores)
    assert repo.equity("demo-run")[0]["equity_drawdown"] == 80
    assert len(repo.trades("demo-run", {"symbol": "NQ", "direction": "bullish"})) == 1
    assert repo.trades("demo-run", {"symbol": "GC"}) == []
    assert len(repo.decisions("demo-run", {"timeframe": "15m"})) == 1
    assert repo.blocked_setups("demo-run")[0]["reason"] == "PENDING_EXPIRED"
    assert repo.pending_expirations("demo-run")["by_timeframe"][0]["timeframe"] == "15m"
    assert repo.risk_audits("demo-run")[0]["no_reapplication"] is True
    coverage = repo.coverage("incomplete-capture")
    assert coverage["incomplete"] is True and coverage["warning"]
    assert coverage["rows"][0]["root"] == "NQ"
    assert coverage["history_status"] == "INSUFFICIENT_HISTORY_FOR_PHASE6"


def check_research_api_is_get_only_and_does_not_mutate_stores(research_stores):
    from src.dashboard import app as dashboard
    original_repository = dashboard.research_repository
    original_password = dashboard.DASHBOARD_PASSWORD
    dashboard.research_repository = ResearchDashboardRepository(*research_stores)
    dashboard.DASHBOARD_PASSWORD = ""
    before = [digest(path) for path in research_stores]
    try:
        request = SimpleNamespace(cookies={})
        assert "RESEARCH / BACKTEST LAB" in asyncio.run(dashboard.research_index()).body.decode()
        assert asyncio.run(dashboard.research_runs(request))["read_only"] is True
        assert asyncio.run(dashboard.research_run_detail("demo-run", request))["metrics"]["total_trades"] == 1
        assert asyncio.run(dashboard.research_trades("demo-run", request, market="NQ"))["items"]
        assert asyncio.run(dashboard.research_decisions("demo-run", request, timeframe="15m"))["items"]
        assert asyncio.run(dashboard.research_blocked("demo-run", request))["items"]
        assert asyncio.run(dashboard.research_pending_expirations("demo-run", request))["items"]
        assert asyncio.run(dashboard.research_risk_audits("demo-run", request))["items"]
        assert asyncio.run(dashboard.research_coverage(request, "incomplete-capture"))["warning"]
        research_routes = [route for route in dashboard.app.routes if getattr(route, "path", "").startswith("/market/api/research")]
        assert research_routes and all(route.methods == {"GET"} for route in research_routes)
        assert before == [digest(path) for path in research_stores]
    finally:
        dashboard.research_repository = original_repository
        dashboard.DASHBOARD_PASSWORD = original_password


def check_strategy_lab_static_surface_contains_required_views():
    root = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "static"
    html = (root / "research.html").read_text()
    script = (root / "research.js").read_text()
    for label in ("Backtests", "Run Detail", "Trades", "Decisions", "Blocked Setups", "Data Coverage", "RESEARCH / BACKTEST LAB"):
        assert label in html
    for feature in ("Long vs Short", "Pending Expirations", "Risk Audit", "Recovery Timeline", "Ambiguity policy", "NOT VALID FOR STRATEGY EVALUATION"):
        assert feature in html + script


class Phase4ResearchDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.stores = research_stores(self.root)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_run_listing_detail_metrics_segments_and_empty_states(self):
        check_run_listing_detail_metrics_segments_and_empty_states(self.stores, self.root)

    def test_equity_drawdown_trade_decision_block_expiration_risk_and_coverage(self):
        check_equity_drawdown_trade_decision_block_expiration_risk_and_coverage(self.stores)

    def test_research_api_is_get_only_and_does_not_mutate_stores(self):
        check_research_api_is_get_only_and_does_not_mutate_stores(self.stores)

    def test_strategy_lab_static_surface_contains_required_views(self):
        check_strategy_lab_static_surface_contains_required_views()


if __name__ == "__main__":
    unittest.main()
