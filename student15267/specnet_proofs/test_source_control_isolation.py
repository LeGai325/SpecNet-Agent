import unittest
from collections import Counter
from types import SimpleNamespace

from .source_control_isolation import (
    SOURCE_ACTIONS,
    IsolationSimulator,
    make_fixed_source_policy,
)


class SourceControlIsolationTests(unittest.TestCase):
    def test_policy_keeps_source_action_fixed(self):
        policy_class = make_fixed_source_policy("critical_only", "fifo")
        policy = policy_class(seed=1)
        workflow = SimpleNamespace(decision_state=None)
        simulator = SimpleNamespace(observable_state=lambda value: ("low", "loose", "low_spec"))
        self.assertEqual(policy.decide_action(simulator, workflow), "critical_only")
        self.assertEqual(policy.name, "fixed_critical_only_fifo")
        self.assertEqual(policy.action_counter["critical_only"], 1)

    def test_all_source_actions_are_supported(self):
        for source_action in SOURCE_ACTIONS:
            policy_class = make_fixed_source_policy(source_action, "critical_path")
            self.assertEqual(policy_class.name, f"fixed_{source_action}_critical_path")

    def test_summary_records_generated_source_bytes(self):
        simulator = object.__new__(IsolationSimulator)
        simulator.completed_workflows = []
        simulator.flows = {}
        simulator.policy = SimpleNamespace(name="test", action_counter=Counter())
        simulator.load = "light"
        simulator.seed = 1
        simulator.total_served = 0.0
        simulator.total_capacity = 1.0
        simulator.queue_pressure_samples = []
        self.assertEqual(simulator.summary()["generated_speculative_bytes_per_workflow"], 0.0)


if __name__ == "__main__":
    unittest.main()
