"""Tests for v1.0/v1.1 workflow-hint replay and diagnostics."""

import copy
import unittest

try:
    from dynamic_dag_fixtures import run_all_fixtures
    from workflow_hint_replay import (
        WorkflowHintReplayError,
        audit_workflow_hint_events,
        compare_replay_to_engine_snapshot,
        normalize_workflow_hint_event,
        replay_workflow_hint_events,
    )
    from workflow_hints import WorkflowHintCollector
except ImportError:  # pragma: no cover - package-style imports
    from .dynamic_dag_fixtures import run_all_fixtures
    from .workflow_hint_replay import (
        WorkflowHintReplayError,
        audit_workflow_hint_events,
        compare_replay_to_engine_snapshot,
        normalize_workflow_hint_event,
        replay_workflow_hint_events,
    )
    from .workflow_hints import WorkflowHintCollector


class WorkflowHintReplayTest(unittest.TestCase):
    def make_events(self):
        collector = WorkflowHintCollector()
        collector.register_workflow(
            7,
            deadline_hint=100.0,
            timestamp=0.0,
            source="dynamic_runtime",
        )
        collector.create_step(
            7,
            "tool",
            parents=(),
            request_type="tool",
            size_hint=10.0,
            size_unit="normalized_work",
            speculation_level=0.0,
            timestamp=0.0,
        )
        collector.mark_ready(7, "tool", timestamp=0.0)
        collector.start_step(7, "tool", timestamp=0.0)
        collector.fail_step(7, "tool", timestamp=1.0)
        collector.retry_step(7, "tool", timestamp=2.0)
        collector.mark_ready(7, "tool", timestamp=2.0)
        collector.start_step(7, "tool", timestamp=2.0)
        collector.complete_step(7, "tool", timestamp=3.0)
        collector.create_step(
            7,
            "optional",
            parents=("tool",),
            dependency_kinds={"tool": "control_trigger"},
            request_type="retrieval",
            size_hint=20.0,
            size_unit="normalized_work",
            speculation_level=1.0,
            timestamp=3.0,
        )
        collector.mark_ready(7, "optional", timestamp=3.0)
        collector.start_step(7, "optional", timestamp=3.0)
        collector.cancel_step(
            7,
            "optional",
            timestamp=4.0,
            reason="judge_pruned",
        )
        collector.finalize_workflow(7, timestamp=4.0)
        return collector.event_dicts()

    def test_v1_1_replay_reconstructs_attempt_state_and_reason(self):
        snapshot = replay_workflow_hint_events(self.make_events())

        self.assertEqual(snapshot.workflow_id, "7")
        self.assertEqual(snapshot.active_steps, ())
        self.assertEqual(snapshot.steps["tool"]["state"], "completed")
        self.assertEqual(snapshot.steps["tool"]["attempt_id"], 1)
        self.assertEqual(snapshot.steps["tool"]["failure_count"], 1)
        self.assertEqual(snapshot.steps["optional"]["state"], "cancelled")
        self.assertEqual(snapshot.steps["optional"]["last_reason"], "judge_pruned")

    def test_v1_0_events_without_reason_remain_replayable(self):
        legacy_events = copy.deepcopy(self.make_events())
        for event in legacy_events:
            event["schema_version"] = "1.0"
            event.pop("reason")

        snapshot = replay_workflow_hint_events(legacy_events)

        self.assertEqual(snapshot.schema_versions, ("1.0",))
        self.assertEqual(snapshot.steps["tool"]["attempt_id"], 1)
        self.assertEqual(snapshot.steps["optional"]["last_reason"], "")

    def test_partial_replay_stops_at_requested_sequence(self):
        events = self.make_events()
        failed_sequence = next(
            event["sequence"] for event in events if event["event"] == "failed"
        )

        snapshot = replay_workflow_hint_events(events, upto_sequence=failed_sequence)

        self.assertEqual(snapshot.steps["tool"]["state"], "failed")
        self.assertNotIn("optional", snapshot.steps)

    def test_illegal_transition_returns_structured_diagnostic(self):
        events = self.make_events()
        invalid = [event for event in events if event["event"] != "ready"]

        audit = audit_workflow_hint_events(invalid)

        self.assertFalse(audit.valid)
        self.assertEqual(audit.diagnostics[0].code, "illegal_transition")
        self.assertEqual(audit.diagnostics[0].step_id, "tool")

    def test_v1_1_reasoned_event_requires_reason(self):
        event = next(
            event for event in self.make_events() if event["event"] == "failed"
        )
        event = dict(event)
        event["reason"] = ""

        with self.assertRaises(WorkflowHintReplayError) as raised:
            normalize_workflow_hint_event(event)

        self.assertEqual(raised.exception.diagnostic.code, "missing_event_reason")

    def test_replay_rejects_content_payload_fields(self):
        event = dict(self.make_events()[0])
        event["prompt"] = "secret"
        with self.assertRaises(WorkflowHintReplayError) as raised:
            normalize_workflow_hint_event(event)
        self.assertEqual(
            raised.exception.diagnostic.code,
            "forbidden_content_fields",
        )

    def test_all_dynamic_fixtures_match_engine_snapshot(self):
        for result in run_all_fixtures():
            with self.subTest(fixture=result.name):
                replay = replay_workflow_hint_events(result.events)
                diagnostics = compare_replay_to_engine_snapshot(
                    replay,
                    result.snapshot,
                )
                self.assertEqual(diagnostics, ())


if __name__ == "__main__":
    unittest.main()
