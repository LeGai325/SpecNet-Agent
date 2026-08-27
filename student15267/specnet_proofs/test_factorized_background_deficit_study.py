import unittest

from .factorized_background_deficit_study import select_deficit_candidate


class FactorizedBackgroundDeficitStudyTests(unittest.TestCase):
    def test_selection_prefers_smallest_reservation_then_boost(self):
        rows = [
            {
                "candidate_id": 0,
                "background_target_ratio": 0.25,
                "background_weight_boost": 6.0,
                "mean_p99_latency": 105.0,
                "all_validation_gates_pass": 1,
            },
            {
                "candidate_id": 1,
                "background_target_ratio": 0.30,
                "background_weight_boost": 3.0,
                "mean_p99_latency": 100.0,
                "all_validation_gates_pass": 1,
            },
        ]
        self.assertEqual(select_deficit_candidate(rows)["candidate_id"], 0)

    def test_selection_requires_all_gates(self):
        with self.assertRaises(ValueError):
            select_deficit_candidate([])


if __name__ == "__main__":
    unittest.main()
