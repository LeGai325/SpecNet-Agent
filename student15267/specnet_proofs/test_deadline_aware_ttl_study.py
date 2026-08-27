import unittest
from types import SimpleNamespace

from .deadline_aware_ttl_study import EarliestExpiryIdleEligibleRule


class DeadlineAwareTTLTests(unittest.TestCase):
    def test_earliest_expiry_wins_during_idle(self):
        early_owner = SimpleNamespace(complete_time=10.0)
        late_owner = SimpleNamespace(complete_time=20.0)
        early = SimpleNamespace(background=True, workflow_id=1)
        late = SimpleNamespace(background=True, workflow_id=2)
        sim = SimpleNamespace(
            workflows={1: early_owner, 2: late_owner},
            deferred_ttl_epochs=100,
            active_flows=lambda: [early, late],
            deferred_target_reached=lambda flow: False,
        )
        rule = object.__new__(EarliestExpiryIdleEligibleRule)
        self.assertEqual(1.0, rule.flow_weight(early, sim))
        self.assertLess(rule.flow_weight(late, sim), 1e-9)

    def test_deferred_work_remains_hidden_while_foreground_busy(self):
        owner = SimpleNamespace(complete_time=10.0)
        deferred = SimpleNamespace(background=True, workflow_id=1)
        foreground = SimpleNamespace(background=False, workflow_id=2)
        sim = SimpleNamespace(
            workflows={1: owner},
            deferred_ttl_epochs=100,
            active_flows=lambda: [deferred, foreground],
            deferred_target_reached=lambda flow: False,
        )
        rule = object.__new__(EarliestExpiryIdleEligibleRule)
        self.assertEqual(0.0, rule.flow_weight(deferred, sim))

