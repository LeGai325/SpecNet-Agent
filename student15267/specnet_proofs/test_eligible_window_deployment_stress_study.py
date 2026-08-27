import unittest
from types import SimpleNamespace

from .eligible_window_deployment_stress_study import (
    TTLEligibleWindowPressureSimulator,
    QuiescentTTLEligibleWindowPressureSimulator,
    select_smallest_feasible_ttl,
    ttl_has_expired,
    ttl_label,
)


class EligibleWindowDeploymentStressTests(unittest.TestCase):
    def test_ttl_labels_and_boundary(self):
        self.assertEqual("ttl_16", ttl_label(16))
        self.assertEqual("unbounded", ttl_label(None))
        self.assertFalse(ttl_has_expired(10.0, 10.0, 0))
        self.assertTrue(ttl_has_expired(10.0, 11.0, 0))
        self.assertFalse(ttl_has_expired(10.0, 100.0, None))

    def test_selection_uses_smallest_finite_feasible_ttl(self):
        selected = select_smallest_feasible_ttl(
            [
                {"ttl_epochs": 8, "finite_ttl": 1, "all_deployment_gates_pass": 0},
                {"ttl_epochs": 32, "finite_ttl": 1, "all_deployment_gates_pass": 1},
                {"ttl_epochs": 16, "finite_ttl": 1, "all_deployment_gates_pass": 1},
                {"ttl_epochs": "unbounded", "finite_ttl": 0, "all_deployment_gates_pass": 1},
            ]
        )
        self.assertEqual(16, selected["ttl_epochs"])

    def test_selection_rejects_no_finite_feasible_ttl(self):
        with self.assertRaises(ValueError):
            select_smallest_feasible_ttl(
                [{"ttl_epochs": "unbounded", "finite_ttl": 0, "all_deployment_gates_pass": 1}]
            )

    def test_expired_workflow_keeps_first_terminal_time(self):
        simulator = object.__new__(QuiescentTTLEligibleWindowPressureSimulator)
        workflow = SimpleNamespace(
            complete_time=10.0,
            deferred_ttl_expired=True,
            deferred_terminal_time=11.0,
        )
        simulator.expire_deferred_background(workflow)
        self.assertEqual(11.0, workflow.deferred_terminal_time)

    def test_pending_deferred_background_blocks_idle_termination(self):
        simulator = object.__new__(QuiescentTTLEligibleWindowPressureSimulator)
        simulator.workflows = {
            1: SimpleNamespace(
                complete_time=10.0,
                deferred_ttl_expired=False,
                background_flows=[7],
            )
        }
        simulator.flows = {7: SimpleNamespace(completed_at=None)}
        simulator.deferred_target_reached = lambda flow: False
        self.assertTrue(simulator.has_pending_deferred_background())
