"""Tests for leakage-safe Pcrit and paper-form Score shadow computation."""

import math
import unittest

try:
    from criticality_history import (
        SelectionHistory,
        SelectionHistoryKey,
        SelectionObservation,
    )
    from criticality_scoring import (
        CriticalityInputs,
        GraphCriticalityFeatures,
        config_for_profile,
        derive_graph_features,
        score_criticality,
    )
    from criticality_preflight import run_criticality_preflight
    import specnet_agent_experiment as experiment
    from workflow_hint_replay import replay_workflow_hint_events
    from workflow_hints import WorkflowHintCollector
except ImportError:  # pragma: no cover - package-style imports
    from .criticality_history import (
        SelectionHistory,
        SelectionHistoryKey,
        SelectionObservation,
    )
    from .criticality_scoring import (
        CriticalityInputs,
        GraphCriticalityFeatures,
        config_for_profile,
        derive_graph_features,
        score_criticality,
    )
    from .criticality_preflight import run_criticality_preflight
    from . import specnet_agent_experiment as experiment
    from .workflow_hint_replay import replay_workflow_hint_events
    from .workflow_hints import WorkflowHintCollector


class CriticalityScoringTest(unittest.TestCase):
    def inputs(self, **changes):
        values = {
            "workflow_id": "7",
            "step_id": "branch:0",
            "flow_id": "12",
            "timestamp": 10.0,
            "request_type": "retrieval",
            "required": True,
            "speculation_level": 0.0,
            "size": 16.0,
            "remaining_size": 16.0,
            "created_at": 0.0,
            "slack_ratio": 1.0,
            "historical_selection_rate": 0.5,
            "history_sample_count": 0,
            "graph": GraphCriticalityFeatures(
                blocks_final=True,
                direct_hard_children=1,
                downstream_hard_reachable=2,
                downstream_total_reachable=2,
                dependency_role="hard_dependency",
                optional_rank=0,
            ),
        }
        values.update(changes)
        return CriticalityInputs(**values)

    def test_required_hard_path_scores_above_optional_background(self):
        for profile in (
            "balanced",
            "structure_heavy",
            "urgency_heavy",
            "no_cost_urgency",
        ):
            with self.subTest(profile=profile):
                config = config_for_profile(profile)
                required = score_criticality(self.inputs(), config)
                optional = score_criticality(
                    self.inputs(
                        required=False,
                        speculation_level=1.0,
                        historical_selection_rate=0.2,
                        graph=GraphCriticalityFeatures(
                            dependency_role="optional_evidence",
                            optional_rank=1,
                        ),
                    ),
                    config,
                )
                background = score_criticality(
                    self.inputs(
                        required=False,
                        request_type="background",
                        speculation_level=1.0,
                        historical_selection_rate=0.0,
                        graph=GraphCriticalityFeatures(),
                    ),
                    config,
                )
                self.assertGreater(required.pcrit, optional.pcrit)
                self.assertGreater(optional.pcrit, background.pcrit)
                self.assertGreater(required.score, optional.score)

    def test_tighter_slack_increases_pcrit_and_cost_delay(self):
        loose = score_criticality(self.inputs(slack_ratio=3.0))
        tight = score_criticality(self.inputs(slack_ratio=-1.0))
        self.assertGreater(tight.urgency, loose.urgency)
        self.assertGreater(tight.pcrit, loose.pcrit)
        self.assertGreater(tight.cost_delay, loose.cost_delay)

    def test_history_size_fanout_age_and_spec_penalty_are_monotonic(self):
        optional = {
            "required": False,
            "speculation_level": 1.0,
            "graph": GraphCriticalityFeatures(dependency_role="optional_evidence"),
        }
        low_history = score_criticality(
            self.inputs(**optional, historical_selection_rate=0.1)
        )
        high_history = score_criticality(
            self.inputs(**optional, historical_selection_rate=0.9)
        )
        self.assertGreater(high_history.pcrit, low_history.pcrit)
        self.assertLess(high_history.spec_penalty, low_history.spec_penalty)

        small = score_criticality(self.inputs(remaining_size=4.0))
        large = score_criticality(self.inputs(remaining_size=64.0))
        self.assertGreater(small.score, large.score)

        leaf = score_criticality(self.inputs(graph=GraphCriticalityFeatures()))
        fanout = score_criticality(
            self.inputs(
                graph=GraphCriticalityFeatures(
                    direct_hard_children=2,
                    downstream_hard_reachable=3,
                    downstream_total_reachable=4,
                )
            )
        )
        self.assertGreater(fanout.fanout_factor, leaf.fanout_factor)
        self.assertGreater(fanout.score, leaf.score)

        young = score_criticality(self.inputs(timestamp=1.0, created_at=0.0))
        old = score_criticality(self.inputs(timestamp=1000.0, created_at=0.0))
        older = score_criticality(self.inputs(timestamp=2000.0, created_at=0.0))
        self.assertGreater(old.age_boost, young.age_boost)
        self.assertEqual(old.age_boost, older.age_boost)

    def test_zero_size_is_finite_and_profiles_are_preregistered(self):
        result = score_criticality(self.inputs(size=0.0, remaining_size=0.0))
        self.assertTrue(math.isfinite(result.score))
        self.assertFalse(result.affects_policy)
        for profile in (
            "balanced",
            "structure_heavy",
            "urgency_heavy",
            "no_cost_urgency",
        ):
            with self.subTest(profile=profile):
                self.assertAlmostEqual(
                    config_for_profile(profile).structure_weight
                    + config_for_profile(profile).urgency_weight
                    + config_for_profile(profile).history_weight,
                    1.0,
                )

    def test_graph_features_support_engine_and_replay_snapshots(self):
        snapshot = {
            "steps": {
                "planner": {
                    "parents": [],
                    "dependency_kinds": {},
                    "request_type": "planner",
                    "state": "completed",
                },
                "branch:0": {
                    "parents": ["planner"],
                    "dependency_kinds": {"planner": "control_trigger"},
                    "request_type": "retrieval",
                    "state": "running",
                },
                "llm": {
                    "parents": ["branch:0"],
                    "dependency_kinds": {"branch:0": "hard_dependency"},
                    "request_type": "llm",
                    "state": "created",
                },
                "judge": {
                    "parents": ["llm"],
                    "dependency_kinds": {"llm": "hard_dependency"},
                    "request_type": "judge",
                    "state": "created",
                },
            }
        }
        features = derive_graph_features(snapshot, "branch:0")
        self.assertTrue(features.blocks_final)
        self.assertEqual(features.direct_hard_children, 1)
        self.assertEqual(features.downstream_hard_reachable, 2)
        self.assertEqual(features.downstream_total_reachable, 2)
        self.assertEqual(features.dependency_role, "hard_dependency")
        self.assertEqual(features.optional_rank, 0)

    def test_v1_0_and_v1_1_replay_snapshots_have_same_graph_features(self):
        collector = WorkflowHintCollector()
        collector.register_workflow(3, deadline_hint=100.0, timestamp=0.0, source="test")
        collector.create_step(
            3,
            "parent",
            parents=(),
            request_type="tool",
            size_hint=4.0,
            size_unit="normalized_work",
            speculation_level=0.0,
            timestamp=0.0,
        )
        collector.mark_ready(3, "parent", timestamp=0.0)
        collector.start_step(3, "parent", timestamp=0.0)
        collector.complete_step(3, "parent", timestamp=1.0)
        collector.create_step(
            3,
            "judge",
            parents=("parent",),
            request_type="judge",
            size_hint=2.0,
            size_unit="normalized_work",
            speculation_level=0.0,
            timestamp=1.0,
        )
        events_v11 = collector.event_dicts()
        events_v10 = []
        for event in events_v11:
            legacy = dict(event)
            legacy["schema_version"] = "1.0"
            legacy.pop("reason")
            events_v10.append(legacy)

        v11 = replay_workflow_hint_events(events_v11).to_dict()
        v10 = replay_workflow_hint_events(events_v10).to_dict()
        self.assertEqual(
            derive_graph_features(v11, "parent"),
            derive_graph_features(v10, "parent"),
        )
        self.assertTrue(derive_graph_features(v11, "parent").blocks_final)


class SelectionHistoryTest(unittest.TestCase):
    def test_smoothing_and_finalize_boundary_prevent_future_leakage(self):
        history = SelectionHistory(alpha=1.0, beta=1.0)
        key = SelectionHistoryKey("rag", "retrieval", "optional_evidence", 0)

        before, samples = history.probability(key)
        self.assertEqual((before, samples), (0.5, 0))
        # Merely knowing a current/future outcome cannot mutate history; only
        # the explicit finalized-workflow boundary below can do so.
        still_before, samples = history.probability(key)
        self.assertEqual((still_before, samples), (0.5, 0))

        history.record_finalized_workflow(
            "past-workflow",
            [SelectionObservation(key=key, selected=True)],
        )
        after, samples = history.probability(key)
        self.assertAlmostEqual(after, 2.0 / 3.0)
        self.assertEqual(samples, 1)
        with self.assertRaisesRegex(ValueError, "already recorded"):
            history.record_finalized_workflow("past-workflow", [])


class CriticalityPreflightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = run_criticality_preflight({"test_capacity": 16.0})

    def test_dynamic_fixtures_pass_shadow_semantic_checks(self):
        self.assertEqual(len(self.results), 4)
        for result in self.results:
            with self.subTest(fixture=result.network.fixture):
                self.assertTrue(result.score_records)
                self.assertTrue(all(result.checks.values()), result.checks)
                self.assertEqual(result.network.collector_summary["validation_errors"], 0)


class SimulatorShadowEquivalenceTest(unittest.TestCase):
    def test_shadow_outputs_scores_but_does_not_change_simulation(self):
        specs = experiment.generate_workload(
            seed=41,
            load="medium",
            duration=160,
            max_workflows=5,
        )
        common = dict(
            specs=specs,
            load="medium",
            seed=41,
            duration=160,
            max_time=1200,
        )
        off = experiment.Simulator(
            policy=experiment.RuleBasedFeedbackPolicy(9),
            criticality_scoring="off",
            **common,
        ).run()
        shadow = experiment.Simulator(
            policy=experiment.RuleBasedFeedbackPolicy(9),
            criticality_scoring="shadow",
            criticality_score_epoch=5,
            **common,
        ).run()

        score_records = shadow.pop("criticality_score_records")
        score_summary = shadow.pop("criticality_summary")
        self.assertTrue(score_records)
        self.assertTrue(all(row["affects_policy"] is False for row in score_records))
        self.assertTrue(score_summary["finite_scores"])
        self.assertIn("offline_selection_diagnostic", score_summary)
        self.assertNotIn("workflow_hint_events", shadow)
        self.assertEqual(shadow, off)

    def test_shadow_does_not_change_controller_q_table_or_actions(self):
        specs = experiment.generate_workload(23, "medium", 180, 5)
        off_policy = experiment.SpecNetAgentBanditPolicy(seed=5, train=False)
        shadow_policy = experiment.SpecNetAgentBanditPolicy(seed=5, train=False)
        off = experiment.Simulator(
            specs,
            off_policy,
            "medium",
            23,
            180,
            1200,
        ).run()
        shadow = experiment.Simulator(
            specs,
            shadow_policy,
            "medium",
            23,
            180,
            1200,
            criticality_scoring="shadow",
        ).run()
        self.assertEqual(off["action_counts"], shadow["action_counts"])
        self.assertEqual(dict(off_policy.q_values), dict(shadow_policy.q_values))
        self.assertEqual(dict(off_policy.counts), dict(shadow_policy.counts))


if __name__ == "__main__":
    unittest.main()
