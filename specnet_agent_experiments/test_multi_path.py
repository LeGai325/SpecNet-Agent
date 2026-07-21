#!/usr/bin/env python3
"""Regression tests for the optional service-specific path scheduler."""

from __future__ import annotations

import csv
import io
import json
import shutil
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import specnet_agent_experiment as experiment


class MultiPathSchedulerTest(unittest.TestCase):
    def make_specs(self, count: int = 2):
        return [
            experiment.WorkflowSpec(
                workflow_id=index,
                arrival_time=0,
                template="coding",
                deadline=100.0,
                planner_size=1.0,
                branches=[],
                llm_size=1.0,
                judge_size=1.0,
                background_sizes=[],
            )
            for index in range(count)
        ]

    def make_simulator(self, policy=None, network_model: str = "service_paths"):
        specs = self.make_specs()
        return experiment.Simulator(
            specs,
            policy or experiment.FIFOPolicy(),
            "heavy",
            1,
            10,
            10,
            network_model=network_model,
        )

    def add_flow(self, simulator, workflow_id, service_type, role="normal"):
        workflow = simulator.workflows[workflow_id]
        return simulator.new_flow(
            workflow,
            service_type,
            100.0,
            role=role,
            stage="diagnostic",
        )

    def test_service_types_route_to_expected_paths(self) -> None:
        simulator = self.make_simulator()
        expected = {
            "planner": "control",
            "judge": "control",
            "retrieval": "data",
            "tool": "data",
            "storage": "data",
            "background": "data",
            "llm": "model",
        }
        for service_type, path_id in expected.items():
            with self.subTest(service_type=service_type):
                self.assertEqual(simulator.path_for_service_type(service_type), path_id)
        with self.assertRaises(ValueError):
            simulator.path_for_service_type("unknown")

    def test_single_bottleneck_routes_unknown_service_to_shared(self) -> None:
        simulator = self.make_simulator(network_model="single_bottleneck")
        self.assertEqual(simulator.path_for_service_type("unknown"), "shared")

    def test_single_bottleneck_capacity_override(self) -> None:
        simulator = experiment.Simulator(
            self.make_specs(),
            experiment.FIFOPolicy(),
            "heavy",
            1,
            10,
            10,
            network_model="single_bottleneck",
            single_bottleneck_capacity=48.0,
        )
        first = self.add_flow(simulator, 0, "retrieval")
        second = self.add_flow(simulator, 1, "llm")

        simulator.serve_active_flows()

        self.assertEqual(simulator.capacity, 48.0)
        self.assertEqual(simulator.path_capacities, {"shared": 48.0})
        self.assertEqual(simulator.flows[first].served, 24.0)
        self.assertEqual(simulator.flows[second].served, 24.0)
        self.assertEqual(simulator.total_capacity, 48.0)

    def test_capacity_override_rejects_invalid_uses(self) -> None:
        with self.assertRaises(ValueError):
            experiment.Simulator(
                self.make_specs(), experiment.FIFOPolicy(), "heavy", 1, 10, 10,
                single_bottleneck_capacity=0.0,
            )
        with self.assertRaises(ValueError):
            experiment.Simulator(
                self.make_specs(), experiment.FIFOPolicy(), "heavy", 1, 10, 10,
                network_model="service_paths",
                single_bottleneck_capacity=48.0,
            )

    def test_different_paths_receive_independent_capacity(self) -> None:
        simulator = self.make_simulator()
        retrieval = self.add_flow(simulator, 0, "retrieval")
        llm = self.add_flow(simulator, 1, "llm", role="critical_bulk")

        simulator.serve_active_flows()

        self.assertEqual(simulator.flows[retrieval].served, 16.0)
        self.assertEqual(simulator.flows[llm].served, 16.0)
        self.assertEqual(simulator.total_served, 32.0)
        self.assertEqual(simulator.total_capacity, 48.0)

    def test_same_path_preserves_fifo_and_policy_weights(self) -> None:
        fifo = self.make_simulator()
        first = self.add_flow(fifo, 0, "retrieval")
        second = self.add_flow(fifo, 1, "tool")
        fifo.serve_active_flows()
        self.assertEqual(fifo.flows[first].served, 8.0)
        self.assertEqual(fifo.flows[second].served, 8.0)

        static = self.make_simulator(experiment.StaticPriorityPolicy())
        retrieval = self.add_flow(static, 0, "retrieval")
        storage = self.add_flow(static, 1, "storage")
        static.serve_active_flows()
        self.assertEqual(static.flows[retrieval].served, 10.0)
        self.assertEqual(static.flows[storage].served, 6.0)

        critical = self.make_simulator(experiment.CriticalPathOnlyPolicy())
        critical_flow = self.add_flow(critical, 0, "retrieval", role="critical_control")
        normal_flow = self.add_flow(critical, 1, "tool", role="normal")
        critical.serve_active_flows()
        self.assertAlmostEqual(critical.flows[critical_flow].served, 12.8)
        self.assertAlmostEqual(critical.flows[normal_flow].served, 3.2)

    def test_small_flow_completion_recycles_capacity_within_each_path(self) -> None:
        simulator = self.make_simulator()
        small_data = simulator.new_flow(
            simulator.workflows[0], "retrieval", 4.0, "normal", "diagnostic"
        )
        large_data = self.add_flow(simulator, 1, "tool")
        small_model = simulator.new_flow(
            simulator.workflows[0], "llm", 3.0, "critical_bulk", "diagnostic"
        )
        large_model = self.add_flow(simulator, 1, "llm", role="critical_bulk")

        simulator.serve_active_flows()

        self.assertEqual(simulator.flows[small_data].completed_at, 1)
        self.assertEqual(simulator.flows[small_model].completed_at, 1)
        self.assertEqual(simulator.flows[large_data].served, 12.0)
        self.assertEqual(simulator.flows[large_model].served, 13.0)
        self.assertEqual(simulator.path_total_served["data"], 16.0)
        self.assertEqual(simulator.path_total_served["model"], 16.0)

    def test_per_path_capacity_and_global_controller_signals(self) -> None:
        simulator = self.make_simulator()
        self.add_flow(simulator, 0, "planner", role="critical_control")
        self.add_flow(simulator, 0, "retrieval")
        self.add_flow(simulator, 1, "llm", role="critical_bulk")

        self.assertAlmostEqual(simulator.remaining_active_bytes(), 300.0)
        self.assertAlmostEqual(simulator.congestion_ratio(), 300.0 / (16.0 * 12.0))
        self.assertAlmostEqual(simulator.slack_queue_work(), 300.0)
        simulator.serve_active_flows()

        for path_id in experiment.SERVICE_PATH_ORDER:
            self.assertLessEqual(simulator.path_total_served[path_id], 16.0)

    def test_finish_workflow_cancellation_accounting_is_unchanged(self) -> None:
        simulator = self.make_simulator()
        workflow = simulator.workflows[0]
        speculative = simulator.new_flow(
            workflow, "retrieval", 20.0, "speculative", "branch", speculative=True
        )
        background = simulator.new_flow(
            workflow, "background", 30.0, "background", "background", background=True
        )
        workflow.speculative_branch_flows.append(speculative)
        workflow.background_flows.append(background)
        simulator.flows[speculative].served = 7.0
        simulator.flows[background].served = 9.0

        simulator.finish_workflow(workflow)

        self.assertTrue(simulator.flows[speculative].cancelled)
        self.assertTrue(simulator.flows[background].cancelled)
        self.assertEqual(workflow.wasted_speculative_bytes, 7.0)
        self.assertEqual(workflow.background_bytes_served, 9.0)

    def test_default_model_matches_pre_change_fixed_seed_summary(self) -> None:
        specs = experiment.generate_workload(123, "light", 300, 2)
        simulator = experiment.Simulator(
            specs,
            experiment.CriticalPathOnlyPolicy(seed=5),
            "light",
            123,
            300,
            1200,
        )
        summary = simulator.run()
        expected = {
            "completed": 2,
            "mean_latency": 13,
            "p95_latency": 16.599999999999998,
            "p99_latency": 16.919999999999998,
            "deadline_miss_ratio": 0.0,
            "wasted_speculative_bytes_per_workflow": 64.29588965483224,
            "background_bytes_served_per_workflow": 17.06745416716222,
            "avg_quality": 1.0,
            "link_utilization": 0.25990099009900963,
            "avg_queue_pressure": 19.926616659175625,
        }
        for metric, value in expected.items():
            with self.subTest(metric=metric):
                self.assertEqual(summary[metric], value)
        self.assertEqual({flow.path_id for flow in simulator.flows.values()}, {"shared"})

    def test_training_and_validation_use_selected_network_model(self) -> None:
        observed_models = []
        original_simulator = experiment.Simulator

        class RecordingSimulator(original_simulator):
            def __init__(self, *args, **kwargs):
                observed_models.append(kwargs.get("network_model"))
                super().__init__(*args, **kwargs)

        with patch.object(experiment, "Simulator", RecordingSimulator):
            policy = experiment.train_specnet_agent(
                episodes=2,
                loads=["light"],
                duration=80,
                max_workflows=2,
                max_time=400,
                seed=31,
                quality_weight=1.6,
                checkpoint_episodes=[1],
                checkpoint_selection="best_validation",
                validation_seed=9021,
                checkpoint_eval_runs=1,
                network_model="service_paths",
            )
        self.assertTrue(observed_models)
        self.assertEqual(set(observed_models), {"service_paths"})
        self.assertTrue(all("validation" in item for item in policy.training_checkpoints))

    def test_service_paths_are_deterministic_for_fixed_seed(self) -> None:
        specs = experiment.generate_workload(321, "medium", 200, 4)
        summaries = []
        served = []
        for _ in range(2):
            simulator = experiment.Simulator(
                specs,
                experiment.FIFOPolicy(seed=9),
                "medium",
                321,
                200,
                800,
                network_model="service_paths",
            )
            summary = simulator.run()
            summaries.append(
                {key: value for key, value in summary.items() if key not in {"workflow_records", "path_records"}}
            )
            served.append(
                [(flow.flow_id, flow.path_id, flow.served, flow.completed_at) for flow in simulator.flows.values()]
            )
        self.assertEqual(summaries[0], summaries[1])
        self.assertEqual(served[0], served[1])

    def test_cli_path_results_contract_and_invalid_model(self) -> None:
        output_dir = Path(__file__).resolve().parent / "test_artifacts" / "multi_path"
        shutil.rmtree(output_dir.parent, ignore_errors=True)
        args = [
            "specnet_agent_experiment.py",
            "--network-model", "service_paths",
            "--output-dir", str(output_dir),
            "--loads", "light",
            "--train-episodes", "1",
            "--eval-runs", "1",
            "--duration", "20",
            "--max-workflows", "0",
            "--max-time", "40",
            "--checkpoint-episodes", "1",
        ]
        try:
            with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                experiment.main()
            with (output_dir / "path_results.csv").open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                self.assertEqual(
                    reader.fieldnames,
                    [
                        "load", "policy", "controller_variant", "state_features",
                        "quality_weight", "slack_queue_basis", "slack_queue_weight",
                        "train_seed", "eval_seed", "run", "seed", "network_model",
                        "path_id", "capacity", "total_served", "total_capacity",
                        "utilization", "avg_queue_pressure",
                    ],
                )
            self.assertEqual({row["path_id"] for row in rows}, set(experiment.SERVICE_PATH_ORDER))
            self.assertTrue(all(float(row["capacity"]) == 16.0 for row in rows))
            self.assertTrue(all(float(row["utilization"]) == 0.0 for row in rows))
            model = json.loads((output_dir / "specnet_agent_model.json").read_text(encoding="utf-8"))
            self.assertEqual(model["network_model"], "service_paths")
            self.assertEqual(
                model["path_capacities"],
                {"control": 16.0, "data": 16.0, "model": 16.0},
            )
        finally:
            shutil.rmtree(output_dir.parent, ignore_errors=True)

        with patch.object(sys, "argv", ["specnet_agent_experiment.py", "--network-model", "invalid"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                experiment.parse_args()


if __name__ == "__main__":
    unittest.main()
