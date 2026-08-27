import unittest
from types import SimpleNamespace

from .factorized_background_eligible_window_study import (
    EligibleWindowPressureSimulator,
    IdleEligibleFactorizedRule,
    foreground_parity_metrics,
    meets_background_floor,
)


class FactorizedBackgroundEligibleWindowStudyTests(unittest.TestCase):
    def test_background_is_idle_only(self):
        params = {
            "congestion_critical_boost": 1.5,
            "congestion_optional_scale": 0.75,
            "slack_critical_boost": 2.0,
        }
        background = SimpleNamespace(background=True, workflow_id=1)
        critical = SimpleNamespace(background=False, workflow_id=2)
        owner = SimpleNamespace(complete_time=10)
        busy = SimpleNamespace(
            active_flows=lambda: [background, critical], workflows={1: owner}
        )
        idle = SimpleNamespace(active_flows=lambda: [background], workflows={1: owner})
        policy = IdleEligibleFactorizedRule(params)
        self.assertEqual(policy.flow_weight(background, busy), 0.0)
        self.assertEqual(policy.flow_weight(background, idle), 0.5)

    def test_target_is_not_enforced_before_owner_completion(self):
        simulator = EligibleWindowPressureSimulator.__new__(
            EligibleWindowPressureSimulator
        )
        owner = SimpleNamespace(complete_time=None, action="full")
        simulator.workflows = {1: owner}
        flow = SimpleNamespace(workflow_id=1, size=100.0, served=20.0)
        self.assertFalse(simulator.deferred_target_reached(flow))
        owner.complete_time = 10
        self.assertTrue(simulator.deferred_target_reached(flow))

    def test_foreground_parity_detects_waste_divergence(self):
        record = {
            "workflow_id": 1,
            "action": "full",
            "decision_state": "('low', 'loose', 'low_spec')",
            "latency": 10.0,
            "wasted_speculative_bytes": 2.0,
        }
        reference = {"workflow_records": [record]}
        matching = {"workflow_records": [dict(record)]}
        self.assertEqual(
            foreground_parity_metrics(matching, reference)["foreground_parity_pass"],
            1.0,
        )
        divergent = {
            "workflow_records": [dict(record, wasted_speculative_bytes=2.5)]
        }
        self.assertEqual(
            foreground_parity_metrics(divergent, reference)["foreground_parity_pass"],
            0.0,
        )

    def test_background_floor_accepts_machine_precision_rounding(self):
        self.assertTrue(meets_background_floor(0.2 - 1e-12))
        self.assertFalse(meets_background_floor(0.2 - 1e-6))


if __name__ == "__main__":
    unittest.main()
