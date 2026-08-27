import unittest
from collections import Counter

from specnet_proofs import optimization_study as o
from specnet_proofs import proof_harness as h


class OptimizationStudyTests(unittest.TestCase):
    def test_balanced_full_split_covers_every_factor_level(self):
        validation, evaluation = o.balanced_scenario_split(
            h.scenarios("full"), 18, 18
        )
        self.assertFalse(set(validation) & set(evaluation))
        for split in (validation, evaluation):
            for factor in range(4):
                counts = Counter(row[factor] for row in split)
                self.assertEqual(3, len(counts))
                self.assertEqual({6}, set(counts.values()))

    def test_balanced_smoke_split_covers_every_factor_level(self):
        validation, evaluation = o.balanced_scenario_split(
            h.scenarios("smoke"), 12, 12
        )
        self.assertFalse(set(validation) & set(evaluation))
        for split in (validation, evaluation):
            for factor in range(4):
                self.assertEqual(
                    set(row[factor] for row in h.scenarios("smoke")),
                    set(row[factor] for row in split),
                )

    def test_v43_smoke_uses_third_unobserved_scenario_block(self):
        matrix = h.scenarios("smoke")
        validation, evaluation, prior_evaluation = o.protocol_scenario_split(
            "smoke", matrix, 12, 12
        )
        self.assertFalse(set(validation) & set(evaluation))
        self.assertFalse(set(prior_evaluation) & set(evaluation))
        self.assertEqual(set(matrix), set(validation + prior_evaluation + evaluation))
        for factor in range(4):
            self.assertEqual(
                set(row[factor] for row in matrix),
                set(row[factor] for row in evaluation),
            )

    def test_cluster_bootstrap_keeps_replicate_scenarios_together(self):
        values = [(0, 0, 1.0), (0, 1, 1.0), (1, 0, 3.0), (1, 1, 3.0)]
        low, high = o.replicate_cluster_bootstrap_ci(values, seed=1, draws=200)
        self.assertEqual(1.0, low)
        self.assertEqual(3.0, high)

    def test_adaptive_rule_uses_multiple_actions(self):
        always_recovery = {
            "wc": 0.1, "ws": 0.1, "wp": 0.1, "wcs": 0.0, "wcp": 0.0,
            "t0": 0.0, "t1": 10.0, "t2": 11.0, "t3": 12.0,
        }
        self.assertEqual(1, o.rule_action_count(always_recovery))
        self.assertGreaterEqual(o.rule_action_count(h.candidate_rules(5)[2]), 2)

    def test_bounded_candidates_use_only_recovery_and_moderate(self):
        for candidate in o.bounded_recovery_candidates():
            actions = {
                o.rule_action_from_state(candidate, state) for state in h.ALL_STATES
            }
            self.assertEqual({"recovery", "moderate"}, actions)

    def test_robust_selection_never_relabels_fixed_rule_as_adaptive(self):
        selected = o.choose_robust_candidate(
            [
                {
                    "candidate_id": 0,
                    "adaptive_candidate": False,
                    "meets_robust_feasibility": True,
                    "robust_cost": 1.0,
                },
                {
                    "candidate_id": 1,
                    "adaptive_candidate": True,
                    "meets_robust_feasibility": False,
                    "robust_cost": 2.0,
                },
            ]
        )
        self.assertEqual(1, selected["candidate_id"])
        self.assertFalse(selected["meets_robust_feasibility"])

    def test_robust_selection_prefers_feasible_adaptive_rule(self):
        selected = o.choose_robust_candidate(
            [
                {
                    "candidate_id": 1,
                    "adaptive_candidate": True,
                    "meets_robust_feasibility": False,
                    "robust_cost": 1.0,
                },
                {
                    "candidate_id": 2,
                    "adaptive_candidate": True,
                    "meets_robust_feasibility": True,
                    "robust_cost": 3.0,
                },
            ]
        )
        self.assertEqual(2, selected["candidate_id"])
        self.assertTrue(selected["meets_robust_feasibility"])

    def test_fair_reward_prefers_service_up_to_background_floor(self):
        specs = h.scaled_workload(11, "medium", 300, 8, 0.65, 1.0)
        sim = o.FairProofSimulator(
            specs, h.AuditedBandit(seed=1), "medium", 11, 300, 1500
        )
        workflow = next(iter(sim.workflows.values()))
        workflow.complete_time = workflow.spec.arrival_time + 10
        no_service = sim.workflow_reward(workflow)
        workflow.background_bytes_served = (
            o.FairProofSimulator.background_floor
            * sum(workflow.spec.background_sizes)
        )
        floor_service = sim.workflow_reward(workflow)
        self.assertGreater(floor_service, no_service)

    def test_aligned_reward_penalizes_quality_floor_violation(self):
        specs = h.scaled_workload(11, "medium", 300, 8, 0.65, 1.0)
        sim = o.AlignedRewardSimulator(
            specs, h.AuditedBandit(seed=1), "medium", 11, 300, 1500
        )
        workflow = next(iter(sim.workflows.values()))
        workflow.complete_time = workflow.spec.arrival_time + 10
        workflow.quality = 0.90
        low_quality = sim.workflow_reward(workflow)
        workflow.quality = 0.98
        high_quality = sim.workflow_reward(workflow)
        self.assertGreater(high_quality, low_quality)

    def test_strict_aligned_penalizes_deadline_miss_more(self):
        specs = h.scaled_workload(11, "medium", 300, 8, 0.65, 1.0)
        penalties = []
        for simulator_class in (
            o.AlignedRewardSimulator,
            o.StrictAlignedRewardSimulator,
        ):
            sim = simulator_class(
                specs, h.AuditedBandit(seed=1), "medium", 11, 300, 1500
            )
            workflow = next(iter(sim.workflows.values()))
            workflow.quality = 0.98
            workflow.background_bytes_served = (
                simulator_class.background_floor
                * sum(workflow.spec.background_sizes)
            )
            workflow.complete_time = (
                workflow.spec.arrival_time + workflow.spec.deadline - 1
            )
            before_deadline = sim.workflow_reward(workflow)
            workflow.complete_time = (
                workflow.spec.arrival_time + workflow.spec.deadline + 1
            )
            after_deadline = sim.workflow_reward(workflow)
            penalties.append(before_deadline - after_deadline)
        self.assertGreater(penalties[1], penalties[0])

    def test_deployment_gates_reject_miss_and_background_regression(self):
        summaries = [
            {
                "variant": "baseline",
                "mean_p99_latency": 100.0,
                "mean_quality": 0.96,
            },
            {
                "variant": "candidate",
                "mean_p99_latency": 99.0,
                "mean_quality": 0.96,
                "mean_background_service_ratio": 0.19,
                "mean_fair_cost": 3.0,
                "quality_feasible_fraction": 0.80,
            },
        ]
        comparisons = [
            {"variant": "candidate", "metric": "fair_cost", "ci95_high": -0.1},
            {"variant": "candidate", "metric": "quality", "ci95_low": 0.0},
            {"variant": "candidate", "metric": "p99_latency", "ci95_high": 5.0},
            {
                "variant": "candidate",
                "metric": "deadline_miss_ratio",
                "ci95_high": 0.01,
            },
        ]
        by_load = [
            {"variant": "candidate", "load": "heavy", "mean_quality": 0.96}
        ]
        fallback_rule = {
            "meets_quality_feasibility": True,
            "robust": {"meets_robust_feasibility": True},
        }
        row = o.deployment_gate_rows(
            summaries, comparisons, by_load, fallback_rule
        )[0]
        self.assertEqual(0, row["deadline_miss_noninferior"])
        self.assertEqual(0, row["background_floor_met"])
        self.assertEqual(0, row["all_deployment_gates_passed"])

    def test_median_ensemble_uses_member_median(self):
        state = ("high", "tight", "high_spec")
        members = []
        for value in (1.0, 2.0, 100.0):
            policy = h.AuditedBandit(seed=1)
            policy.q_values[state]["moderate"] = value
            policy.counts[state]["moderate"] = 10
            members.append(policy)
        ensemble = o.MedianEnsemble(members)
        self.assertEqual(2.0, ensemble.q_values[state]["moderate"])
        self.assertEqual(30, ensemble.counts[state]["moderate"])

    def test_hybrid_falls_back_when_average_support_is_low(self):
        state = ("high", "tight", "high_spec")
        member = h.AuditedBandit(seed=1)
        member.q_values[state]["critical_only"] = 1.0
        member.counts[state]["critical_only"] = 5
        ensemble = o.MedianEnsemble([member])
        always_full = {
            "wc": 1.0, "ws": 1.0, "wp": 1.0, "wcs": 0.0, "wcp": 0.0,
            "t0": 10.0, "t1": 11.0, "t2": 12.0, "t3": 13.0,
        }
        hybrid = o.ConfidenceHybrid(ensemble, always_full)
        self.assertEqual(("full", "low_support"), hybrid.action_for_state(state))


if __name__ == "__main__":
    unittest.main()
