import unittest
from types import SimpleNamespace

from .quality_contract_trace_audit import audit_workflow


def branch(index, size, utility, probability):
    return SimpleNamespace(
        branch_index=index,
        size=size,
        expected_utility=utility,
        selection_probability=probability,
        required=False,
    )


def workflow(branches, template="coding"):
    return SimpleNamespace(
        workflow_id=7,
        template=template,
        branches=branches,
    )


class QualityContractTraceAuditTests(unittest.TestCase):
    def test_point_broker_matches_v4_and_robust_selection_has_premium(self):
        target = workflow(
            [
                branch(1, 10.0, 0.7, 1.0),
                branch(2, 3.0, 0.2, 0.3),
                branch(3, 4.0, 0.3, 1.0),
            ]
        )
        rows = {
            row["uncertainty_penalty"]: row
            for row in audit_workflow(target, (0.0, 1.0))
        }
        self.assertEqual(rows[0.0]["point_exact_agreement"], 1)
        self.assertEqual(rows[0.0]["broker_selected_ids"], "1;2")
        self.assertEqual(rows[0.0]["maximum_supported_quality"], 1.0)
        self.assertEqual(rows[1.0]["broker_selected_ids"], "1;3")
        self.assertEqual(rows[1.0]["byte_premium_vs_point"], 1.0)
        self.assertEqual(rows[1.0]["feasible"], 1)

    def test_robust_haircut_can_expose_infeasible_contract(self):
        target = workflow([branch(1, 5.0, 1.0, 0.5)])
        rows = {
            row["uncertainty_penalty"]: row
            for row in audit_workflow(target, (0.0, 1.0))
        }
        self.assertEqual(rows[0.0]["feasible"], 1)
        self.assertEqual(rows[1.0]["feasible"], 0)
        self.assertEqual(rows[1.0]["fallback_all_optional"], 1)
        self.assertLess(rows[1.0]["maximum_supported_quality"], 0.95)

    def test_tiered_contract_degrades_instead_of_opening_every_branch(self):
        target = workflow(
            [
                branch(1, 5.0, 1.0, 0.77),
                branch(2, 6.0, 0.0, 0.1),
            ]
        )
        robust = audit_workflow(target, (1.0,))[0]
        self.assertEqual(robust["feasible"], 0)
        self.assertEqual(robust["negotiated_tier"], "degraded")
        self.assertEqual(robust["negotiated_admission_bytes"], 5.0)
        self.assertEqual(robust["negotiated_degraded"], 1)


if __name__ == "__main__":
    unittest.main()
