from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import specnet_agent as experiment
from specnet_agent.cli.experiment import main, parse_args


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "core_baseline.json"
TEST_OUTPUT = Path(__file__).resolve().parents[1] / "fixtures" / "_config_test.json"


class CoreContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_workload_generation_matches_pre_refactor_fixture(self) -> None:
        baseline = self.baseline
        specs = experiment.generate_workload(
            baseline["seed"], baseline["load"], baseline["duration"], baseline["max_workflows"]
        )
        self.assertEqual(len(specs), baseline["workflow_count"])
        expected = baseline["first_workflow"]
        first = specs[0]
        self.assertEqual(first.arrival_time, expected["arrival_time"])
        self.assertEqual(first.template, expected["template"])
        for name in ("deadline", "planner_size", "llm_size", "judge_size"):
            self.assertAlmostEqual(getattr(first, name), expected[name])
        self.assertEqual(first.branches[0].service_type, expected["first_branch_service_type"])
        self.assertAlmostEqual(first.branches[0].size, expected["first_branch_size"])
        self.assertEqual(first.branches[0].required, expected["first_branch_required"])

    def test_simulator_summary_matches_pre_refactor_fixture(self) -> None:
        baseline = self.baseline
        specs = experiment.generate_workload(
            baseline["seed"], baseline["load"], baseline["duration"], baseline["max_workflows"]
        )
        summary = experiment.Simulator(
            specs,
            experiment.CriticalPathOnlyPolicy(seed=5),
            baseline["load"],
            baseline["seed"],
            baseline["duration"],
            1200,
        ).run()
        for metric, expected in baseline["critical_path_summary"].items():
            self.assertAlmostEqual(float(summary[metric]), float(expected))

    def test_json_config_and_cli_precedence(self) -> None:
        try:
            TEST_OUTPUT.write_text(
                json.dumps({"schema_version": 1, "seed": 23, "loads": "light", "train_episodes": 2}),
                encoding="utf-8",
            )
            args = parse_args(["--config", str(TEST_OUTPUT), "--seed", "29"])
            self.assertEqual(args.seed, 29)
            self.assertEqual(args.loads, "light")
            self.assertEqual(args.train_episodes, 2)
        finally:
            TEST_OUTPUT.unlink(missing_ok=True)

    def test_json_config_rejects_unknown_keys(self) -> None:
        try:
            TEST_OUTPUT.write_text(json.dumps({"schema_version": 1, "unknown": 1}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                parse_args(["--config", str(TEST_OUTPUT)])
        finally:
            TEST_OUTPUT.unlink(missing_ok=True)

    def test_json_config_rejects_wrong_type_and_choice(self) -> None:
        invalid_configs = (
            {"schema_version": 1, "seed": "seven"},
            {"schema_version": 1, "epsilon_schedule": "unknown"},
        )
        try:
            for payload in invalid_configs:
                with self.subTest(payload=payload):
                    TEST_OUTPUT.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(SystemExit):
                        parse_args(["--config", str(TEST_OUTPUT)])
        finally:
            TEST_OUTPUT.unlink(missing_ok=True)

    def test_invalid_load_and_slack_weight_fail_before_experiment(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--loads", "invalid"])
        with self.assertRaises(SystemExit):
            main(["--slack-queue-weight", "-0.1"])

    def test_policy_action_weight_and_state_contract(self) -> None:
        spec = experiment.generate_workload(123, "light", 300, 1)[0]
        simulator = experiment.Simulator(
            [spec], experiment.CriticalPathOnlyPolicy(seed=5), "light", 123, 300, 1200
        )
        workflow = simulator.workflows[spec.workflow_id]
        simulator.time = spec.arrival_time
        policy = experiment.SpecNetAgentBanditPolicy(seed=5, train=False, epsilon=0.0)
        state = policy.state_key(simulator, workflow)
        self.assertEqual(state, ("low", "loose", "low_spec"))
        self.assertEqual(policy.decide_action(simulator, workflow), "full")
        flow = experiment.Flow(1, 0, "llm", 1.0, 1.0, "critical_bulk", "llm")
        self.assertEqual(experiment.StaticPriorityPolicy().flow_weight(flow, simulator), 5.0)
        self.assertEqual(experiment.CriticalPathOnlyPolicy().flow_weight(flow, simulator), 8.0)

    def test_new_and_historical_cli_outputs_are_byte_identical(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        new_output = repository / "tests" / "_contract_new"
        old_output = repository / "tests" / "_contract_old"
        common = [
            "--seed", "123", "--train-seed", "123", "--eval-seed", "123",
            "--duration", "300", "--max-workflows", "1", "--max-time", "800",
            "--train-episodes", "1", "--eval-runs", "1", "--loads", "light",
            "--checkpoint-episodes", "1",
        ]
        files = (
            "summary_by_run.csv", "summary_aggregate.csv", "workflow_results.csv",
            "action_counts.csv", "trained_agents.csv", "specnet_agent_model.json",
        )
        try:
            subprocess.check_call(
                [sys.executable, "-m", "specnet_agent.cli.experiment", *common,
                 "--output-dir", str(new_output)],
                cwd=repository,
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                [sys.executable, "specnet_agent_experiments/specnet_agent_experiment.py", *common,
                 "--output-dir", str(old_output)],
                cwd=repository,
                stdout=subprocess.DEVNULL,
            )
            for filename in files:
                with self.subTest(filename=filename):
                    self.assertEqual(
                        (new_output / filename).read_bytes(),
                        (old_output / filename).read_bytes(),
                    )
        finally:
            shutil.rmtree(new_output, ignore_errors=True)
            shutil.rmtree(old_output, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
