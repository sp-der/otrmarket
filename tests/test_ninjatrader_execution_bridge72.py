import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NinjaTraderExecutionBridge72Tests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "ninjatrader/OTRExecutionBridge.cs").read_text()

    def test_execution_adapter_defaults_disarmed(self):
        self.assertIn("ArmSimulationOrders = false;", self.text)
        self.assertIn("if (!ArmSimulationOrders)", self.text)

    def test_execution_adapter_refuses_non_sim_accounts(self):
        self.assertIn('StartsWith("Sim", StringComparison.OrdinalIgnoreCase)', self.text)
        self.assertIn("!IsSimulationAccountName(account.Name)", self.text)

    def test_phase_one_is_single_micro_only(self):
        self.assertIn("if (command.quantity != 1)", self.text)
        self.assertIn("requires exactly one micro contract", self.text)

    def test_command_id_is_embedded_in_order_names_for_restart_idempotency(self):
        self.assertIn('return "OTR72|" + commandId + "|" + role;', self.text)
        self.assertIn("SeedKnownOtrOrders", self.text)
        self.assertIn("submittedCommands.TryAdd(commandId, 0)", self.text)

    def test_filled_entry_creates_oco_bracket(self):
        self.assertIn("SubmitProtectiveBracket", self.text)
        self.assertIn("OrderType.Limit", self.text)
        self.assertIn("OrderType.StopMarket", self.text)
        self.assertIn("account.Submit(new[] { target, stop });", self.text)


if __name__ == "__main__":
    unittest.main()
