from __future__ import annotations

import json
import subprocess
import sys
import unittest


class Operation80RuntimeTests(unittest.TestCase):
    def test_operation80_installs_final_legacy_contracts_in_isolated_process(self):
        script = r'''
import json
from src import main_80
from src import main_65

pipeline = main_80._install_pipeline_80()
print(json.dumps({
    "runtime_evaluator": getattr(main_80.runtime.evaluate_strategy, "__name__", ""),
    "quality_gate": getattr(pipeline.quality_gate, "__name__", ""),
    "setup_risk": getattr(pipeline.setup_risk, "__name__", ""),
    "intrabar": getattr(main_65._handle_intrabar_setup_65, "__name__", ""),
    "engine": "src.main_80",
}))
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["runtime_evaluator"], "evaluate")
        self.assertEqual(payload["quality_gate"], "_adaptive_quality_gate_72q")
        self.assertEqual(payload["setup_risk"], "_setup_risk_72o")
        self.assertEqual(payload["intrabar"], "handle_intrabar_80")
        self.assertEqual(payload["engine"], "src.main_80")


if __name__ == "__main__":
    unittest.main()
