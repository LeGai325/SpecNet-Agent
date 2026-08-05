#!/usr/bin/env python3
"""Tests for the content-free Workflow Hint Collector and simulator adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import specnet_agent_experiment as experiment
from workflow_hints import SCHEMA_VERSION, WorkflowHintCollector, WorkflowHintError


class WorkflowHintCollectorTest(unittest.TestCase):
    def make_collector(self, *, forward: bool = False) -> WorkflowHintCollector:
        collector = WorkflowHintCollector(allow_forward_references=forward)
        collector.register_workflow(
            7,
            deadline_hint=100.0,
            timestamp=0.0,
            source="dynamic_runtime",
        )
        return collector

    def create_running_step(
        self,
        collector: WorkflowHintCollector,
        step_id: str,
        *,
        parents=(),
        dependency_kinds=None,
        request_type: str = "tool",
        speculation_level: float = 0.0,
        timestamp: float = 0.0,
    ) -> None:
        collector.create_step(
            7,
            step_id,
            parents=parents,
            dependency_kinds=dependency_kinds,
            request_type=request_type,
            size_hint=10.0,
            size_unit="normalized_work",
            speculation_level=speculation_level,
            timestamp=timestamp,
        )
        collector.mark_ready(7, step_id, timestamp=timestamp)
        collector.start_step(7, step_id, timestamp=timestamp)

    def test_dynamic_lifecycle_dependencies_retry_and_selection(self) -> None:
        collector = self.make_collector()
        self.create_running_step(collector, "planner", request_type="planner")

        collector.create_step(
            7,
            "retrieval:0",
            parents=("planner",),
            dependency_kinds={"planner": "control_trigger"},
            request_type="retrieval",
            size_hint=20.0,
            size_unit="normalized_work",
            speculation_level=0.0,
            timestamp=1.0,
        )
        collector.mark_ready(7, "retrieval:0", timestamp=1.0)
        collector.start_step(7, "retrieval:0", timestamp=1.0)

        collector.create_step(
            7,
            "llm",
            parents=("retrieval:0",),
            dependency_kinds={"retrieval:0": "hard_dependency"},
            request_type="llm",
            size_hint=30.0,
            size_unit="normalized_work",
            speculation_level=0.0,
            timestamp=1.0,
        )
        with self.assertRaisesRegex(WorkflowHintError, "hard dependencies"):
            collector.mark_ready(7, "llm", timestamp=1.0)

        collector.create_step(
            7,
            "optional:0",
            parents=("retrieval:0",),
            dependency_kinds={"retrieval:0": "optional_evidence"},
            request_type="tool",
            size_hint=12.0,
            size_unit="normalized_work",
            speculation_level=1.0,
            timestamp=1.0,
        )
        collector.mark_ready(7, "optional:0", timestamp=1.0)
        collector.start_step(7, "optional:0", timestamp=1.0)
        collector.complete_step(7, "optional:0", timestamp=2.0)
        collector.mark_selected(7, "optional:0", timestamp=3.0)

        collector.complete_step(7, "planner", timestamp=2.0)
        collector.fail_step(7, "retrieval:0", timestamp=3.0)
        self.assertEqual(collector.retry_step(7, "retrieval:0", timestamp=4.0), 1)
        collector.mark_ready(7, "retrieval:0", timestamp=4.0)
        collector.start_step(7, "retrieval:0", timestamp=4.0)
        collector.complete_step(7, "retrieval:0", timestamp=5.0)
        collector.mark_ready(7, "llm", timestamp=5.0)
        collector.start_step(7, "llm", timestamp=5.0)
        collector.complete_step(7, "llm", timestamp=6.0)
        collector.finalize_workflow(7, timestamp=6.0)

        self.assertEqual(collector.step(7, "retrieval:0").attempt_id, 1)
        self.assertTrue(collector.step(7, "optional:0").selected)
        self.assertEqual(collector.summary()["validation_errors"], 0)
        self.assertEqual(collector.summary()["workflow_statuses"], {"completed": 1})
        self.assertEqual(
            [event.event for event in collector.events if event.step_id == "retrieval:0"],
            ["created", "ready", "started", "failed", "retried", "ready", "started", "completed"],
        )

    def test_missing_parent_and_cycle_are_rejected(self) -> None:
        collector = self.make_collector()
        with self.assertRaisesRegex(WorkflowHintError, "missing parent"):
            collector.create_step(
                7,
                "child",
                parents=("unknown",),
                request_type="tool",
                size_hint=1.0,
                size_unit="bytes",
                speculation_level=0.0,
                timestamp=0.0,
            )

        forward = self.make_collector(forward=True)
        forward.create_step(
            7,
            "first",
            parents=("second",),
            request_type="tool",
            size_hint=1.0,
            size_unit="bytes",
            speculation_level=0.0,
            timestamp=0.0,
        )
        with self.assertRaisesRegex(WorkflowHintError, "cycle"):
            forward.create_step(
                7,
                "second",
                parents=("first",),
                request_type="tool",
                size_hint=1.0,
                size_unit="bytes",
                speculation_level=0.0,
                timestamp=0.0,
            )

    def test_invalid_hint_values_are_rejected(self) -> None:
        collector = self.make_collector()
        invalid_values = (
            {"size_hint": -1.0, "speculation_level": 0.0},
            {"size_hint": 1.0, "speculation_level": 1.1},
        )
        for index, values in enumerate(invalid_values):
            with self.subTest(values=values), self.assertRaises(WorkflowHintError):
                collector.create_step(
                    7,
                    f"step:{index}",
                    parents=(),
                    request_type="tool",
                    size_unit="bytes",
                    timestamp=0.0,
                    **values,
                )

    def test_public_event_schema_has_no_content_payload_fields(self) -> None:
        collector = self.make_collector()
        self.create_running_step(collector, "planner", request_type="planner")
        event = collector.event_dicts()[0]
        self.assertEqual(event["schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            set(event),
            {
                "schema_version",
                "sequence",
                "workflow_id",
                "step_id",
                "attempt_id",
                "parents",
                "dependency_kinds",
                "request_type",
                "deadline_hint",
                "size_hint",
                "size_unit",
                "speculation_level",
                "event",
                "timestamp",
                "source",
            },
        )
        serialized = json.dumps(event).lower()
        for forbidden in ("prompt", "content", "payload", "response_text", "tool_args"):
            self.assertNotIn(forbidden, serialized)


class WorkflowHintSimulatorTest(unittest.TestCase):
    def make_specs(self):
        return experiment.generate_workload(71, "light", 120, 2)

    def run_simulator(self, mode: str):
        simulator = experiment.Simulator(
            self.make_specs(),
            experiment.FIFOPolicy(seed=17),
            "light",
            71,
            120,
            800,
            workflow_hints=mode,
        )
        return simulator, simulator.run()

    def test_fixed_workflow_adapter_records_valid_complete_dags(self) -> None:
        simulator, summary = self.run_simulator("record")
        hint_summary = summary["workflow_hint_summary"]

        self.assertEqual(hint_summary["workflows_registered"], len(simulator.completed_workflows))
        self.assertEqual(hint_summary["workflows_finalized"], len(simulator.completed_workflows))
        self.assertEqual(hint_summary["validation_errors"], 0)
        self.assertEqual(
            hint_summary["workflow_statuses"],
            {"completed": len(simulator.completed_workflows)},
        )
        self.assertGreater(hint_summary["steps_recorded"], 0)
        simulator.workflow_hint_collector.validate_all(require_terminal=True)

        first_id = simulator.completed_workflows[0].spec.workflow_id
        collector = simulator.workflow_hint_collector
        self.assertEqual(collector.step(first_id, "planner").parents, ())
        self.assertEqual(collector.step(first_id, "llm").state, "completed")
        self.assertEqual(collector.step(first_id, "judge").parents, ("llm",))
        branch_steps = [
            step
            for step in collector.workflows[str(first_id)].steps.values()
            if step.step_id.startswith("branch:")
        ]
        self.assertTrue(branch_steps)
        self.assertTrue(all(step.parents == ("planner",) for step in branch_steps))
        self.assertTrue(
            all(step.source == "fixed_template_adapter" for step in branch_steps)
        )

    def test_record_mode_is_behaviorally_identical_to_off(self) -> None:
        off_simulator, off = self.run_simulator("off")
        record_simulator, record = self.run_simulator("record")
        ignored = {"workflow_hint_events", "workflow_hint_summary"}
        self.assertEqual(
            {key: value for key, value in off.items() if key not in ignored},
            {key: value for key, value in record.items() if key not in ignored},
        )
        self.assertNotIn("workflow_hint_events", off)
        self.assertNotIn("workflow_hint_summary", off)
        self.assertEqual(off_simulator.flows, record_simulator.flows)
        self.assertEqual(off_simulator.time, record_simulator.time)

    def test_invalid_workflow_hint_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "workflow-hint mode"):
            experiment.Simulator(
                self.make_specs(),
                experiment.FIFOPolicy(),
                "light",
                1,
                10,
                100,
                workflow_hints="enabled",
            )

    def test_policy_filter_defaults_to_specnet_family_and_supports_all(self) -> None:
        selectors = experiment.parse_workflow_hint_policy_selectors("specnet_agent")
        self.assertTrue(
            experiment.should_record_workflow_hints(
                "record", selectors, "specnet_agent_full_qw_1_60"
            )
        )
        self.assertFalse(
            experiment.should_record_workflow_hints(
                "record", selectors, "critical_path_only"
            )
        )
        self.assertTrue(
            experiment.should_record_workflow_hints(
                "record", ["all"], "critical_path_only"
            )
        )
        self.assertFalse(
            experiment.should_record_workflow_hints(
                "off", ["all"], "specnet_agent"
            )
        )

    def test_timeout_cancels_open_hint_steps_before_finalization(self) -> None:
        spec = experiment.WorkflowSpec(
            workflow_id=0,
            arrival_time=0,
            template="coding",
            deadline=20.0,
            planner_size=100.0,
            branches=[],
            llm_size=1.0,
            judge_size=1.0,
            background_sizes=[],
        )
        simulator = experiment.Simulator(
            [spec],
            experiment.FIFOPolicy(),
            "light",
            1,
            1,
            1,
            workflow_hints="record",
        )

        summary = simulator.run()

        self.assertEqual(summary["workflow_hint_summary"]["workflow_statuses"], {"timed_out": 1})
        self.assertEqual(simulator.workflow_hint_collector.step(0, "planner").state, "cancelled")
        simulator.workflow_hint_collector.validate_all(require_terminal=True)

    def test_cli_record_mode_writes_jsonl_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory)
            argv = [
                "specnet_agent_experiment.py",
                "--output-dir",
                str(output),
                "--loads",
                "light",
                "--train-episodes",
                "1",
                "--checkpoint-episodes",
                "1",
                "--eval-runs",
                "1",
                "--duration",
                "500",
                "--max-time",
                "1200",
                "--max-workflows",
                "1",
                "--workflow-hints",
                "record",
            ]
            with (
                patch.object(sys, "argv", argv),
                redirect_stdout(StringIO()),
                redirect_stderr(StringIO()),
            ):
                experiment.main()

            jsonl_path = output / "workflow_hints.jsonl"
            summary_path = output / "workflow_hint_summary.json"
            self.assertTrue(jsonl_path.exists())
            self.assertTrue(summary_path.exists())
            rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
            self.assertTrue(rows)
            self.assertTrue(all(row["schema_version"] == SCHEMA_VERSION for row in rows))
            report = json.loads(summary_path.read_text())
            self.assertEqual(report["schema_version"], SCHEMA_VERSION)
            self.assertEqual(report["run_count"], 1)
            self.assertGreater(report["totals"]["events_recorded"], 0)
            self.assertEqual(report["totals"]["validation_errors"], 0)


if __name__ == "__main__":
    unittest.main()
