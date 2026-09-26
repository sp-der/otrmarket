from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.execution.paper import (
    PaperCostModel,
    cap_from_default,
    default_max_micros_cap,
    CANNOT_SIZE_RESULT,
    PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
    PaperExecutor,
    size_whole_contract,
)
from src.strategies.models import Displacement, FairValueGap, StrategySetup


def _gc_setup(*, entry: float, stop: float, target: float, direction: str = "bullish", setup_id: str = "gc-1"):
    t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
    fvg = FairValueGap("GC", "5m", direction, entry, entry, t, t, t)
    displacement = Displacement("GC", "5m", direction, t, entry, entry, 2, 2)
    risk = abs(target - entry) / abs(entry - stop)
    return StrategySetup(
        setup_id, "GC", "5m", direction, t, fvg, "liquidity_sweep", {},
        displacement, fvg, entry, stop, target, risk,
    )


def _nq_setup(setup_id: str = "nq-1"):
    t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
    fvg = FairValueGap("NQ", "5m", "bullish", 30000.0, 30000.0, t, t, t)
    displacement = Displacement("NQ", "5m", "bullish", t, 30000.0, 30000.0, 2, 2)
    return StrategySetup(
        setup_id, "NQ", "5m", "bullish", t, fvg, "liquidity_sweep", {},
        displacement, fvg, 30000.0, 29990.0, 30020.0, 2.0,
    )


class WholeContractSizingTests(unittest.TestCase):
    """All tests pass an explicit max_micros_cap so results are independent
    of whatever OTR_EXECUTION_MAX_MICROS happens to be set to in the
    environment running the suite. Cap-specific behavior has its own class
    below.
    """

    def test_750_requested_risk_produces_correct_quantity_and_actual_risk(self):
        setup = _gc_setup(entry=3500.0, stop=3496.7, target=3520.0)  # 3.3pt stop -> $33/contract
        sizing = size_whole_contract(setup, 750.0, max_micros_cap=100)

        self.assertTrue(sizing.sizeable)
        self.assertEqual(sizing.quantity, 22)
        self.assertAlmostEqual(sizing.per_contract_risk, 33.0, places=6)
        self.assertAlmostEqual(sizing.actual_risk_dollars, 726.0, places=6)
        self.assertLessEqual(sizing.actual_risk_dollars, 750.0)
        self.assertEqual(sizing.contract_multiplier, 10.0)
        self.assertEqual(sizing.execution_contract, "MGC")
        self.assertAlmostEqual(sizing.unused_risk_dollars, 24.0, places=6)

    def test_500_requested_risk_produces_correct_floor_quantity(self):
        setup = _gc_setup(entry=3500.0, stop=3496.7, target=3520.0)
        sizing = size_whole_contract(setup, 500.0, max_micros_cap=100)

        self.assertTrue(sizing.sizeable)
        self.assertEqual(sizing.quantity, 15)
        self.assertAlmostEqual(sizing.actual_risk_dollars, 495.0, places=6)
        self.assertLessEqual(sizing.actual_risk_dollars, 500.0)

    def test_actual_risk_never_exceeds_requested_risk(self):
        for requested in (50.0, 123.0, 499.99, 750.0, 10_000.0):
            setup = _gc_setup(entry=3500.0, stop=3496.7, target=3520.0)
            sizing = size_whole_contract(setup, requested, max_micros_cap=1000)
            if sizing.sizeable:
                self.assertLessEqual(sizing.actual_risk_dollars, requested)

    def test_insufficient_budget_for_one_mgc_yields_cannot_size(self):
        # 1000pt stop distance -> $10,000/contract; $750 cannot fund even 1.
        setup = _gc_setup(entry=3500.0, stop=2500.0, target=4500.0)
        sizing = size_whole_contract(setup, 750.0, max_micros_cap=100)

        self.assertFalse(sizing.sizeable)
        self.assertEqual(sizing.quantity, 0)
        self.assertEqual(sizing.actual_risk_dollars, 0.0)

    def test_no_forced_minimum_of_one_contract(self):
        setup = _gc_setup(entry=3500.0, stop=2500.0, target=4500.0)
        executor = PaperExecutor()

        position = executor.register_setup(setup, risk_dollars=750.0, max_micros_cap=100)

        self.assertEqual(position.quantity, 0)
        self.assertNotEqual(position.quantity, 1)
        self.assertEqual(position.status, "INVALIDATED")
        self.assertEqual(position.result, CANNOT_SIZE_RESULT)
        self.assertEqual(position.result_dollars, 0.0)
        self.assertNotIn(setup.setup_id, executor.positions)
        self.assertIn(position, executor.closed)


class MaxMicrosCapTests(unittest.TestCase):
    """Regression coverage for the paper-sizing / live-sizing cap mismatch:
    an A+ GC 1m setup (entry 4322.5, stop 4321.6, requested $750) produced 83
    uncapped MGC contracts in paper while live sizing would have obeyed
    OTR_EXECUTION_MAX_MICROS. Paper sizing must respect the same cap.
    """

    def test_cap_below_uncapped_quantity_reduces_quantity_without_forcing_zero(self):
        # 0.9pt stop -> $9/contract; $750 uncapped would floor to 83 contracts.
        setup = _gc_setup(entry=4322.5, stop=4321.6, target=4326.7)
        sizing = size_whole_contract(setup, 750.0, max_micros_cap=10)

        self.assertTrue(sizing.sizeable)
        self.assertEqual(sizing.quantity, 10)
        self.assertEqual(sizing.max_micros_cap, 10)
        self.assertLessEqual(sizing.actual_risk_dollars, 750.0)

    def test_uncapped_sizing_reproduces_the_83_contract_bug_without_a_cap(self):
        setup = _gc_setup(entry=4322.5, stop=4321.6, target=4326.7)
        sizing = size_whole_contract(setup, 750.0, max_micros_cap=1_000_000)

        self.assertEqual(sizing.quantity, 83)

    def test_cap_never_forces_cannot_size_when_one_contract_would_fit(self):
        setup = _gc_setup(entry=3500.0, stop=3496.7, target=3520.0)  # $33/contract
        sizing = size_whole_contract(setup, 750.0, max_micros_cap=1)

        self.assertTrue(sizing.sizeable)
        self.assertEqual(sizing.quantity, 1)

    def test_register_setup_reads_configured_execution_max_micros_by_default(self):
        setup = _gc_setup(entry=4322.5, stop=4321.6, target=4326.7)
        executor = PaperExecutor()

        with patch.dict(os.environ, {"OTR_EXECUTION_MAX_MICROS": "5"}, clear=False):
            position = executor.register_setup(setup, risk_dollars=750.0)

        self.assertEqual(position.quantity, 5)
        self.assertEqual(position.max_micros_cap, 5)
        self.assertLessEqual(position.actual_risk_dollars, position.requested_risk_dollars)

    def test_explicit_cap_argument_overrides_environment(self):
        setup = _gc_setup(entry=4322.5, stop=4321.6, target=4326.7)
        executor = PaperExecutor()

        with patch.dict(os.environ, {"OTR_EXECUTION_MAX_MICROS": "5"}, clear=False):
            position = executor.register_setup(setup, risk_dollars=750.0, max_micros_cap=20)

        self.assertEqual(position.quantity, 20)
        self.assertEqual(position.max_micros_cap, 20)


class WholeContractPnlTests(unittest.TestCase):
    def _register(self, *, entry=3500.0, stop=3495.0, target=3510.0, risk_dollars=500.0):
        setup = _gc_setup(entry=entry, stop=stop, target=target)
        executor = PaperExecutor()
        position = executor.register_setup(setup, risk_dollars=risk_dollars, max_micros_cap=100)
        self.assertIn(setup.setup_id, executor.positions)
        self.assertEqual(position.quantity, 10)  # $500 / ($5 * $10/pt) = 10 contracts
        self.assertEqual(position.accounting_version, PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1)
        return executor, setup

    def test_win_pnl_is_quantity_times_point_move_times_multiplier(self):
        executor, setup = self._register()
        t = setup.created_at
        executor.on_price("GC", setup.entry_price, t)  # fill
        changed = executor.on_price("GC", setup.target_price, t)  # touch target -> WIN

        position = changed[-1]
        self.assertEqual(position.status, "CLOSED")
        self.assertEqual(position.result, "WIN")
        # quantity(10) x point_move(10) x multiplier(10) = 1000.0
        self.assertAlmostEqual(position.result_dollars, 1000.0, places=6)

    def test_loss_pnl_is_quantity_times_point_move_times_multiplier(self):
        executor, setup = self._register()
        t = setup.created_at
        executor.on_price("GC", setup.entry_price, t)  # fill
        changed = executor.on_price("GC", setup.stop_price, t)  # touch stop -> LOSS

        position = changed[-1]
        self.assertEqual(position.status, "CLOSED")
        self.assertEqual(position.result, "LOSS")
        # quantity(10) x point_move(5) x multiplier(10) = -500.0
        self.assertAlmostEqual(position.result_dollars, -500.0, places=6)
        self.assertEqual(position.result_dollars, -position.actual_risk_dollars)


class ExistingLifecycleUnaffectedTests(unittest.TestCase):
    def test_non_gc_symbol_keeps_legacy_theoretical_accounting(self):
        setup = _nq_setup()
        executor = PaperExecutor()
        position = executor.register_setup(setup, risk_dollars=250.0)

        self.assertIsNone(position.quantity)
        self.assertIsNone(position.accounting_version)
        self.assertIn(setup.setup_id, executor.positions)

        t = setup.created_at
        executor.on_price("NQ", setup.entry_price, t)
        changed = executor.on_price("NQ", setup.target_price, t)
        position = changed[-1]

        self.assertEqual(position.result, "WIN")
        # Legacy theoretical calc: risk_dollars * risk_reward
        self.assertAlmostEqual(position.result_dollars, 250.0 * setup.risk_reward, places=6)

    def test_gc_trade_without_risk_dollars_keeps_legacy_path(self):
        setup = _gc_setup(entry=3500.0, stop=3495.0, target=3510.0)
        executor = PaperExecutor()
        position = executor.register_setup(setup, risk_dollars=None)

        self.assertIsNone(position.quantity)
        self.assertIsNone(position.accounting_version)
        self.assertIn(setup.setup_id, executor.positions)

    def test_full_pending_to_closed_lifecycle_still_works(self):
        setup = _gc_setup(entry=3500.0, stop=3495.0, target=3510.0)
        executor = PaperExecutor()
        position = executor.register_setup(setup, risk_dollars=500.0)
        self.assertEqual(position.status, "PENDING")

        t = setup.created_at
        executor.on_price("GC", 3502.0, t)  # not yet touched entry
        self.assertEqual(position.status, "PENDING")

        executor.on_price("GC", setup.entry_price, t)
        self.assertEqual(position.status, "OPEN")
        self.assertIsNotNone(position.opened_at)

        executor.on_price("GC", setup.target_price, t)
        self.assertEqual(position.status, "CLOSED")
        self.assertIsNotNone(position.closed_at)
        self.assertNotIn(setup.setup_id, executor.positions)
        self.assertIn(position, executor.closed)


if __name__ == "__main__":
    unittest.main()


class SizingCapResolutionTests(unittest.TestCase):
    """Regression coverage for the paper-sizing contract ceiling.

    ``ExecutionConfig.max_micros`` deliberately defaults to 1 (fail-safe for an
    unarmed broker deployment). Inheriting that into the *research* ledger
    silently sized every paper trade at one micro contract, so replay P&L was
    reported at a fraction of the intended scale.
    """

    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("OTR_PAPER_MAX_MICROS", "OTR_EXECUTION_MAX_MICROS", "EVAL_MAX_MICROS")
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_unconfigured_environment_uses_documented_default_not_one_contract(self):
        self.assertEqual(default_max_micros_cap(), 40)
        self.assertTrue(cap_from_default())

    def test_execution_ceiling_is_honoured_when_configured(self):
        with patch.dict(os.environ, {"OTR_EXECUTION_MAX_MICROS": "7"}, clear=False):
            self.assertEqual(default_max_micros_cap(), 7)
            self.assertFalse(cap_from_default())

    def test_paper_override_wins_over_execution_ceiling(self):
        with patch.dict(
            os.environ,
            {"OTR_EXECUTION_MAX_MICROS": "7", "OTR_PAPER_MAX_MICROS": "21"},
            clear=False,
        ):
            self.assertEqual(default_max_micros_cap(), 21)

    def test_eval_ceiling_is_used_when_no_execution_ceiling_is_set(self):
        with patch.dict(os.environ, {"EVAL_MAX_MICROS": "12"}, clear=False):
            self.assertEqual(default_max_micros_cap(), 12)
            self.assertFalse(cap_from_default())

    def test_a_plus_budget_is_not_collapsed_to_one_contract_by_default(self):
        # $3.30 stop -> $33/contract; $750 should fund 22 contracts, not 1.
        setup = _gc_setup(entry=3500.0, stop=3496.7, target=3520.0)
        executor = PaperExecutor()
        position = executor.register_setup(setup, risk_dollars=750.0)

        self.assertEqual(position.quantity, 22)
        self.assertFalse(position.cap_binding)
        self.assertTrue(position.cap_from_default)

    def test_binding_cap_is_recorded_instead_of_hidden(self):
        # $0.90 stop -> $9/contract; $750 wants 83 contracts.
        setup = _gc_setup(entry=4322.5, stop=4321.6, target=4326.7)
        executor = PaperExecutor()
        with patch.dict(os.environ, {"OTR_PAPER_MAX_MICROS": "10"}, clear=False):
            position = executor.register_setup(setup, risk_dollars=750.0)

        self.assertEqual(position.quantity, 10)
        self.assertEqual(position.uncapped_quantity, 83)
        self.assertTrue(position.cap_binding)
        self.assertFalse(position.cap_from_default)


class CostModelTests(unittest.TestCase):
    """Execution frictions for the research ledger.

    GROSS is the historical behaviour and must stay byte-for-byte identical so
    existing runs remain comparable. REALISTIC adds stop slippage (including
    gap-through) and per-contract round-turn costs.
    """

    def _run(self, cost_model, *, exit_price):
        setup = _gc_setup(entry=3500.0, stop=3495.0, target=3510.0)
        executor = PaperExecutor(cost_model=cost_model)
        executor.register_setup(setup, risk_dollars=500.0, max_micros_cap=100)
        t = setup.created_at
        executor.on_price("GC", setup.entry_price, t)  # fill at 3500
        changed = executor.on_price("GC", exit_price, t)
        return changed[-1]

    def test_gross_model_unchanged_on_target_exit(self):
        position = self._run(PaperCostModel(model="GROSS"), exit_price=3510.0)
        self.assertAlmostEqual(position.result_dollars, 1000.0, places=6)
        self.assertAlmostEqual(position.result_dollars_gross, 1000.0, places=6)
        self.assertAlmostEqual(position.slippage_dollars, 0.0, places=6)
        self.assertEqual(position.cost_model, "GROSS")

    def test_gross_model_unchanged_on_stop_exit(self):
        position = self._run(PaperCostModel(model="GROSS"), exit_price=3495.0)
        self.assertAlmostEqual(position.result_dollars, -500.0, places=6)

    def test_realistic_target_exit_charges_costs_only(self):
        # 10 contracts: 10 x ($1.24 + $0.36) = $16.00 of round-turn cost.
        position = self._run(PaperCostModel(model="REALISTIC"), exit_price=3510.0)
        self.assertAlmostEqual(position.result_dollars_gross, 1000.0, places=6)
        self.assertAlmostEqual(position.commission_dollars, 12.4, places=6)
        self.assertAlmostEqual(position.fees_dollars, 3.6, places=6)
        self.assertAlmostEqual(position.slippage_dollars, 0.0, places=6)
        self.assertAlmostEqual(position.result_dollars, 984.0, places=6)
        self.assertAlmostEqual(position.result_dollars_net, 984.0, places=6)

    def test_realistic_stop_exit_slips_one_tick_and_charges_costs(self):
        # Stop 3495.0 touched exactly -> fill 3494.9 after 1 tick ($0.10).
        # Loss = (3495.0 - 3494.9) ... i.e. 5.1 points x 10 contracts x $10.
        position = self._run(PaperCostModel(model="REALISTIC"), exit_price=3495.0)
        self.assertAlmostEqual(position.exit_price, 3494.9, places=6)
        self.assertAlmostEqual(position.result_dollars_gross, -510.0, places=6)
        self.assertAlmostEqual(position.slippage_dollars, 10.0, places=6)
        self.assertAlmostEqual(position.result_dollars, -526.0, places=6)

    def test_realistic_stop_exit_prices_a_gap_through_the_stop(self):
        # A news print gaps two dollars through the stop: fill at the market
        # price (3493.0) less one tick, not at the protected 3495.0.
        position = self._run(PaperCostModel(model="REALISTIC"), exit_price=3493.0)
        self.assertAlmostEqual(position.exit_price, 3492.9, places=6)
        self.assertAlmostEqual(position.result_dollars_gross, -710.0, places=6)

    def test_realistic_net_r_is_reported_against_actual_risk(self):
        position = self._run(PaperCostModel(model="REALISTIC"), exit_price=3510.0)
        # 984.0 net / 500.0 actual risk
        self.assertAlmostEqual(position.net_result_r, 1.968, places=6)

    def test_unknown_cost_model_is_rejected(self):
        with self.assertRaises(ValueError):
            PaperCostModel(model="NOT_A_MODEL")

    def test_cost_model_reads_environment(self):
        with patch.dict(
            os.environ,
            {
                "OTR_PAPER_COST_MODEL": "REALISTIC",
                "OTR_PAPER_ROUND_TURN_COMMISSION": "2.0",
                "OTR_PAPER_ROUND_TURN_FEES": "0.5",
                "OTR_PAPER_STOP_SLIPPAGE_TICKS": "2",
            },
            clear=False,
        ):
            model = PaperCostModel.from_env()
        self.assertEqual(model.model, "REALISTIC")
        self.assertTrue(model.enabled)
        self.assertEqual(model.round_turn_commission, 2.0)
        self.assertEqual(model.round_turn_fees, 0.5)
        self.assertEqual(model.slippage_ticks, 2)
