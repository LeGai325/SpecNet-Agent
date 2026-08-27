import unittest

from .eligible_window_floor_audit import seed_base_from_rule


class EligibleWindowFloorAuditTests(unittest.TestCase):
    def test_parses_standard_seed_rule(self):
        self.assertEqual(
            seed_base_from_rule("2300000 + run*10000 + scenario_index"),
            2_300_000,
        )

    def test_rejects_unknown_seed_rule(self):
        with self.assertRaises(ValueError):
            seed_base_from_rule("random seed")


if __name__ == "__main__":
    unittest.main()
