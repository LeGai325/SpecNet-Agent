import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from specnet_proofs import proof_harness as h


class ProofHarnessTests(unittest.TestCase):
    def make_sim(self):
        specs = h.scaled_workload(11, "medium", 300, 8, 0.65, 1.0)
        policy = h.AuditedBandit(seed=1, train=False, epsilon=0.0)
        return h.ProofSimulator(specs, policy, "medium", 11, 300, 1500)

    def test_effective_slack_is_action_independent(self):
        sim = self.make_sim()
        workflow = next(iter(sim.workflows.values()))
        sim.time = workflow.spec.arrival_time
        before = sim.workflow_slack_ratio(workflow)
        workflow.action = "critical_only"
        after = sim.workflow_slack_ratio(workflow)
        self.assertEqual(before, after)

    def test_ablation_keys_remove_only_named_signal(self):
        sim = self.make_sim()
        workflow = next(iter(sim.workflows.values()))
        full = h.AuditedBandit().state_key(sim, workflow)
        no_slack = h.NoSlackBandit().state_key(sim, workflow)
        self.assertEqual(full[0], no_slack[0])
        self.assertEqual("all_slack", no_slack[1])
        self.assertEqual(full[2], no_slack[2])

    def test_rule_thresholds_are_ordered(self):
        for params in h.candidate_rules(20):
            self.assertLess(params["t0"], params["t1"])
            self.assertLess(params["t1"], params["t2"])
            self.assertLess(params["t2"], params["t3"])

    def test_simulator_construction_does_not_mutate_upstream(self):
        before = h.sha256(h.UPSTREAM_PATH)
        self.make_sim()
        self.assertEqual(before, h.sha256(h.UPSTREAM_PATH))

    def test_bandit_checkpoint_round_trip(self):
        policy = h.AuditedBandit(seed=3)
        state = ("high", "tight", "high_spec")
        policy.q_values[state]["moderate"] = -0.25
        policy.counts[state]["moderate"] = 17
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bandit.json"
            h.save_bandit(path, policy)
            restored = h.load_bandit(path)
        self.assertEqual(-0.25, restored.q_values[state]["moderate"])
        self.assertEqual(17, restored.counts[state]["moderate"])

    def test_rq1_slice_uses_full_workflow_membership(self):
        base = {
            "run": 0, "scenario": 0, "workflow_id": 1, "latency": 10,
            "deadline": 20, "deadline_miss": 0, "wasted_speculative_bytes": 2,
            "quality": 1.0, "slack_bucket": "tight", "spec_pressure_bucket": "high_spec",
            "congestion_bucket": "high",
        }
        rows = []
        for policy in ("full", "no_congestion", "no_slack", "no_spec_pressure"):
            row = dict(base, policy=policy)
            if policy != "full":
                # The ablation's endogenous state differs, but the same workflow
                # must remain in the full-reference slice.
                row["congestion_bucket"] = "low"
                row["slack_bucket"] = "loose"
                row["spec_pressure_bucket"] = "low_spec"
                row["latency"] = 12
            rows.append(row)
        results = h.ablation_slice_rows(rows)
        primary = [row for row in results if row["primary_metric"]]
        self.assertEqual(3, len(primary))
        self.assertTrue(all(row["paired_units"] == 1 for row in primary))
        self.assertTrue(all(str(row["slice"]).startswith("full_reference:") for row in primary))

    def test_stratified_mean_weights_scenarios_equally(self):
        # Scenario 0 has more repetitions but must not outweigh scenario 1.
        values = [(0, 0.0), (0, 0.0), (0, 0.0), (1, 10.0)]
        self.assertEqual(5.0, h.stratified_mean(values))
        low, high = h.stratified_bootstrap_ci(values, draws=100)
        self.assertEqual((5.0, 5.0), (low, high))

    def test_stratified_randomization_uses_equal_scenario_weight(self):
        values = [(0, 1.0), (0, 1.0), (0, 1.0), (1, -1.0)]
        self.assertEqual(0.0, h.stratified_mean(values))
        self.assertEqual(1.0, h.stratified_randomization_p(values, seed=1, draws=100))

    def test_rule_bandit_pairing_enforces_quality_floor(self):
        common = {
            "run": 0, "scenario": 0, "load": "heavy", "deadline_scale": 1.0,
            "optional_scale": 1.0, "capacity_scale": 1.0, "template": "all",
            "deadline_miss_ratio": 0.0, "wasted_speculative_bytes_per_workflow": 10.0,
        }
        rows = [
            dict(common, policy="bandit", p99_latency=100.0, avg_quality=0.96),
            # Raw latency is better, but the rule violates the quality floor.
            dict(common, policy="global_tuned_rule", p99_latency=80.0, avg_quality=0.90),
        ]
        units, summary = h.rule_bandit_pairwise_rows(rows, 0.95, 100.0, 10.0)
        self.assertEqual("bandit", units[0]["constrained_winner"])
        self.assertEqual("neither", units[0]["raw_dominance_winner"])
        self.assertEqual(5, len(summary))

    def test_all_deployment_policies_receive_paired_comparison(self):
        common = {
            "run": 0, "scenario": 0, "load": "heavy", "deadline_scale": 1.0,
            "optional_scale": 1.0, "capacity_scale": 1.0, "template": "all",
            "deadline_miss_ratio": 0.0, "wasted_speculative_bytes_per_workflow": 10.0,
            "p99_latency": 100.0, "avg_quality": 0.96,
        }
        rows = [
            dict(common, policy="bandit"),
            dict(common, policy="fixed_moderate", p99_latency=90.0),
            dict(common, policy="global_tuned_rule", p99_latency=95.0),
        ]
        units, summary = h.policy_bandit_pairwise_rows(rows, 0.95, 100.0, 10.0)
        self.assertEqual({"fixed_moderate", "global_tuned_rule"}, {row["comparison_policy"] for row in units})
        self.assertEqual(10, len(summary))


if __name__ == "__main__":
    unittest.main()
