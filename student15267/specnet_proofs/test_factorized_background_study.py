import unittest

from .factorized_background_study import select_background_candidate


class FactorizedBackgroundStudyTests(unittest.TestCase):
    def test_selection_uses_smallest_feasible_boost(self):
        rows = [
            {
                "candidate_id": 0,
                "background_weight_boost": 2.0,
                "mean_p99_latency": 100.0,
                "all_validation_gates_pass": 0,
            },
            {
                "candidate_id": 1,
                "background_weight_boost": 3.0,
                "mean_p99_latency": 105.0,
                "all_validation_gates_pass": 1,
            },
            {
                "candidate_id": 2,
                "background_weight_boost": 4.0,
                "mean_p99_latency": 101.0,
                "all_validation_gates_pass": 1,
            },
        ]
        self.assertEqual(
            select_background_candidate(rows)["background_weight_boost"], 3.0
        )

    def test_selection_rejects_when_no_candidate_passes(self):
        with self.assertRaises(ValueError):
            select_background_candidate(
                [
                    {
                        "candidate_id": 0,
                        "background_weight_boost": 8.0,
                        "mean_p99_latency": 100.0,
                        "all_validation_gates_pass": 0,
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
