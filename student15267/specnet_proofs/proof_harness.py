#!/usr/bin/env python3
"""Isolated evidence harness for the three SpecNet-Agent claims.

The upstream simulator is loaded read-only. All changed state definitions,
policies, experiment drivers, and outputs live in student15267/specnet_proofs.
Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.util
import itertools
import json
import math
import os
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Type


LEGACY_SOURCE = Path(
    "/home/gl/fyf/organized_code_files/organized_code_files/source_snapshot/"
    "specnet_agent_experiments/specnet_agent_experiment.py"
)
ROOT = Path(__file__).resolve().parent
LOCAL_SOURCE = (
    ROOT.parents[1]
    / "organized_code_files/source_snapshot/specnet_agent_experiments/specnet_agent_experiment.py"
)
DEFAULT_SOURCE = LOCAL_SOURCE if LOCAL_SOURCE.is_file() else LEGACY_SOURCE
PROTOCOL_VERSION = "2026-07-19.v2"


def load_upstream(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"upstream simulator not found: {path}")
    spec = importlib.util.spec_from_file_location("specnet_upstream_readonly", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load upstream simulator: {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses inspect sys.modules while the module is executed.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


UPSTREAM_PATH = Path(os.environ.get("SPECNET_UPSTREAM", str(DEFAULT_SOURCE))).resolve()
up = load_upstream(UPSTREAM_PATH)

CONGESTION = ("low", "medium", "high")
SLACK = ("tight", "normal", "loose")
SPEC = ("low_spec", "mid_spec", "high_spec")
ALL_STATES = tuple(itertools.product(CONGESTION, SLACK, SPEC))
ACTION_STRENGTH = {
    "critical_only": 0,
    "conservative": 1,
    "moderate": 2,
    "recovery": 3,
    "full": 4,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def save_bandit(path: Path, policy: "AuditedBandit") -> None:
    """Persist the frozen table so long experiment stages can be resumed."""
    rows = []
    for state, values in policy.q_values.items():
        rows.append(
            {
                "state": list(state),
                "q_values": dict(values),
                "counts": dict(policy.counts.get(state, Counter())),
            }
        )
    write_json(path, {"policy": policy.name, "states": rows})


def load_bandit(path: Path) -> "AuditedBandit":
    payload = json.loads(path.read_text(encoding="utf-8"))
    policy = AuditedBandit(seed=7, train=False, epsilon=0.0)
    for row in payload["states"]:
        state = tuple(row["state"])
        policy.q_values[state].update({key: float(value) for key, value in row["q_values"].items()})
        policy.counts[state].update({key: int(value) for key, value in row["counts"].items()})
    policy.set_evaluation_mode()
    return policy


def scaled_workload(
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    deadline_scale: float,
    optional_scale: float,
):
    specs = copy.deepcopy(up.generate_workload(seed, load, duration, max_workflows))
    for workflow in specs:
        workflow.deadline *= deadline_scale
        for branch in workflow.branches:
            if not branch.required:
                branch.size *= optional_scale
        workflow.background_sizes = [size * optional_scale for size in workflow.background_sizes]
    return specs


class ProofSimulator(up.Simulator):
    """Simulator with action-independent effective slack and audit records."""

    def __init__(self, *args, capacity_scale: float = 1.0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.capacity *= capacity_scale
        self.capacity_scale = capacity_scale

    def estimated_remaining_critical_time(self, workflow) -> float:
        # Uses only currently observable work and static workflow hints. It does
        # not use the selected action, future completion time, or test outcome.
        required = sum(branch.size for branch in workflow.spec.branches if branch.required)
        own_bytes = required + workflow.spec.llm_size + workflow.spec.judge_size
        active_critical = sum(
            flow.remaining
            for flow in self.active_flows()
            if flow.required or flow.role in ("critical_control", "critical_bulk")
        )
        all_active = self.remaining_active_bytes()
        template_prior = 0.22 * up.TEMPLATES[workflow.spec.template]["deadline_base"]
        serialization = (own_bytes + active_critical) / max(self.capacity, 1e-9)
        contention = 0.70 * all_active / max(self.capacity, 1e-9)
        return max(1.0, template_prior + serialization + contention)

    def workflow_slack_ratio(self, workflow) -> float:
        remaining_budget = workflow.deadline_time - self.time
        estimate = self.estimated_remaining_critical_time(workflow)
        return (remaining_budget - estimate) / max(estimate, 1e-9)

    def workflow_slack_bucket(self, workflow) -> str:
        margin = self.workflow_slack_ratio(workflow)
        if margin < 0.25:
            return "tight"
        if margin < 1.0:
            return "normal"
        return "loose"

    def observable_state(self, workflow) -> Tuple[str, str, str]:
        return (
            self.congestion_level(),
            self.workflow_slack_bucket(workflow),
            self.speculative_pressure_bucket(),
        )

    def spawn_branches(self, workflow) -> None:
        workflow.observable_state = self.observable_state(workflow)
        workflow.slack_margin = self.workflow_slack_ratio(workflow)
        workflow.raw_action = None
        workflow.final_action = None
        workflow.guard_reason = "disabled"
        super().spawn_branches(workflow)
        workflow.raw_action = workflow.action
        workflow.final_action = workflow.action

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        by_id = {workflow.spec.workflow_id: workflow for workflow in self.completed_workflows}
        for record in result["workflow_records"]:
            workflow = by_id[record["workflow_id"]]
            state = getattr(workflow, "observable_state", None)
            record.update(
                {
                    "congestion_bucket": state[0] if state else "unseen",
                    "slack_bucket": state[1] if state else "unseen",
                    "spec_pressure_bucket": state[2] if state else "unseen",
                    "slack_margin": getattr(workflow, "slack_margin", ""),
                    "decision_state": str(workflow.decision_state),
                    "raw_action": getattr(workflow, "raw_action", workflow.action),
                    "final_action": getattr(workflow, "final_action", workflow.action),
                    "guard_reason": getattr(workflow, "guard_reason", "disabled"),
                    "reward": self.workflow_reward(workflow),
                }
            )
        return result


class AuditedBandit(up.SpecNetAgentBanditPolicy):
    name = "full"


class NoCongestionBandit(AuditedBandit):
    name = "no_congestion"

    def state_key(self, sim, workflow):
        return ("all_congestion", sim.workflow_slack_bucket(workflow), sim.speculative_pressure_bucket())


class NoSlackBandit(AuditedBandit):
    name = "no_slack"

    def state_key(self, sim, workflow):
        return (sim.congestion_level(), "all_slack", sim.speculative_pressure_bucket())


class NoSpecPressureBandit(AuditedBandit):
    name = "no_spec_pressure"

    def state_key(self, sim, workflow):
        return (sim.congestion_level(), sim.workflow_slack_bucket(workflow), "all_spec")


class CongestionOnlyBandit(AuditedBandit):
    name = "congestion_only"

    def state_key(self, sim, workflow):
        return (sim.congestion_level(), "all_slack", "all_spec")


ABLATIONS: Tuple[Type[AuditedBandit], ...] = (
    AuditedBandit,
    NoCongestionBandit,
    NoSlackBandit,
    NoSpecPressureBandit,
    CongestionOnlyBandit,
)


class TunedRiskRulePolicy(up.CriticalPathOnlyPolicy):
    name = "global_tuned_rule"

    def __init__(self, params: Mapping[str, float], seed: int = 0, name: str = "global_tuned_rule") -> None:
        super().__init__(seed)
        self.params = dict(params)
        self.name = name

    def decide_action(self, sim, workflow) -> str:
        c = {"low": 0.0, "medium": 0.5, "high": 1.0}[sim.congestion_level()]
        s = {"loose": 0.0, "normal": 0.5, "tight": 1.0}[sim.workflow_slack_bucket(workflow)]
        p = {"low_spec": 0.0, "mid_spec": 0.5, "high_spec": 1.0}[sim.speculative_pressure_bucket()]
        risk = (
            self.params["wc"] * c
            + self.params["ws"] * s
            + self.params["wp"] * p
            + self.params["wcs"] * c * s
            + self.params["wcp"] * c * p
        )
        thresholds = [self.params[f"t{i}"] for i in range(4)]
        if risk >= thresholds[3]:
            action = "critical_only"
        elif risk >= thresholds[2]:
            action = "conservative"
        elif risk >= thresholds[1]:
            action = "moderate"
        elif risk >= thresholds[0]:
            action = "recovery"
        else:
            action = "full"
        self.action_counter[action] += 1
        workflow.decision_state = (sim.congestion_level(), sim.workflow_slack_bucket(workflow), sim.speculative_pressure_bucket())
        return action


class FixedActionPolicy(up.CriticalPathOnlyPolicy):
    def __init__(self, action: str, seed: int = 0) -> None:
        super().__init__(seed)
        self.action = action
        self.name = f"fixed_{action}"

    def decide_action(self, sim, workflow) -> str:
        self.action_counter[self.action] += 1
        workflow.decision_state = sim.observable_state(workflow)
        return self.action


def scenarios(mode: str) -> List[Tuple[str, float, float, float]]:
    if mode == "smoke":
        # Broad pilot scales intentionally test whether all buckets can activate.
        deadline_scales = (0.30, 0.65, 1.20)
        optional_scales = (0.45, 1.65)
        capacity_scales = (0.72, 1.25)
    else:
        deadline_scales = (0.30, 0.65, 1.20)
        optional_scales = (0.50, 1.00, 1.50)
        capacity_scales = (0.70, 1.00, 1.30)
    return list(itertools.product(up.LOAD_CONFIG.keys(), deadline_scales, optional_scales, capacity_scales))


def train_bandit(
    policy_class: Type[AuditedBandit],
    seed: int,
    episodes: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    matrix: Sequence[Tuple[str, float, float, float]],
) -> AuditedBandit:
    policy = policy_class(seed=seed, train=True, epsilon=0.18, learning_rate=0.25)
    for episode in range(episodes):
        load, deadline_scale, optional_scale, capacity_scale = matrix[episode % len(matrix)]
        workload_seed = seed + 10000 + episode
        specs = scaled_workload(
            workload_seed, load, duration, max_workflows, deadline_scale, optional_scale
        )
        sim = ProofSimulator(
            specs, policy, load, workload_seed, duration, max_time, capacity_scale=capacity_scale
        )
        sim.run()
    policy.set_evaluation_mode()
    return policy


def run_once(
    policy,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Dict[str, object]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = scaled_workload(
        workload_seed, load, duration, max_workflows, deadline_scale, optional_scale
    )
    sim = ProofSimulator(
        specs, policy, load, workload_seed, duration, max_time, capacity_scale=capacity_scale
    )
    summary = sim.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
        }
    )
    return summary


def compact_summary(summary: Mapping[str, object]) -> Dict[str, object]:
    return {key: value for key, value in summary.items() if key not in ("workflow_records", "action_counts")}


def coverage_rows(
    split: str,
    summaries: Iterable[Mapping[str, object]],
) -> List[Dict[str, object]]:
    counts: Counter[Tuple[str, str, str, str, float, float, float]] = Counter()
    for summary in summaries:
        for row in summary["workflow_records"]:
            state = (
                row["congestion_bucket"],
                row["slack_bucket"],
                row["spec_pressure_bucket"],
            )
            counts[(summary["load"], *state, summary["deadline_scale"], summary["optional_scale"], summary["capacity_scale"])] += 1
    return [
        {
            "split": split,
            "load": load,
            "congestion_bucket": congestion,
            "slack_bucket": slack,
            "spec_pressure_bucket": spec,
            "deadline_scale": deadline,
            "optional_scale": optional,
            "capacity_scale": capacity,
            "visit_count": count,
        }
        for (load, congestion, slack, spec, deadline, optional, capacity), count in sorted(counts.items())
    ]


def training_coverage_rows(policy: AuditedBandit) -> List[Dict[str, object]]:
    rows = []
    for state in ALL_STATES:
        count = sum(policy.counts.get(state, Counter()).values())
        rows.append(
            {
                "split": "train",
                "load": "all",
                "congestion_bucket": state[0],
                "slack_bucket": state[1],
                "spec_pressure_bucket": state[2],
                "deadline_scale": "matrix",
                "optional_scale": "matrix",
                "capacity_scale": "matrix",
                "visit_count": count,
            }
        )
    return rows


def state_metrics(records: Sequence[Mapping[str, object]]) -> Dict[str, float]:
    latencies = [float(row["latency"]) for row in records]
    return {
        "n": len(records),
        "p99_latency": up.percentile(latencies, 0.99),
        "deadline_miss_ratio": statistics.mean(float(row["deadline_miss"]) for row in records),
        "waste": statistics.mean(float(row["wasted_speculative_bytes"]) for row in records),
        "quality": statistics.mean(float(row["quality"]) for row in records),
        "normalized_latency": statistics.mean(
            float(row["latency"]) / max(1.0, float(row["deadline"])) for row in records
        ),
    }


def bootstrap_ci(values: Sequence[float], seed: int = 15267, draws: int = 4000) -> Tuple[float, float]:
    if not values:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        means.append(statistics.mean(rng.choice(values) for _ in values))
    return (up.percentile(means, 0.025), up.percentile(means, 0.975))


def stratified_mean(values: Sequence[Tuple[object, float]]) -> float:
    """Average within fixed scenarios first so dense slices cannot dominate."""
    grouped: Dict[object, List[float]] = defaultdict(list)
    for stratum, value in values:
        grouped[stratum].append(value)
    return statistics.mean(statistics.mean(items) for items in grouped.values())


def stratified_bootstrap_ci(
    values: Sequence[Tuple[object, float]], seed: int = 15267, draws: int = 4000
) -> Tuple[float, float]:
    """Paired bootstrap over runs within each fixed scenario.

    The scenario matrix is deliberately heterogeneous. Treating all available
    run/scenario cells as one exchangeable pool lets scenarios with more slice
    members receive extra weight. This bootstrap preserves the scenario mix
    and resamples only the repeated workload runs inside each scenario.
    """
    if not values:
        return (math.nan, math.nan)
    grouped: Dict[object, List[float]] = defaultdict(list)
    for stratum, value in values:
        grouped[stratum].append(value)
    rng = random.Random(seed)
    estimates = []
    strata = sorted(grouped, key=str)
    for _ in range(draws):
        stratum_means = []
        for stratum in strata:
            items = grouped[stratum]
            stratum_means.append(statistics.mean(rng.choice(items) for _ in items))
        estimates.append(statistics.mean(stratum_means))
    return (up.percentile(estimates, 0.025), up.percentile(estimates, 0.975))


def stratified_effect_dz(values: Sequence[Tuple[object, float]]) -> float:
    """Standardize the equal-weight scenario means for a paired effect size."""
    grouped: Dict[object, List[float]] = defaultdict(list)
    for stratum, value in values:
        grouped[stratum].append(value)
    scenario_means = [statistics.mean(items) for items in grouped.values()]
    if len(scenario_means) < 2:
        return math.nan
    spread = statistics.stdev(scenario_means)
    if spread <= 1e-15:
        return math.inf if statistics.mean(scenario_means) > 0 else -math.inf if statistics.mean(scenario_means) < 0 else 0.0
    return statistics.mean(scenario_means) / spread


def stratified_randomization_p(
    values: Sequence[Tuple[object, float]], seed: int, draws: int = 20000
) -> float:
    """Paired sign-flip test using the same equal-scenario estimand as the CI."""
    if not values:
        return math.nan
    observed = abs(stratified_mean(values))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(draws):
        permuted = [
            (stratum, value if rng.random() < 0.5 else -value)
            for stratum, value in values
        ]
        extreme += abs(stratified_mean(permuted)) >= observed - 1e-15
    return (extreme + 1) / (draws + 1)


def paired_randomization_p(values: Sequence[float], seed: int, draws: int = 20000) -> float:
    """Two-sided paired sign-flip randomization test at the run/scenario level."""
    if not values:
        return math.nan
    observed = abs(statistics.mean(values))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(draws):
        permuted = abs(statistics.mean(value if rng.random() < 0.5 else -value for value in values))
        extreme += permuted >= observed - 1e-15
    return (extreme + 1) / (draws + 1)


def holm_adjust(p_values: Mapping[str, float]) -> Dict[str, float]:
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    adjusted: Dict[str, float] = {}
    running = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (total - rank) * value))
        adjusted[name] = running
    return adjusted


def ablation_slice_rows(workflow_rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    """Build matched RQ1 slices using the full policy as the state anchor."""
    slice_specs = {
        "H1-C": ("no_congestion", "congestion_bucket", "high", "p99_latency"),
        "H1-S": ("no_slack", "slack_bucket", "tight", "normalized_latency"),
        "H1-P": ("no_spec_pressure", "spec_pressure_bucket", "high_spec", "waste"),
    }
    indexed = {
        (str(row["policy"]), int(row["run"]), int(row["scenario"]), int(row["workflow_id"])): row
        for row in workflow_rows
    }
    grouped: Dict[Tuple[str, int, int], List[Mapping[str, object]]] = defaultdict(list)
    for row in workflow_rows:
        grouped[(str(row["policy"]), int(row["run"]), int(row["scenario"]))].append(row)
    units = sorted({(run, scenario) for _, run, scenario in grouped})
    slice_rows: List[Dict[str, object]] = []
    primary_p: Dict[str, float] = {}
    for hypothesis, (ablation, field, target, primary_metric) in slice_specs.items():
        deltas: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
        for run_index, scenario_index in units:
            full_rows = [
                row for row in grouped.get(("full", run_index, scenario_index), [])
                if row[field] == target
            ]
            workflow_ids = [int(row["workflow_id"]) for row in full_rows]
            ablation_rows = [
                indexed[(ablation, run_index, scenario_index, workflow_id)]
                for workflow_id in workflow_ids
                if (ablation, run_index, scenario_index, workflow_id) in indexed
            ]
            if not full_rows or len(ablation_rows) != len(full_rows):
                continue
            metrics = {"full": state_metrics(full_rows), ablation: state_metrics(ablation_rows)}
            for metric in ("p99_latency", "deadline_miss_ratio", "waste", "quality", "normalized_latency"):
                deltas[metric].append(
                    (scenario_index, metrics[ablation][metric] - metrics["full"][metric])
                )
        for metric, values in deltas.items():
            low, high = stratified_bootstrap_ci(values, seed=15267 + len(slice_rows))
            is_primary = metric == primary_metric
            p_value = stratified_randomization_p(values, seed=25267 + len(slice_rows)) if is_primary else ""
            if is_primary:
                primary_p[hypothesis] = float(p_value)
            slice_rows.append(
                {
                    "hypothesis": hypothesis,
                    "slice": f"full_reference:{field}={target}",
                    "ablation": ablation,
                    "metric": metric,
                    "primary_metric": int(is_primary),
                    "paired_units": len(values),
                    "scenario_strata": len({scenario for scenario, _ in values}),
                    "mean_delta_ablation_minus_full": stratified_mean(values),
                    "standardized_effect_dz": stratified_effect_dz(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "randomization_p": p_value,
                    "holm_adjusted_p": "",
                }
            )
    adjusted = holm_adjust(primary_p)
    for row in slice_rows:
        if row["primary_metric"]:
            row["holm_adjusted_p"] = adjusted[str(row["hypothesis"])]
    return slice_rows


def ablation_slice_rows_from_csv(path: Path) -> List[Dict[str, object]]:
    """Memory-bounded reconstruction for a large completed workflow audit."""
    specs = {
        "H1-C": ("no_congestion", "congestion_bucket", "high", "p99_latency"),
        "H1-S": ("no_slack", "slack_bucket", "tight", "normalized_latency"),
        "H1-P": ("no_spec_pressure", "spec_pressure_bucket", "high_spec", "waste"),
    }
    fields = ("latency", "deadline", "deadline_miss", "wasted_speculative_bytes", "quality")
    members: Dict[str, set] = {name: set() for name in specs}
    grouped: Dict[str, Dict[str, Dict[Tuple[int, int], List[Dict[str, object]]]]] = {
        name: {"full": defaultdict(list), ablation: defaultdict(list)}
        for name, (ablation, _, _, _) in specs.items()
    }
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] != "full":
                continue
            run, scenario, workflow_id = int(row["run"]), int(row["scenario"]), int(row["workflow_id"])
            compact = {field: row[field] for field in fields}
            for name, (_, state_field, target, _) in specs.items():
                if row[state_field] == target:
                    members[name].add((run, scenario, workflow_id))
                    grouped[name]["full"][(run, scenario)].append(compact)
    ablation_to_hypothesis = {value[0]: name for name, value in specs.items()}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name = ablation_to_hypothesis.get(row["policy"])
            if name is None:
                continue
            key = (int(row["run"]), int(row["scenario"]), int(row["workflow_id"]))
            if key in members[name]:
                grouped[name][row["policy"]][key[:2]].append({field: row[field] for field in fields})

    rows: List[Dict[str, object]] = []
    primary_p: Dict[str, float] = {}
    for hypothesis, (ablation, state_field, target, primary_metric) in specs.items():
        deltas: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
        for unit, full_records in grouped[hypothesis]["full"].items():
            ablation_records = grouped[hypothesis][ablation].get(unit, [])
            if not full_records or len(full_records) != len(ablation_records):
                continue
            metrics = {"full": state_metrics(full_records), ablation: state_metrics(ablation_records)}
            for metric in ("p99_latency", "deadline_miss_ratio", "waste", "quality", "normalized_latency"):
                deltas[metric].append((unit[1], metrics[ablation][metric] - metrics["full"][metric]))
        for metric, values in deltas.items():
            is_primary = metric == primary_metric
            p_value = stratified_randomization_p(values, 35267 + len(rows)) if is_primary else ""
            if is_primary:
                primary_p[hypothesis] = float(p_value)
            low, high = stratified_bootstrap_ci(values, seed=45267 + len(rows))
            rows.append(
                {
                    "hypothesis": hypothesis,
                    "slice": f"full_reference:{state_field}={target}",
                    "ablation": ablation,
                    "metric": metric,
                    "primary_metric": int(is_primary),
                    "paired_units": len(values),
                    "scenario_strata": len({scenario for scenario, _ in values}),
                    "mean_delta_ablation_minus_full": stratified_mean(values),
                    "standardized_effect_dz": stratified_effect_dz(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "randomization_p": p_value,
                    "holm_adjusted_p": "",
                }
            )
    adjusted = holm_adjust(primary_p)
    for row in rows:
        if row["primary_metric"]:
            row["holm_adjusted_p"] = adjusted[str(row["hypothesis"])]
    return rows


def rq1(
    out: Path,
    mode: str,
    matrix: Sequence[Tuple[str, float, float, float]],
    duration: int,
    max_workflows: int,
    max_time: int,
    train_episodes: int,
    eval_runs: int,
) -> Tuple[AuditedBandit, List[Dict[str, object]]]:
    trained = {
        cls.name: train_bandit(cls, 7, train_episodes, duration, max_workflows, max_time, matrix)
        for cls in ABLATIONS
    }
    summaries: List[Dict[str, object]] = []
    by_run: List[Dict[str, object]] = []
    workflow_rows: List[Dict[str, object]] = []
    # Balanced subset keeps the full run tractable while activating all factors.
    eval_matrix = matrix if mode == "smoke" else matrix[::3]
    for run_index in range(eval_runs):
        for scenario_index, scenario in enumerate(eval_matrix):
            workload_seed = 30000 + 1000 * run_index + scenario_index
            for policy_name, policy in trained.items():
                summary = run_once(
                    policy, scenario, workload_seed, duration, max_workflows, max_time
                )
                summary["run"] = run_index
                summary["scenario"] = scenario_index
                summaries.append(summary)
                by_run.append(compact_summary(summary))
                for record in summary["workflow_records"]:
                    workflow_rows.append(
                        {
                            **record,
                            "policy": policy_name,
                            "run": run_index,
                            "scenario": scenario_index,
                            "seed": workload_seed,
                            "load": scenario[0],
                            "deadline_scale": scenario[1],
                            "optional_scale": scenario[2],
                            "capacity_scale": scenario[3],
                        }
                    )
    write_csv(out / "ablation_by_run.csv", by_run)
    write_csv(out / "workflow_audit.csv", workflow_rows)
    full_summaries = [summary for summary in summaries if summary["policy"] == "full"]
    write_csv(
        out / "state_coverage.csv",
        training_coverage_rows(trained["full"]) + coverage_rows("test", full_summaries),
    )
    write_csv(out / "ablation_by_slice.csv", ablation_slice_rows(workflow_rows))
    save_bandit(out / "full_bandit.json", trained["full"])
    return trained["full"], summaries


def candidate_rules(count: int, seed: int = 91) -> List[Dict[str, float]]:
    rng = random.Random(seed)
    candidates = []
    anchors = [
        # Explicit high-quality endpoints make the constrained search feasible
        # rather than silently falling back to an infeasible rule.
        (0.1, 0.1, 0.1, 0.0, 0.0, (0.0, 10.0, 11.0, 12.0)),  # always recovery
        (1.0, 1.0, 1.0, 0.0, 0.0, (10.0, 11.0, 12.0, 13.0)),  # always full
        (1.0, 1.0, 1.0, 0.0, 0.0, (0.25, 0.75, 1.35, 2.10)),
        (1.3, 0.8, 1.2, 0.5, 0.7, (0.35, 1.00, 1.85, 2.80)),
        (0.8, 1.4, 0.9, 0.8, 0.4, (0.30, 0.90, 1.60, 2.50)),
    ]
    for wc, ws, wp, wcs, wcp, thresholds in anchors:
        candidates.append(
            {"wc": wc, "ws": ws, "wp": wp, "wcs": wcs, "wcp": wcp, **{f"t{i}": v for i, v in enumerate(thresholds)}}
        )
    while len(candidates) < count:
        weights = [rng.uniform(0.35, 1.65) for _ in range(3)] + [rng.uniform(0.0, 1.0) for _ in range(2)]
        start = rng.uniform(0.10, 0.55)
        gaps = [rng.uniform(0.30, 0.85) for _ in range(3)]
        thresholds = [start]
        for gap in gaps:
            thresholds.append(thresholds[-1] + gap)
        candidates.append(
            {
                "wc": weights[0], "ws": weights[1], "wp": weights[2],
                "wcs": weights[3], "wcp": weights[4],
                **{f"t{i}": value for i, value in enumerate(thresholds)},
            }
        )
    return candidates


def validation_objective(
    records: Sequence[Mapping[str, object]],
    q_min: float = 0.95,
    p99_scale: float = 1.0,
    waste_scale: float = 1.0,
) -> Tuple[float, Dict[str, float]]:
    metrics = state_metrics(records)
    quality_violation = max(0.0, q_min - metrics["quality"])
    objective = (
        metrics["p99_latency"] / max(p99_scale, 1e-9)
        + 3.0 * metrics["deadline_miss_ratio"]
        + metrics["waste"] / max(waste_scale, 1e-9)
        + 100.0 * quality_violation
    )
    return objective, metrics


def test_domain_rows(summary: Mapping[str, object], label: str) -> List[Dict[str, object]]:
    """Return the aggregate row plus per-template held-out rows."""
    common = {
        "policy": label,
        "load": summary["load"],
        "seed": summary["seed"],
        "deadline_scale": summary["deadline_scale"],
        "optional_scale": summary["optional_scale"],
        "capacity_scale": summary["capacity_scale"],
    }
    rows = [{**compact_summary(summary), **common, "template": "all"}]
    for template in up.TEMPLATES:
        records = [row for row in summary["workflow_records"] if row["template"] == template]
        if not records:
            continue
        metrics = state_metrics(records)
        rows.append(
            {
                **common,
                "template": template,
                "completed": int(metrics["n"]),
                "p99_latency": metrics["p99_latency"],
                "deadline_miss_ratio": metrics["deadline_miss_ratio"],
                "wasted_speculative_bytes_per_workflow": metrics["waste"],
                "avg_quality": metrics["quality"],
                "normalized_latency": metrics["normalized_latency"],
            }
        )
    return rows


def rule_bandit_pairwise_rows(
    test_rows: Sequence[Mapping[str, object]],
    quality_floor: float,
    p99_scale: float,
    waste_scale: float,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Build paired, quality-aware rule-vs-bandit evidence.

    Strict four-objective dominance is retained as a descriptive diagnostic,
    while constrained outcomes distinguish an infeasible low-quality point
    from a genuinely competitive operating point.
    """
    indexed: Dict[Tuple[str, int, int], Mapping[str, object]] = {}
    for row in test_rows:
        if row.get("template") == "all" and row["policy"] in ("bandit", "global_tuned_rule"):
            indexed[(str(row["policy"]), int(row["run"]), int(row["scenario"]))] = row

    units: List[Dict[str, object]] = []
    keys = sorted({(run, scenario) for _, run, scenario in indexed})
    metric_fields = {
        "p99_latency": "p99_latency",
        "deadline_miss_ratio": "deadline_miss_ratio",
        "waste": "wasted_speculative_bytes_per_workflow",
        "quality": "avg_quality",
    }

    def dominates(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
        return (
            float(left["p99_latency"]) <= float(right["p99_latency"])
            and float(left["deadline_miss_ratio"]) <= float(right["deadline_miss_ratio"])
            and float(left["wasted_speculative_bytes_per_workflow"])
            <= float(right["wasted_speculative_bytes_per_workflow"])
            and float(left["avg_quality"]) >= float(right["avg_quality"])
        )

    for run, scenario in keys:
        bandit = indexed.get(("bandit", run, scenario))
        rule = indexed.get(("global_tuned_rule", run, scenario))
        if bandit is None or rule is None:
            continue
        bandit_feasible = float(bandit["avg_quality"]) >= quality_floor
        rule_feasible = float(rule["avg_quality"]) >= quality_floor
        raw_winner = "rule" if dominates(rule, bandit) else "bandit" if dominates(bandit, rule) else "neither"
        if rule_feasible and not bandit_feasible:
            constrained_winner = "rule"
        elif bandit_feasible and not rule_feasible:
            constrained_winner = "bandit"
        elif not rule_feasible and not bandit_feasible:
            constrained_winner = "neither_infeasible"
        else:
            constrained_winner = raw_winner

        def cost(row: Mapping[str, object]) -> float:
            return (
                float(row["p99_latency"]) / max(p99_scale, 1e-9)
                + 3.0 * float(row["deadline_miss_ratio"])
                + float(row["wasted_speculative_bytes_per_workflow"]) / max(waste_scale, 1e-9)
                + 100.0 * max(0.0, quality_floor - float(row["avg_quality"]))
            )

        unit: Dict[str, object] = {
            "run": run,
            "scenario": scenario,
            "load": rule["load"],
            "deadline_scale": rule["deadline_scale"],
            "optional_scale": rule["optional_scale"],
            "capacity_scale": rule["capacity_scale"],
            "quality_floor": quality_floor,
            "bandit_feasible": int(bandit_feasible),
            "rule_feasible": int(rule_feasible),
            "raw_dominance_winner": raw_winner,
            "constrained_winner": constrained_winner,
            "delta_cost_rule_minus_bandit": cost(rule) - cost(bandit),
        }
        for label, field in metric_fields.items():
            unit[f"delta_{label}_rule_minus_bandit"] = float(rule[field]) - float(bandit[field])
        units.append(unit)

    summaries: List[Dict[str, object]] = []
    summary_fields = list(metric_fields) + ["cost"]
    for index, metric in enumerate(summary_fields):
        values = [
            (int(row["scenario"]), float(row[f"delta_{metric}_rule_minus_bandit"]))
            for row in units
        ]
        low, high = stratified_bootstrap_ci(values, seed=91267 + index)
        summaries.append(
            {
                "comparison": "global_tuned_rule_minus_bandit",
                "metric": metric,
                "paired_units": len(values),
                "scenario_strata": len({scenario for scenario, _ in values}),
                "mean_delta": stratified_mean(values),
                "ci95_low": low,
                "ci95_high": high,
                "quality_floor": quality_floor,
            }
        )
    return units, summaries


def policy_bandit_pairwise_rows(
    test_rows: Sequence[Mapping[str, object]],
    quality_floor: float,
    p99_scale: float,
    waste_scale: float,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Apply the same paired comparison to every deployment-policy baseline."""
    competitors = sorted(
        {
            str(row["policy"])
            for row in test_rows
            if row.get("template") == "all" and row["policy"] != "bandit"
        }
    )
    all_units: List[Dict[str, object]] = []
    all_summaries: List[Dict[str, object]] = []
    for competitor in competitors:
        renamed = []
        for row in test_rows:
            if row.get("template") != "all" or row["policy"] not in ("bandit", competitor):
                continue
            renamed.append(
                {
                    **row,
                    "policy": "global_tuned_rule" if row["policy"] == competitor else "bandit",
                }
            )
        units, summaries = rule_bandit_pairwise_rows(
            renamed, quality_floor, p99_scale, waste_scale
        )
        for row in units:
            converted = {
                "comparison_policy": competitor,
                **row,
                "competitor_feasible": row["rule_feasible"],
                "raw_dominance_winner": competitor
                if row["raw_dominance_winner"] == "rule"
                else row["raw_dominance_winner"],
                "constrained_winner": competitor
                if row["constrained_winner"] == "rule"
                else row["constrained_winner"],
            }
            for metric in ("p99_latency", "deadline_miss_ratio", "waste", "quality", "cost"):
                converted[f"delta_{metric}_competitor_minus_bandit"] = row[
                    f"delta_{metric}_rule_minus_bandit"
                ]
            all_units.append(converted)
        for row in summaries:
            all_summaries.append(
                {
                    **row,
                    "comparison": f"{competitor}_minus_bandit",
                    "comparison_policy": competitor,
                }
            )
    return all_units, all_summaries


def deployment_summary_rows(
    test_rows: Sequence[Mapping[str, object]],
    regret_rows: Sequence[Mapping[str, object]],
    quality_floor: float,
) -> List[Dict[str, object]]:
    """Summarize deployable baselines without hiding quality or tail regret."""
    regret_by_policy: Dict[str, List[float]] = defaultdict(list)
    for row in regret_rows:
        regret_by_policy[str(row["policy"])].append(float(row["regret"]))
    aggregate_by_policy: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in test_rows:
        if row.get("template") == "all":
            aggregate_by_policy[str(row["policy"])].append(row)
    rows: List[Dict[str, object]] = []
    for policy, aggregate in sorted(aggregate_by_policy.items()):
        regrets = regret_by_policy.get(policy, [])
        rows.append(
            {
                "policy": policy,
                "aggregate_test_units": len(aggregate),
                "domain_regret_units": len(regrets),
                "mean_p99_latency": statistics.mean(float(row["p99_latency"]) for row in aggregate),
                "mean_deadline_miss_ratio": statistics.mean(
                    float(row["deadline_miss_ratio"]) for row in aggregate
                ),
                "mean_waste": statistics.mean(
                    float(row["wasted_speculative_bytes_per_workflow"]) for row in aggregate
                ),
                "mean_quality": statistics.mean(float(row["avg_quality"]) for row in aggregate),
                "quality_feasible_fraction": statistics.mean(
                    float(row["avg_quality"]) >= quality_floor for row in aggregate
                ),
                "mean_regret": statistics.mean(regrets) if regrets else math.nan,
                "median_regret": statistics.median(regrets) if regrets else math.nan,
                "p90_regret": up.percentile(regrets, 0.90) if regrets else math.nan,
                "max_regret": max(regrets) if regrets else math.nan,
                "best_domain_fraction": statistics.mean(value <= 1e-12 for value in regrets)
                if regrets
                else math.nan,
            }
        )
    return rows


def rq2(
    out: Path,
    mode: str,
    full_bandit: AuditedBandit,
    matrix: Sequence[Tuple[str, float, float, float]],
    duration: int,
    max_workflows: int,
    max_time: int,
) -> None:
    # Nine parameters make the former 48-point search a weak rule baseline.
    # Keep smoke inexpensive but double the full validation budget.
    candidates = candidate_rules(16 if mode == "smoke" else 96)
    validation_matrix = matrix[:: max(1, len(matrix) // (6 if mode == "smoke" else 18))]
    search_rows: List[Dict[str, object]] = []
    candidate_records: Dict[int, List[Dict[str, object]]] = defaultdict(list)
    for candidate_id, params in enumerate(candidates):
        for scenario_index, scenario in enumerate(validation_matrix):
            seed = 50000 + scenario_index
            policy = TunedRiskRulePolicy(params, seed=seed)
            summary = run_once(policy, scenario, seed, duration, max_workflows, max_time)
            for record in summary["workflow_records"]:
                candidate_records[candidate_id].append({**record, "load": scenario[0]})
    raw_metrics = {candidate_id: state_metrics(records) for candidate_id, records in candidate_records.items()}
    p99_scale = statistics.median(metrics["p99_latency"] for metrics in raw_metrics.values())
    waste_scale = statistics.median(metrics["waste"] for metrics in raw_metrics.values())
    bandit_validation_records: List[Dict[str, object]] = []
    for scenario_index, scenario in enumerate(validation_matrix):
        seed = 50000 + scenario_index
        summary = run_once(full_bandit, scenario, seed, duration, max_workflows, max_time)
        bandit_validation_records.extend(summary["workflow_records"])
    _, bandit_validation_metrics = validation_objective(
        bandit_validation_records, 0.0, p99_scale, waste_scale
    )
    matched_q_min = max(0.0, bandit_validation_metrics["quality"] - 0.01)
    for candidate_id, params in enumerate(candidates):
        objective_matched, metrics = validation_objective(
            candidate_records[candidate_id], matched_q_min, p99_scale, waste_scale
        )
        objective95, metrics = validation_objective(
            candidate_records[candidate_id], 0.95, p99_scale, waste_scale
        )
        objective97, _ = validation_objective(
            candidate_records[candidate_id], 0.97, p99_scale, waste_scale
        )
        search_rows.append(
            {
                "candidate_id": candidate_id,
                **params,
                "objective_bandit_minus_001": objective_matched,
                "objective_q95": objective95,
                "objective_q97": objective97,
                "feasible_q95": int(metrics["quality"] >= 0.95),
                "feasible_q97": int(metrics["quality"] >= 0.97),
                "matched_q_min": matched_q_min,
                "feasible_bandit_minus_001": int(metrics["quality"] >= matched_q_min),
                "validation_p99_scale": p99_scale,
                "validation_waste_scale": waste_scale,
                "simulator_interactions": len(candidate_records[candidate_id]),
                **metrics,
            }
        )
    write_csv(out / "tuned_rule_search.csv", search_rows)

    pareto_rows = []
    for row in search_rows:
        dominated = False
        for other in search_rows:
            weakly_better = (
                float(other["p99_latency"]) <= float(row["p99_latency"])
                and float(other["deadline_miss_ratio"]) <= float(row["deadline_miss_ratio"])
                and float(other["waste"]) <= float(row["waste"])
                and float(other["quality"]) >= float(row["quality"])
            )
            strictly_better = any(
                (
                    float(other[key]) < float(row[key])
                    if key != "quality"
                    else float(other[key]) > float(row[key])
                )
                for key in ("p99_latency", "deadline_miss_ratio", "waste", "quality")
            )
            if weakly_better and strictly_better:
                dominated = True
                break
        if not dominated:
            pareto_rows.append(row)
    write_csv(out / "tuned_rule_pareto.csv", pareto_rows)

    feasible = [row for row in search_rows if float(row["quality"]) >= matched_q_min]
    pool = feasible or search_rows
    best = min(pool, key=lambda row: float(row["objective_bandit_minus_001"]))
    best_params = {key: float(best[key]) for key in ("wc", "ws", "wp", "wcs", "wcp", "t0", "t1", "t2", "t3")}
    feasible95 = [row for row in search_rows if float(row["quality"]) >= 0.95]
    best95 = min(feasible95 or search_rows, key=lambda row: float(row["objective_q95"]))
    best95_params = {key: float(best95[key]) for key in ("wc", "ws", "wp", "wcs", "wcp", "t0", "t1", "t2", "t3")}
    feasible97 = [row for row in search_rows if float(row["quality"]) >= 0.97]
    best97 = min(feasible97 or search_rows, key=lambda row: float(row["objective_q97"]))

    def select_for(field: str, values: Sequence[str]) -> Dict[str, Dict[str, object]]:
        selected: Dict[str, Dict[str, object]] = {}
        for value in values:
            scored = []
            for candidate_id, records in candidate_records.items():
                subset = [record for record in records if str(record[field]) == value]
                if not subset:
                    continue
                objective, metrics = validation_objective(subset, 0.95, p99_scale, waste_scale)
                scored.append((candidate_id, objective, metrics))
            feasible_scored = [item for item in scored if item[2]["quality"] >= 0.95]
            candidate_id, objective, metrics = min(feasible_scored or scored, key=lambda item: item[1])
            selected[value] = {
                "candidate_id": candidate_id,
                "objective": objective,
                "metrics": metrics,
                "params": candidates[candidate_id],
            }
        return selected

    per_load = select_for("load", list(up.LOAD_CONFIG))
    per_template = select_for("template", list(up.TEMPLATES))
    write_json(
        out / "selected_rule.json",
        {
            "search_budget": {
                "candidate_rules": len(candidates),
                "validation_scenarios": len(validation_matrix),
                "rule_workflows_per_candidate_min": min(len(rows) for rows in candidate_records.values()),
                "rule_workflows_per_candidate_max": max(len(rows) for rows in candidate_records.values()),
                "bandit_training_updates": sum(
                    sum(counts.values()) for counts in full_bandit.counts.values()
                ),
            },
            "q_min": {"fixed": 0.95, "bandit_minus_001": matched_q_min},
            "bandit_validation_metrics": bandit_validation_metrics,
            "validation_normalization": {"p99": p99_scale, "waste": waste_scale},
            "global": {"candidate_id": best["candidate_id"], "params": best_params},
            "global_q95": {"candidate_id": best95["candidate_id"], "params": best95_params},
            "global_q97": {
                "candidate_id": best97["candidate_id"],
                "params": {key: float(best97[key]) for key in ("wc", "ws", "wp", "wcs", "wcp", "t0", "t1", "t2", "t3")},
            },
            "per_load": per_load,
            "per_template": per_template,
        },
    )

    test_rows: List[Dict[str, object]] = []
    test_matrix = matrix[1:: max(1, len(matrix) // (4 if mode == "smoke" else 12))]
    test_runs = 2 if mode == "smoke" else 20
    for run_index in range(test_runs):
        for scenario_index, scenario in enumerate(test_matrix):
            seed = 60000 + 1000 * run_index + scenario_index
            class PerTemplateRule(TunedRiskRulePolicy):
                def decide_action(self, sim, workflow):
                    self.params = dict(per_template[workflow.spec.template]["params"])
                    return super().decide_action(sim, workflow)

            policies = (
                ("bandit", full_bandit),
                ("fixed_moderate", FixedActionPolicy("moderate", seed=seed)),
                ("fixed_conservative", FixedActionPolicy("conservative", seed=seed)),
                ("handwritten_rule", up.RuleBasedFeedbackPolicy(seed=seed)),
                ("global_tuned_rule", TunedRiskRulePolicy(best_params, seed=seed)),
                ("global_tuned_rule_q95", TunedRiskRulePolicy(best95_params, seed=seed, name="global_tuned_rule_q95")),
                ("per_load_tuned_rule", TunedRiskRulePolicy(per_load[scenario[0]]["params"], seed=seed, name="per_load_tuned_rule")),
                ("per_template_tuned_rule", PerTemplateRule(best_params, seed=seed, name="per_template_tuned_rule")),
            )
            for label, policy in policies:
                summary = run_once(policy, scenario, seed, duration, max_workflows, max_time)
                for row in test_domain_rows(summary, label):
                    row.update({"run": run_index, "scenario": scenario_index})
                    test_rows.append(row)
    write_csv(out / "rule_bandit_test.csv", test_rows)

    pairwise_units, pairwise_summary = rule_bandit_pairwise_rows(
        test_rows, matched_q_min, p99_scale, waste_scale
    )
    write_csv(out / "rule_bandit_pairwise.csv", pairwise_units)
    write_csv(out / "rule_bandit_pairwise_summary.csv", pairwise_summary)
    policy_pairwise_units, policy_pairwise_summary = policy_bandit_pairwise_rows(
        test_rows, matched_q_min, p99_scale, waste_scale
    )
    write_csv(out / "policy_bandit_pairwise.csv", policy_pairwise_units)
    write_csv(out / "policy_bandit_pairwise_summary.csv", policy_pairwise_summary)

    regret_rows = []
    groups: Dict[Tuple[int, int, str], List[Dict[str, object]]] = defaultdict(list)
    for row in test_rows:
        groups[(int(row["run"]), int(row["scenario"]), str(row["template"]))].append(row)
    for (run_index, scenario_index, template), rows in groups.items():
        costs = {
            str(row["policy"]): float(row["p99_latency"]) / max(p99_scale, 1e-9)
            + 3.0 * float(row["deadline_miss_ratio"])
            + float(row["wasted_speculative_bytes_per_workflow"]) / max(waste_scale, 1e-9)
            + 100.0 * max(0.0, matched_q_min - float(row["avg_quality"]))
            for row in rows
        }
        best_cost = min(costs.values())
        for policy, cost in costs.items():
            regret_rows.append(
                {
                    "run": run_index,
                    "scenario": scenario_index,
                    "load": rows[0]["load"],
                    "template": template,
                    "deadline_scale": rows[0]["deadline_scale"],
                    "optional_scale": rows[0]["optional_scale"],
                    "capacity_scale": rows[0]["capacity_scale"],
                    "policy": policy,
                    "quality_floor": matched_q_min,
                    "cost": cost,
                    "regret": cost - best_cost,
                }
            )
    write_csv(out / "per_domain_regret.csv", regret_rows)
    write_csv(
        out / "deployment_policy_summary.csv",
        deployment_summary_rows(test_rows, regret_rows, matched_q_min),
    )


def best_action(policy: AuditedBandit, state: Tuple[str, str, str]) -> Tuple[str, str, float]:
    values = policy.q_values.get(state, {action: 0.0 for action in up.ACTIONS})
    ranked = sorted(up.ACTIONS, key=lambda action: (values[action], -up.ACTIONS.index(action)), reverse=True)
    return ranked[0], ranked[1], values[ranked[0]] - values[ranked[1]]


def q_audit_rows(
    policy: AuditedBandit,
    test_counts: Optional[Mapping[Tuple[str, str, str], int]] = None,
) -> List[Dict[str, object]]:
    rows = []
    for state in ALL_STATES:
        values = policy.q_values.get(state, {action: 0.0 for action in up.ACTIONS})
        counts = policy.counts.get(state, Counter())
        best, second, margin = best_action(policy, state)
        visits = sum(counts.values())
        row: Dict[str, object] = {
            "congestion": state[0], "slack": state[1], "spec_pressure": state[2],
            "visit_count": visits,
            "test_count": (test_counts or {}).get(state, 0),
            "support_flag": "supported" if visits >= 30 else "low-support" if visits else "unseen",
            "best_action": best if visits else "unseen",
            "second_action": second if visits else "unseen",
            "q_margin": margin if visits else "",
            "raw_bandit_action": best if visits else "unseen",
            "guarded_action": best if visits else "unseen",
            "guard_reason": "disabled",
        }
        for action in up.ACTIONS:
            row[f"q_{action}"] = values[action]
            row[f"updates_{action}"] = counts[action]
        rows.append(row)
    return rows


def counterfactual_rows(policy: AuditedBandit) -> List[Dict[str, object]]:
    rows = []
    dimensions = ((0, "congestion", CONGESTION), (1, "slack", SLACK), (2, "spec_pressure", SPEC))
    for state in ALL_STATES:
        visits = sum(policy.counts.get(state, Counter()).values())
        if visits < 30:
            continue
        original, _, original_margin = best_action(policy, state)
        original_value = policy.q_values[state][original]
        for index, name, values in dimensions:
            for replacement in values:
                if replacement == state[index]:
                    continue
                counter = list(state)
                counter[index] = replacement
                counter_state = tuple(counter)
                counter_visits = sum(policy.counts.get(counter_state, Counter()).values())
                counter_action, _, counter_margin = best_action(policy, counter_state)
                counter_value = policy.q_values[counter_state][counter_action] if counter_visits else math.nan
                rows.append(
                    {
                        "original_state": str(state), "counterfactual_state": str(counter_state),
                        "changed_variable": name, "original_visits": visits,
                        "counterfactual_visits": counter_visits,
                        "original_action": original,
                        "counterfactual_action": counter_action if counter_visits else "unseen",
                        "action_flip": int(bool(counter_visits) and original != counter_action),
                        "strength_delta": ACTION_STRENGTH[counter_action] - ACTION_STRENGTH[original] if counter_visits else "",
                        "original_q_margin": original_margin,
                        "counterfactual_q_margin": counter_margin if counter_visits else "",
                        "delta_q_best": counter_value - original_value if counter_visits else "",
                    }
                )
    return rows


def sanity_check_rows(policy: AuditedBandit) -> List[Dict[str, object]]:
    checks = [
        ("congestion_nonincrease", ("low", "loose", "high_spec"), ("high", "loose", "high_spec"), "nonincreasing"),
        ("tight_slack_nonincrease", ("high", "loose", "high_spec"), ("high", "tight", "high_spec"), "nonincreasing"),
        ("high_spec_nonincrease", ("high", "loose", "low_spec"), ("high", "loose", "high_spec"), "nonincreasing"),
        ("low_congestion_not_hard_cut", ("low", "loose", "low_spec"), ("low", "loose", "high_spec"), "at_least_moderate"),
    ]
    rows = []
    for name, before, after, expectation in checks:
        before_visits = sum(policy.counts.get(before, Counter()).values())
        after_visits = sum(policy.counts.get(after, Counter()).values())
        before_action, _, _ = best_action(policy, before)
        after_action, _, _ = best_action(policy, after)
        supported = before_visits >= 30 and after_visits >= 30
        if expectation == "nonincreasing":
            violation = ACTION_STRENGTH[after_action] > ACTION_STRENGTH[before_action]
        else:
            violation = ACTION_STRENGTH[after_action] < ACTION_STRENGTH["moderate"]
        rows.append(
            {
                "check": name,
                "before_state": str(before),
                "after_state": str(after),
                "expectation": expectation,
                "before_visits": before_visits,
                "after_visits": after_visits,
                "before_action": before_action if before_visits else "unseen",
                "after_action": after_action if after_visits else "unseen",
                "supported": int(supported),
                "violation": int(violation) if supported else "",
            }
        )
    return rows


def write_policy_heatmap(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    colors = {
        "full": "#2f9e44", "recovery": "#74b816", "moderate": "#f59f00",
        "conservative": "#f76707", "critical_only": "#c92a2a", "unseen": "#ced4da",
    }
    width, height = 1050, 660
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:DejaVu Sans,sans-serif;fill:#212529}.title{font-size:21px;font-weight:bold}.label{font-size:13px}.cell{font-size:11px;font-weight:bold;fill:white}</style>',
        '<text x="30" y="34" class="title">Frozen bandit state-action heatmap</text>',
    ]
    by_state = {(row["congestion"], row["slack"], row["spec_pressure"]): row for row in rows}
    for panel, spec in enumerate(SPEC):
        x0 = 35 + panel * 340
        parts.append(f'<text x="{x0}" y="75" class="label">spec pressure: {spec}</text>')
        for col, slack in enumerate(SLACK):
            parts.append(f'<text x="{x0 + 83 + col * 82}" y="101" text-anchor="middle" class="label">{slack}</text>')
        for row_index, congestion in enumerate(CONGESTION):
            y = 115 + row_index * 105
            parts.append(f'<text x="{x0}" y="{y + 45}" class="label">{congestion}</text>')
            for col, slack in enumerate(SLACK):
                data = by_state[(congestion, slack, spec)]
                action = str(data["best_action"])
                x = x0 + 47 + col * 82
                parts.append(f'<rect x="{x}" y="{y}" width="76" height="90" rx="5" fill="{colors[action]}"/>')
                parts.append(f'<text x="{x + 38}" y="{y + 28}" text-anchor="middle" class="cell">{action}</text>')
                margin = data["q_margin"] if data["q_margin"] != "" else "NA"
                parts.append(f'<text x="{x + 38}" y="{y + 49}" text-anchor="middle" class="cell">margin {margin if margin == "NA" else f"{float(margin):.2f}"}</text>')
                parts.append(f'<text x="{x + 38}" y="{y + 69}" text-anchor="middle" class="cell">N={data["visit_count"]}</text>')
    parts.append('<text x="35" y="630" class="label">Gray = unseen; low-support cells (N&lt;30) are descriptive only. Guard disabled for all policies.</text>')
    parts.append('</svg>')
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def rq3(
    out: Path,
    mode: str,
    reference: AuditedBandit,
    matrix: Sequence[Tuple[str, float, float, float]],
    duration: int,
    max_workflows: int,
    max_time: int,
    train_episodes: int,
    test_counts: Optional[Mapping[Tuple[str, str, str], int]] = None,
) -> None:
    audit = q_audit_rows(reference, test_counts)
    write_csv(out / "q_table_audit.csv", audit)
    write_csv(out / "counterfactual_audit.csv", counterfactual_rows(reference))
    write_csv(out / "sanity_checks.csv", sanity_check_rows(reference))
    write_policy_heatmap(out / "state_action_heatmap.svg", audit)
    seed_count = 3 if mode == "smoke" else 10
    policies = [reference]
    for seed in range(1, seed_count):
        policies.append(
            train_bandit(AuditedBandit, 7 + 101 * seed, train_episodes, duration, max_workflows, max_time, matrix)
        )
    stability = []
    for state in ALL_STATES:
        actions = []
        margins = []
        visits = []
        for policy in policies:
            count = sum(policy.counts.get(state, Counter()).values())
            action, _, margin = best_action(policy, state)
            visits.append(count)
            if count:
                actions.append(action)
                margins.append(margin)
        if actions:
            modal, modal_count = Counter(actions).most_common(1)[0]
            low, high = bootstrap_ci(margins, seed=7331 + len(stability), draws=2000)
            stability.append(
                {
                    "state": str(state), "seeds": len(policies), "supported_seeds": len(actions),
                    "min_visits": min(visits), "median_visits": statistics.median(visits),
                    "modal_action": modal, "best_action_agreement": modal_count / len(actions),
                    "mean_q_margin": statistics.mean(margins), "q_margin_ci95_low": low,
                    "q_margin_ci95_high": high,
                    "uncertain": int(modal_count / len(actions) < 0.8),
                }
            )
    write_csv(out / "policy_stability.csv", stability)

    metric_rows = []
    performance_rows: List[Dict[str, object]] = []
    target_scenarios = 4 if mode == "smoke" else 12
    scenario_step = max(1, len(matrix) // target_scenarios)
    evaluation_scenarios = list(matrix[::scenario_step][:target_scenarios])
    for policy_index, policy in enumerate(policies):
        summaries = [
            run_once(policy, scenario, 70000 + scenario_index, duration, max_workflows, max_time)
            for scenario_index, scenario in enumerate(evaluation_scenarios)
        ]
        for scenario_index, summary in enumerate(summaries):
            performance_rows.append(
                {
                    "training_seed_index": policy_index,
                    "scenario": scenario_index,
                    "load": summary["load"],
                    "deadline_scale": summary["deadline_scale"],
                    "optional_scale": summary["optional_scale"],
                    "capacity_scale": summary["capacity_scale"],
                    "p99_latency": summary["p99_latency"],
                    "deadline_miss_ratio": summary["deadline_miss_ratio"],
                    "waste": summary["wasted_speculative_bytes_per_workflow"],
                    "quality": summary["avg_quality"],
                }
            )
        metric_rows.append(
            {
                "training_seed_index": policy_index,
                "mean_p99_latency": statistics.mean(float(row["p99_latency"]) for row in summaries),
                "mean_deadline_miss_ratio": statistics.mean(float(row["deadline_miss_ratio"]) for row in summaries),
                "mean_waste": statistics.mean(float(row["wasted_speculative_bytes_per_workflow"]) for row in summaries),
                "mean_quality": statistics.mean(float(row["avg_quality"]) for row in summaries),
                "evaluation_scenarios": len(summaries),
            }
        )
    write_csv(out / "policy_stability_metrics.csv", metric_rows)
    write_csv(out / "policy_stability_performance.csv", performance_rows)

    # Action disagreement can be harmless when several actions are near-ties.
    # Test whether independently trained policies remain practically equivalent
    # on held-out performance, using explicit, symmetric equivalence margins.
    reference_by_scenario = {
        int(row["scenario"]): row
        for row in performance_rows
        if int(row["training_seed_index"]) == 0
    }
    definitions = {
        "p99_latency_relative": ("p99_latency", "relative", -0.10, 0.10),
        "deadline_miss_absolute": ("deadline_miss_ratio", "absolute", -0.02, 0.02),
        "waste_relative": ("waste", "relative", -0.10, 0.10),
        "quality_absolute": ("quality", "absolute", -0.01, 0.01),
    }
    per_seed_deltas: Dict[str, List[float]] = defaultdict(list)
    for policy_index in range(1, len(policies)):
        rows = [row for row in performance_rows if int(row["training_seed_index"]) == policy_index]
        for label, (field, scale, _, _) in definitions.items():
            deltas = []
            for row in rows:
                reference_row = reference_by_scenario[int(row["scenario"])]
                current = float(row[field])
                reference_value = float(reference_row[field])
                if scale == "relative":
                    deltas.append((current - reference_value) / max(abs(reference_value), 1e-9))
                else:
                    deltas.append(current - reference_value)
            per_seed_deltas[label].append(statistics.mean(deltas))

    equivalence_rows = []
    for index, (label, (_, scale, lower_margin, upper_margin)) in enumerate(definitions.items()):
        values = per_seed_deltas[label]
        low, high = bootstrap_ci(values, seed=81267 + index, draws=4000)
        equivalence_rows.append(
            {
                "metric": label,
                "scale": scale,
                "training_seed_comparisons": len(values),
                "evaluation_scenarios": len(evaluation_scenarios),
                "mean_delta_vs_reference_seed": statistics.mean(values),
                "ci95_low": low,
                "ci95_high": high,
                "equivalence_lower_margin": lower_margin,
                "equivalence_upper_margin": upper_margin,
                "equivalence_supported": int(low >= lower_margin and high <= upper_margin),
                "seed_fraction_within_margin": statistics.mean(
                    lower_margin <= value <= upper_margin for value in values
                ),
            }
        )
    write_csv(out / "policy_stability_equivalence.csv", equivalence_rows)


def build_report(out: Path, mode: str) -> None:
    q_rows = list(csv.DictReader((out / "q_table_audit.csv").open(encoding="utf-8")))
    supported = sum(row["support_flag"] == "supported" for row in q_rows)
    seen = sum(row["support_flag"] != "unseen" for row in q_rows)
    slice_rows = list(csv.DictReader((out / "ablation_by_slice.csv").open(encoding="utf-8")))
    test_rows = list(csv.DictReader((out / "rule_bandit_test.csv").open(encoding="utf-8")))
    selected_rule = json.loads((out / "selected_rule.json").read_text(encoding="utf-8"))
    stability_rows = list(csv.DictReader((out / "policy_stability.csv").open(encoding="utf-8")))
    equivalence_path = out / "policy_stability_equivalence.csv"
    equivalence_rows = list(csv.DictReader(equivalence_path.open(encoding="utf-8"))) if equivalence_path.exists() else []
    pairwise_path = out / "rule_bandit_pairwise_summary.csv"
    pairwise_rows = list(csv.DictReader(pairwise_path.open(encoding="utf-8"))) if pairwise_path.exists() else []
    pairwise_units_path = out / "rule_bandit_pairwise.csv"
    pairwise_units = list(csv.DictReader(pairwise_units_path.open(encoding="utf-8"))) if pairwise_units_path.exists() else []
    deployment_summary_path = out / "deployment_policy_summary.csv"
    deployment_rows = (
        list(csv.DictReader(deployment_summary_path.open(encoding="utf-8")))
        if deployment_summary_path.exists()
        else []
    )
    sanity_rows = list(csv.DictReader((out / "sanity_checks.csv").open(encoding="utf-8")))
    by_policy: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in test_rows:
        if row.get("template", "all") == "all":
            by_policy[row["policy"]].append(row)
    aggregate_groups: Dict[Tuple[str, str], Dict[str, Dict[str, str]]] = defaultdict(dict)
    for row in test_rows:
        if row.get("template") == "all" and row["policy"] in ("bandit", "global_tuned_rule"):
            aggregate_groups[(row["run"], row["scenario"])][row["policy"]] = row
    domination = Counter()
    for pair in aggregate_groups.values():
        if len(pair) != 2:
            continue
        bandit, rule = pair["bandit"], pair["global_tuned_rule"]
        def dominates(left, right):
            return (
                float(left["p99_latency"]) <= float(right["p99_latency"])
                and float(left["deadline_miss_ratio"]) <= float(right["deadline_miss_ratio"])
                and float(left["wasted_speculative_bytes_per_workflow"]) <= float(right["wasted_speculative_bytes_per_workflow"])
                and float(left["avg_quality"]) >= float(right["avg_quality"])
            )
        if dominates(rule, bandit):
            domination["rule"] += 1
        elif dominates(bandit, rule):
            domination["bandit"] += 1
        else:
            domination["neither"] += 1
    constrained_outcomes = Counter(row["constrained_winner"] for row in pairwise_units)

    primary = {(row["hypothesis"], row["metric"]): row for row in slice_rows if row.get("primary_metric") == "1"}
    all_slice_metrics = {(row["hypothesis"], row["metric"]): row for row in slice_rows}

    def directional_support(hypothesis: str, metric: str) -> bool:
        row = primary[(hypothesis, metric)]
        return (
            float(row["mean_delta_ablation_minus_full"]) > 0.0
            and float(row["ci95_low"]) > 0.0
            and float(row.get("holm_adjusted_p") or 1.0) < 0.05
        )

    h1c_supported = directional_support("H1-C", "p99_latency")
    h1s_supported = directional_support("H1-S", "normalized_latency")
    h1p_supported = directional_support("H1-P", "waste")
    h1s_quality_tradeoff = (
        float(all_slice_metrics[("H1-S", "quality")]["ci95_low"]) > 0.0
    )
    verdict_rows = [
        {
            "claim": "RQ1-H1-C",
            "status": "supported" if h1c_supported else "not_supported",
            "evidence": "directional decision uses positive delta, positive stratified CI lower bound, and Holm-adjusted p<0.05",
        },
        {
            "claim": "RQ1-H1-S",
            "status": "supported_with_tradeoff" if h1s_supported and h1s_quality_tradeoff else "supported" if h1s_supported else "not_supported",
            "evidence": "directional decision uses positive delta, positive stratified CI lower bound, and Holm-adjusted p<0.05; quality tradeoff is reported separately",
        },
        {
            "claim": "RQ1-H1-P",
            "status": "supported" if h1p_supported else "not_supported",
            "evidence": "directional decision uses positive delta, positive stratified CI lower bound, and Holm-adjusted p<0.05",
        },
        {
            "claim": "RQ2-selected-global-rule-consistently-dominates",
            "status": "supported" if domination["rule"] and not domination["bandit"] and not domination["neither"] else "not_supported",
            "evidence": f"finite-search selected rule; four-objective counts: rule={domination['rule']}, bandit={domination['bandit']}, neither={domination['neither']}",
        },
        {
            "claim": "RQ3-auditable",
            "status": "supported" if seen >= 24 and supported >= 24 else "partially_supported" if seen >= 24 else "not_supported",
            "evidence": f"table export is complete; {seen}/27 states seen and {supported}/27 states have N>=30",
        },
        {
            "claim": "RQ3-seed_stable",
            "status": "not_supported",
            "evidence": f"mean action agreement={statistics.mean(float(row['best_action_agreement']) for row in stability_rows):.3f}; uncertain states={sum(row['uncertain'] == '1' for row in stability_rows)}/27",
        },
    ]
    if equivalence_rows:
        equivalent = sum(row["equivalence_supported"] == "1" for row in equivalence_rows)
        verdict_rows.append(
            {
                "claim": "RQ3-performance-equivalent-across-seeds",
                "status": "supported" if equivalent == len(equivalence_rows) else "partially_supported" if equivalent else "not_supported",
                "evidence": f"{equivalent}/{len(equivalence_rows)} held-out metrics satisfy the predeclared practical-equivalence margins",
            }
        )
    write_csv(out / "claim_verdicts.csv", verdict_rows)
    lines = [
        "# SpecNet-Agent 三项证明：隔离实验报告",
        "",
        f"- 运行模式：`{mode}`",
        f"- 证据协议版本：`{PROTOCOL_VERSION}`",
        f"- 主项目源文件（只读）：`{UPSTREAM_PATH}`",
        f"- 源文件 SHA-256：`{sha256(UPSTREAM_PATH)}`",
        "- Guard 协议：controller-core comparison，所有策略统一关闭 guard。",
        "",
        "## RQ1 状态变量必要性",
        "",
        f"完整 bandit 的 27 个理论状态中，训练后 seen={seen}，N>=30 的 supported={supported}。",
        f"Coverage gate 的 seen>=24 已满足；仍有 {27 - supported} 个状态低于主解释门槛 N=30。",
        "定向 slice 以 full policy 的状态锁定相同 workflow ID；正值表示消融比 full 更差（quality 除外）。",
        "",
    ]
    for hypothesis in ("H1-C", "H1-S", "H1-P"):
        selected = [
            row for row in slice_rows
            if row["hypothesis"] == hypothesis
            and row["metric"] in ("p99_latency", "deadline_miss_ratio", "waste", "quality", "normalized_latency")
        ]
        lines.append(f"### {hypothesis}")
        lines.append("")
        for row in selected:
            lines.append(
                f"- {row['metric']}: delta={float(row['mean_delta_ablation_minus_full']):.4f}, "
                f"95% CI [{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}], "
                f"n={row['paired_units']}, strata={row.get('scenario_strata', 'NA')}, "
                f"paired dz={float(row.get('standardized_effect_dz') or 'nan'):.3f}"
                + (
                    f", Holm-adjusted p={float(row['holm_adjusted_p']):.4g} (pre-registered primary)"
                    if row.get("holm_adjusted_p") else ""
                )
            )
        lines.append("")
    rq1_status_text = (
        f"H1-C={'支持' if h1c_supported else '不支持'}；"
        f"H1-S={'支持但存在质量权衡' if h1s_supported and h1s_quality_tradeoff else '支持' if h1s_supported else '不支持'}；"
        f"H1-P={'支持' if h1p_supported else '不支持'}。"
    )
    lines += [
        "结论按预注册方向、场景分层 CI 与 Holm 校正自动判定：" + rq1_status_text,
        "",
    ]
    lines += ["## RQ2 Tuned rule 与 bandit", ""]
    for policy, rows in sorted(by_policy.items()):
        lines.append(
            f"- {policy}: mean p99={statistics.mean(float(row['p99_latency']) for row in rows):.3f}, "
            f"miss={statistics.mean(float(row['deadline_miss_ratio']) for row in rows):.4f}, "
            f"waste={statistics.mean(float(row['wasted_speculative_bytes_per_workflow']) for row in rows):.3f}, "
            f"quality={statistics.mean(float(row['avg_quality']) for row in rows):.4f}"
        )
    lines += [
        "",
        f"Validation bandit quality={float(selected_rule['bandit_validation_metrics']['quality']):.4f}；"
        f"匹配门槛为 bandit-0.01={float(selected_rule['q_min']['bandit_minus_001']):.4f}，并另报固定 0.95 门槛。",
        f"四目标逐环境支配计数：rule={domination['rule']}，bandit={domination['bandit']}，neither={domination['neither']}。",
        f"质量约束后的逐环境结果：rule={constrained_outcomes['rule']}，bandit={constrained_outcomes['bandit']}，"
        f"neither={constrained_outcomes['neither']}，both-infeasible={constrained_outcomes['neither_infeasible']}。",
        f"规则搜索预算：{selected_rule.get('search_budget', {}).get('candidate_rules', 'NA')} 个候选、"
        f"{selected_rule.get('search_budget', {}).get('validation_scenarios', 'NA')} 个 validation 场景。",
        "配对、场景分层的 rule-minus-bandit 差异及 CI 见 `rule_bandit_pairwise_summary.csv`。",
        "所有固定与调优基线相对 bandit 的同协议比较见 `policy_bandit_pairwise_summary.csv`；"
        "跨域 regret 摘要见 `deployment_policy_summary.csv`。",
        "结论：这里检验的是有限搜索得到的候选策略，而不是所有可能规则；严格四目标支配与质量约束结果必须同时解释，"
        "不能把低质量策略的低延迟视为有效优势。",
        "",
        "## RQ3 可审计性",
        "",
        "完整 27-state Q 表、每动作更新次数、Q margin 与支持标记见 `q_table_audit.csv`；",
        "单变量反事实见 `counterfactual_audit.csv`；多训练 seed 一致性与指标变化见 `policy_stability*.csv`。",
        "预注册趋势检查见 `sanity_checks.csv`，三分面策略图见 `state_action_heatmap.svg`。",
        "unseen 状态不把全零 Q 的 tie-break 解释成有效动作。",
        f"{max(int(row['seeds']) for row in stability_rows)}-seed 平均 action agreement={statistics.mean(float(row['best_action_agreement']) for row in stability_rows):.3f}；"
        f"uncertain={sum(row['uncertain'] == '1' for row in stability_rows)}/{len(stability_rows)}；"
        f"supported sanity violations={sum(row['supported'] == '1' and row['violation'] == '1' for row in sanity_rows)}/"
        f"{sum(row['supported'] == '1' for row in sanity_rows)}。",
        "动作稳定性与性能稳定性分开判定；实际性能等价性见 `policy_stability_equivalence.csv`。",
        "结论：表格、支持度、反事实与 guard 路径均可审计；动作不一致不能单独推出性能不稳定，"
        "但仍不支持“策略表跨 seed 稳定/近似单调”的强解释性主张。",
        "",
        "## 结论边界",
        "",
        "Smoke 模式只证明实现与识别协议可运行，不用于论文显著性结论。Full 模式达到 20 个 paired evaluation seeds 与 10 个训练 seeds。",
        "逐项判定及其证据摘要见 `claim_verdicts.csv`；未支持的假设不得在论文中写成已证实。",
    ]
    if deployment_rows:
        best_regret = min(deployment_rows, key=lambda row: float(row["mean_regret"]))
        bandit_regret = next(row for row in deployment_rows if row["policy"] == "bandit")
        lines[lines.index("## RQ3 可审计性") : lines.index("## RQ3 可审计性")] = [
            f"按当前 validation scalar cost，最低平均跨域 regret 为 {best_regret['policy']}="
            f"{float(best_regret['mean_regret']):.4f}；bandit={float(bandit_regret['mean_regret']):.4f}。",
            "该诊断不等同于 Pareto 支配，但能防止只挑对 bandit 有利的指标讲故事。",
            "",
        ]
    (out / "PROOF_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--stage",
        choices=("all", "rq1", "rq1-analysis", "rq2", "rq3", "report"),
        default="all",
        help="Run one resumable experiment stage (default: all).",
    )
    return parser.parse_args()


def full_policy_test_counts(path: Path) -> Counter[Tuple[str, str, str]]:
    counts: Counter[Tuple[str, str, str]] = Counter()
    if not path.is_file():
        return counts
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] == "full":
                counts[(row["congestion_bucket"], row["slack_bucket"], row["spec_pressure_bucket"])] += 1
    return counts


def main() -> None:
    args = parse_args()
    mode = args.mode
    stage = args.stage
    out = Path(args.output_dir) if args.output_dir else ROOT / "results" / mode
    out.mkdir(parents=True, exist_ok=True)
    if mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        train_episodes, eval_runs = 36, 2
    else:
        duration, max_workflows, max_time = 1800, 90, 6000
        train_episodes, eval_runs = 162, 20
    matrix = scenarios(mode)
    manifest_path = out / "run_manifest.json"
    if stage in ("all", "rq1") or not manifest_path.exists():
        manifest = {
            "protocol_version": PROTOCOL_VERSION,
            "mode": mode,
            "upstream_path": str(UPSTREAM_PATH),
            "upstream_sha256_before": sha256(UPSTREAM_PATH),
            "harness_path": str(Path(__file__).resolve()),
            "harness_sha256_before": sha256(Path(__file__).resolve()),
            "matrix": matrix,
            "duration": duration,
            "max_workflows": max_workflows,
            "train_episodes": train_episodes,
            "eval_runs": eval_runs,
            "guard": "disabled_for_all_policies",
            "completed_stages": [],
        }
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    write_json(manifest_path, manifest)

    full_bandit: Optional[AuditedBandit] = None
    if stage in ("all", "rq1"):
        print("[RQ1] training independent ablations and running paired evaluation", flush=True)
        full_bandit, _ = rq1(
            out, mode, matrix, duration, max_workflows, max_time, train_episodes, eval_runs
        )
        manifest["completed_stages"] = sorted(set(manifest.get("completed_stages", [])) | {"rq1"})
        write_json(manifest_path, manifest)
    elif stage == "rq1-analysis":
        print("[RQ1] rebuilding matched full-reference slice statistics", flush=True)
        write_csv(out / "ablation_by_slice.csv", ablation_slice_rows_from_csv(out / "workflow_audit.csv"))
        manifest["completed_stages"] = sorted(set(manifest.get("completed_stages", [])) | {"rq1-analysis"})
        write_json(manifest_path, manifest)

    if stage in ("all", "rq2", "rq3") and full_bandit is None:
        checkpoint = out / "full_bandit.json"
        if checkpoint.is_file():
            full_bandit = load_bandit(checkpoint)
        else:
            print("[checkpoint] training the frozen full bandit once", flush=True)
            full_bandit = train_bandit(
                AuditedBandit, 7, train_episodes, duration, max_workflows, max_time, matrix
            )
            save_bandit(checkpoint, full_bandit)
    if stage in ("all", "rq2"):
        print("[RQ2] validation rule search and frozen held-out comparison", flush=True)
        rq2(out, mode, full_bandit, matrix, duration, max_workflows, max_time)
        manifest["completed_stages"] = sorted(set(manifest.get("completed_stages", [])) | {"rq2"})
        write_json(manifest_path, manifest)
    if stage in ("all", "rq3"):
        print("[RQ3] Q-table, counterfactual, sanity and seed-stability audits", flush=True)
        rq3(
            out, mode, full_bandit, matrix, duration, max_workflows, max_time, train_episodes,
            full_policy_test_counts(out / "workflow_audit.csv"),
        )
        manifest["completed_stages"] = sorted(set(manifest.get("completed_stages", [])) | {"rq3"})
        write_json(manifest_path, manifest)
    if stage in ("all", "report"):
        build_report(out, mode)
        manifest["completed_stages"] = sorted(set(manifest.get("completed_stages", [])) | {"report"})

    manifest["upstream_sha256_after"] = sha256(UPSTREAM_PATH)
    manifest["upstream_unchanged"] = manifest["upstream_sha256_before"] == manifest["upstream_sha256_after"]
    manifest["harness_sha256_after"] = sha256(Path(__file__).resolve())
    manifest["harness_unchanged"] = manifest.get("harness_sha256_before") == manifest["harness_sha256_after"]
    write_json(manifest_path, manifest)
    print(f"Completed stage {stage}; evidence directory: {out.resolve()}")
    print(f"Upstream unchanged: {manifest['upstream_unchanged']}")


if __name__ == "__main__":
    main()
