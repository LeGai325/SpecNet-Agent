import unittest

from .finite_monotonicity_check import check, enumerate_cases


class FiniteMonotonicityTests(unittest.TestCase):
    def test_enumeration_covers_full_cartesian_domain(self):
        self.assertEqual(223587, sum(1 for _ in enumerate_cases()))

    def test_enumerated_cases_have_no_reverse_completion_effect(self):
        result = check()
        self.assertEqual(result["cases"], 223587)
        self.assertEqual(result["violations"], 0)
        self.assertLessEqual(result["max_delta_removed_minus_original"], 0)


if __name__ == "__main__":
    unittest.main()
