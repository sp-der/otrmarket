from types import SimpleNamespace
import unittest

from src.strategies.early_entry72 import EarlyEntryPlanner72


class EarlyEntry72Tests(unittest.TestCase):
    def setUp(self):
        self.planner = EarlyEntryPlanner72()

    def test_capital_priority_only_reserves_against_small_trade(self):
        self.planner.arms[("NQ", "1m")] = {
            "symbol": "NQ",
            "timeframe": "1m",
            "direction": "bearish",
            "checklist_score": 4,
            "risk_reward": 3.20,
            "age_bars": 2,
            "retracement_fraction": 0.705,
        }
        small = SimpleNamespace(
            symbol="ES",
            timeframe="1m",
            risk_reward=1.55,
            metadata={"strategy": "ICT_CONFLUENCE"},
        )
        reason = self.planner.capital_priority_reason(small)
        self.assertIsNotNone(reason)
        self.assertIn("3.20R", reason)

        respectable = SimpleNamespace(
            symbol="ES",
            timeframe="1m",
            risk_reward=1.80,
            metadata={"strategy": "ICT_CONFLUENCE"},
        )
        self.assertIsNone(self.planner.capital_priority_reason(respectable))

    def test_rejection_block_is_never_capital_priority_blocked(self):
        self.planner.arms[("NQ", "1m")] = {
            "symbol": "NQ",
            "timeframe": "1m",
            "direction": "bearish",
            "checklist_score": 4,
            "risk_reward": 4.00,
            "age_bars": 1,
            "retracement_fraction": 0.705,
        }
        setup = SimpleNamespace(
            symbol="ES",
            timeframe="1m",
            risk_reward=1.25,
            metadata={"strategy": "REJECTION_BLOCK_10_10"},
        )
        self.assertIsNone(self.planner.capital_priority_reason(setup))

    def test_confirmed_setup_can_activate_earlier_standard_ote_geometry(self):
        pd_array = SimpleNamespace(lower=94.0, upper=95.0, direction="bearish")
        displacement = SimpleNamespace(low=90.0, high=100.0)
        setup = SimpleNamespace(
            symbol="GC",
            timeframe="1m",
            direction="bearish",
            pd_array=pd_array,
            displacement=displacement,
            entry_price=97.9,
            stop_price=101.0,
            target_price=85.0,
            risk_reward=3.0,
            metadata={
                "entry_type": "OTE_79",
                "risk_multiplier": 0.50,
                "instant_stop_penalty_active": False,
            },
        )
        self.planner.arms[("GC", "1m")] = {
            "state": "PRE_ARMED",
            "symbol": "GC",
            "timeframe": "1m",
            "direction": "bearish",
            "entry_type": "EARLY_OTE_0_705",
            "entry": 97.05,
            "stop": 100.2,
            "target": 90.1,
            "risk_reward": 2.20,
            "retracement_fraction": 0.705,
            "pd_signature": self.planner._pd_signature(pd_array),
            "checklist_score": 4,
            "age_bars": 2,
            "shallow_guard_passed": False,
        }
        promoted = self.planner.promote(SimpleNamespace(contexts={}), setup, {})
        self.assertAlmostEqual(promoted.entry_price, 97.05)
        self.assertAlmostEqual(promoted.risk_reward, 2.20)
        self.assertEqual(promoted.metadata["execution_tier"], "EARLY_ENTRY_CONFIRMED_72H")
        self.assertEqual(
            promoted.metadata["early_entry_arm_72h"]["state"],
            "CONFIRMED_AND_ACTIVATED",
        )
        self.assertEqual(promoted.metadata["risk_multiplier"], 0.50)

    def test_shallow_arm_cannot_bypass_operation67_confirmation(self):
        pd_array = SimpleNamespace(lower=94.0, upper=95.0, direction="bearish")
        displacement = SimpleNamespace(low=90.0, high=100.0)
        setup = SimpleNamespace(
            symbol="GC",
            timeframe="1m",
            direction="bearish",
            pd_array=pd_array,
            displacement=displacement,
            entry_price=97.9,
            stop_price=101.0,
            target_price=85.0,
            risk_reward=3.0,
            metadata={
                "entry_type": "OTE_79",
                "risk_multiplier": 0.75,
                "instant_stop_penalty_active": False,
            },
        )
        self.planner.arms[("GC", "1m")] = {
            "state": "PRE_ARMED",
            "symbol": "GC",
            "timeframe": "1m",
            "direction": "bearish",
            "entry_type": "EARLY_OTE_0_62",
            "entry": 96.2,
            "stop": 100.2,
            "target": 87.4,
            "risk_reward": 2.20,
            "retracement_fraction": 0.62,
            "pd_signature": self.planner._pd_signature(pd_array),
            "checklist_score": 4,
            "age_bars": 2,
            "shallow_guard_passed": False,
        }
        result = self.planner.promote(SimpleNamespace(contexts={}), setup, {})
        self.assertAlmostEqual(result.entry_price, 97.9)
        self.assertEqual(
            result.metadata["early_entry_arm_72h"]["state"],
            "CONFIRMED_BUT_FINAL_GEOMETRY_KEPT",
        )
        self.assertFalse(
            result.metadata["early_entry_arm_72h"]["shallow_activation_allowed"]
        )

    def test_confirmed_shallow_arm_keeps_reduced_risk_cap(self):
        pd_array = SimpleNamespace(lower=94.0, upper=95.0, direction="bearish")
        displacement = SimpleNamespace(low=90.0, high=100.0)
        setup = SimpleNamespace(
            symbol="GC",
            timeframe="1m",
            direction="bearish",
            pd_array=pd_array,
            displacement=displacement,
            entry_price=97.9,
            stop_price=101.0,
            target_price=85.0,
            risk_reward=3.0,
            metadata={
                "entry_type": "OTE_79",
                "risk_multiplier": 0.80,
                "entry_risk_cap": 1.0,
                "instant_stop_penalty_active": False,
            },
        )
        checklist = {"all_confirmed": True}
        self.planner.arms[("GC", "1m")] = {
            "state": "PRE_ARMED",
            "symbol": "GC",
            "timeframe": "1m",
            "direction": "bearish",
            "entry_type": "EARLY_OTE_0_62",
            "entry": 96.2,
            "stop": 100.2,
            "target": 87.4,
            "risk_reward": 2.20,
            "retracement_fraction": 0.62,
            "pd_signature": self.planner._pd_signature(pd_array),
            "checklist_score": 4,
            "age_bars": 2,
            "shallow_guard_passed": True,
            "aggressive_confirmation": checklist,
        }
        promoted = self.planner.promote(SimpleNamespace(contexts={}), setup, {})
        self.assertAlmostEqual(promoted.entry_price, 96.2)
        self.assertLessEqual(promoted.metadata["risk_multiplier"], 0.60)
        self.assertLessEqual(promoted.metadata["entry_risk_cap"], 0.60)
        self.assertTrue(promoted.metadata["aggressive_entry"])

    def test_weak_prearm_does_not_replace_premium_final_geometry(self):
        pd_array = SimpleNamespace(lower=94.0, upper=95.0, direction="bearish")
        displacement = SimpleNamespace(low=90.0, high=100.0)
        setup = SimpleNamespace(
            symbol="GC",
            timeframe="1m",
            direction="bearish",
            pd_array=pd_array,
            displacement=displacement,
            entry_price=97.9,
            stop_price=101.0,
            target_price=85.0,
            risk_reward=3.0,
            metadata={
                "entry_type": "OTE_79",
                "risk_multiplier": 0.50,
                "instant_stop_penalty_active": False,
            },
        )
        self.planner.arms[("GC", "1m")] = {
            "state": "PRE_ARMED",
            "symbol": "GC",
            "timeframe": "1m",
            "direction": "bearish",
            "entry_type": "EARLY_OTE_0_705",
            "entry": 97.05,
            "stop": 100.2,
            "target": 91.4,
            "risk_reward": 1.80,
            "retracement_fraction": 0.705,
            "pd_signature": self.planner._pd_signature(pd_array),
            "checklist_score": 4,
            "age_bars": 2,
            "shallow_guard_passed": False,
        }
        result = self.planner.promote(SimpleNamespace(contexts={}), setup, {})
        self.assertAlmostEqual(result.entry_price, 97.9)
        self.assertAlmostEqual(result.risk_reward, 3.0)
        self.assertEqual(
            result.metadata["early_entry_arm_72h"]["state"],
            "CONFIRMED_BUT_FINAL_GEOMETRY_KEPT",
        )


if __name__ == "__main__":
    unittest.main()
