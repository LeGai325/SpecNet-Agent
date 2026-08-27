import unittest
from types import SimpleNamespace

from .quality_contract_simulator_study import contract_admission_decision


def branch(index, size, utility, probability):
    return SimpleNamespace(
        branch_index=index,
        size=size,
        expected_utility=utility,
        selection_probability=probability,
        required=False,
    )


def workflow(branches):
    return SimpleNamespace(template="coding", branches=branches)


class QualityContractSimulatorStudyTests(unittest.TestCase):
    def test_zero_uncertainty_primary_matches_point_minimum(self):
        target = workflow(
            [
                branch(1, 8.0, 0.6, 0.8),
                branch(2, 5.0, 0.4, 0.6),
                branch(3, 4.0, 0.4, 0.6),
            ]
        )
        decision = contract_admission_decision(target, 0.0, 0.94)
        self.assertEqual(decision.tier, "primary")
        self.assertEqual(decision.admitted_bytes, decision.point_minimum_bytes)

    def test_tiered_contract_avoids_infeasible_full_fallback(self):
        target = workflow(
            [
                branch(1, 5.0, 1.0, 0.77),
                branch(2, 6.0, 0.0, 0.1),
            ]
        )
        fixed = contract_admission_decision(target, 1.0, None)
        tiered = contract_admission_decision(target, 1.0, 0.94)
        self.assertEqual(fixed.tier, "infeasible_full_fallback")
        self.assertEqual(fixed.admitted_bytes, 11.0)
        self.assertEqual(tiered.tier, "degraded")
        self.assertEqual(tiered.admitted_bytes, 5.0)
        self.assertLess(tiered.admitted_bytes, fixed.admitted_bytes)

    def test_budgeted_contract_falls_back_to_point_portfolio(self):
        target = workflow(
            [
                branch(1, 8.0, 0.6, 0.8),
                branch(2, 5.0, 0.4, 0.6),
                branch(3, 4.0, 0.4, 0.6),
            ]
        )
        decision = contract_admission_decision(
            target,
            1.0,
            0.94,
            admission_byte_budget_ratio=1.0,
        )
        self.assertEqual(decision.tier, "budget_fallback")
        self.assertTrue(decision.budget_fallback)
        self.assertEqual(decision.admitted_bytes, decision.point_minimum_bytes)

    def test_budget_ratio_cannot_undercut_point_portfolio(self):
        target = workflow([branch(1, 5.0, 1.0, 0.8)])
        with self.assertRaises(ValueError):
            contract_admission_decision(
                target,
                1.0,
                0.94,
                admission_byte_budget_ratio=0.99,
            )

    def test_rejected_contract_starts_no_optional_branch(self):
        target = workflow([branch(1, 5.0, 1.0, 0.5)])
        decision = contract_admission_decision(target, 1.0, 0.94)
        self.assertTrue(decision.rejected)
        self.assertEqual(decision.selected_branch_ids, ())
        self.assertEqual(decision.admitted_bytes, 0.0)


if __name__ == "__main__":
    unittest.main()
