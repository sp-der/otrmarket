from __future__ import annotations

import unittest

from src.dashboard import server_72r


class Supervisor72RTests(unittest.TestCase):
    def test_promotion_patches_actual_server72_owner(self):
        owner = server_72r.base.base.base
        original = owner.promoted_engine_module
        try:
            self.assertEqual(server_72r._promote_engine_72r(), "src.main_72r")
            self.assertEqual(owner.promoted_engine_module(), "src.main_72r")
            self.assertEqual(owner.promoted_engine_module("src.main_72"), "src.main_72r")
        finally:
            owner.promoted_engine_module = original


if __name__ == "__main__":
    unittest.main()
