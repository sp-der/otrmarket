import unittest

from src.dashboard import server_70


class Supervisor70Tests(unittest.TestCase):
    def test_legacy_modules_promote_to_operation70(self):
        for module in ("", "src.main_67", "src.main_68", "src.main_69"):
            with self.subTest(module=module):
                self.assertEqual(server_70.promoted_engine_module(module), "src.main_70")

    def test_future_explicit_module_is_preserved(self):
        self.assertEqual(server_70.promoted_engine_module("src.main_71"), "src.main_71")


if __name__ == "__main__":
    unittest.main()
