#!/usr/bin/env python3
"""
Trace-driven simulator for SpecNet-Agent.

The simulator compares fixed network QoS baselines with a network-aware
speculation-control policy for agentic GenAI workflows. It intentionally avoids
plotting code and writes CSV/JSON outputs that can be plotted later.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from specnet_data.trace_driven_v1 import (  # noqa: E402
    generate_trace_workload as generate_v1_trace_workload,
    resolve_profile_path as resolve_v1_profile_path,
)
from specnet_data.trace_driven_v2 import (  # noqa: E402
    generate_trace_workload as generate_v2_trace_workload,
    resolve_profile_path as resolve_v2_profile_path,
)
from specnet_data.trace_driven_v3 import (  # noqa: E402
    generate_trace_workload as generate_v3_trace_workload,
    resolve_profile_path as resolve_v3_profile_path,
)


ACTIONS = ("full", "moderate", "conservative", "critical_only", "recovery")
WORKLOAD_PROFILES = (
    "synthetic",
    "trace_driven_v1",
    "trace_driven_v1_1",
    "trace_driven_v2",
    "trace_driven_v3_candidate",
)
TRACE_WORKLOAD_PROFILES = frozenset(WORKLOAD_PROFILES[1:])
DEFAULT_QUALITY_WEIGHTS = (0.5, 1.0, 1.6, 2.5, 4.0, 6.0)
DEFAULT_QUALITY_HARD_FLOOR = 0.90
DEFAULT_LAMBDA_INITIAL = 0.0
DEFAULT_LAMBDA_LEARNING_RATE = 2.0
DEFAULT_LAMBDA_MAX = 12.0
CONTROLLER_VARIANT_FEATURES = {
    "full": ("congestion", "slack", "spec_pressure"),
    "congestion_only": ("congestion",),
    "no_slack": ("congestion", "spec_pressure"),
    "no_spec_pressure": ("congestion", "slack"),
    "path_aware_quality": (
        "slack",
        "required_path_pressure",
        "optional_headroom",
        "spec_pressure",
    ),
}
StateKey = Tuple[str, ...]
SLACK_QUEUE_BASES = ("total", "policy_weighted")
DEFAULT_SLACK_QUEUE_BASIS = "total"
DEFAULT_SLACK_QUEUE_WEIGHT = 1.0
SLACK_ESTIMATORS = {
    "total": "work_queue_aware_v2",
    "policy_weighted": "role_weighted_queue_v2_1",
}
SLACK_TIGHT_THRESHOLD = 0.0
SLACK_LOOSE_THRESHOLD = 1.0

ACTION_CONFIG = {
    "full": {
        "fanout_fraction": 1.00,
        "extra_branches": 99,
        "spawn_background": True,
        "background_scale": 1.00,
        "quality_floor": 1.00,
    },
    "moderate": {
        "fanout_fraction": 0.70,
        "extra_branches": 4,
        "spawn_background": True,
        "background_scale": 0.65,
        "quality_floor": 0.94,
    },
    "conservative": {
        "fanout_fraction": 0.45,
        "extra_branches": 2,
        "spawn_background": True,
        "background_scale": 0.30,
        "quality_floor": 0.86,
    },
    "critical_only": {
        "fanout_fraction": 0.00,
        "extra_branches": 0,
        "spawn_background": False,
        "background_scale": 0.00,
        "quality_floor": 0.76,
    },
    "recovery": {
        "fanout_fraction": 0.85,
        "extra_branches": 6,
        "spawn_background": True,
        "background_scale": 1.00,
        "quality_floor": 0.98,
    },
}

ACTION_COUPLING_MODES = ("legacy", "decoupled")
DEFAULT_ACTION_COUPLING = "legacy"
# Background load is an independent traffic-control knob in decoupled mode.
# Quality-bearing branch fanout remains defined by ACTION_CONFIG.
DECOUPLED_BACKGROUND_SCALE = {
    "full": 0.10,
    "moderate": 0.10,
    "conservative": 0.00,
    "critical_only": 0.00,
    "recovery": 0.10,
}


TEMPLATES = {
    "rag_qa": {
        "max_branches": 8,
        "required_branches": 3,
        "branch_types": ("retrieval", "retrieval", "retrieval", "retrieval", "llm", "retrieval", "tool", "retrieval"),
        "deadline_base": 230.0,
        "background_count": 2,
    },
    "coding": {
        "max_branches": 7,
        "required_branches": 3,
        "branch_types": ("tool", "retrieval", "tool", "llm", "retrieval", "tool", "storage"),
        "deadline_base": 300.0,
        "background_count": 3,
    },
    "research": {
        "max_branches": 12,
        "required_branches": 4,
        "branch_types": ("retrieval", "retrieval", "retrieval", "retrieval", "retrieval", "retrieval", "llm", "llm", "tool", "tool", "retrieval", "storage"),
        "deadline_base": 360.0,
        "background_count": 3,
    },
    "debate": {
        "max_branches": 6,
        "required_branches": 3,
        "branch_types": ("llm", "llm", "llm", "llm", "llm", "retrieval"),
        "deadline_base": 280.0,
        "background_count": 2,
    },
}


SERVICE_SIZE = {
    "planner": (6.0, 1.5),
    "retrieval": (28.0, 0.55),
    "tool": (42.0, 0.65),
    "storage": (64.0, 0.70),
    "llm": (46.0, 0.60),
    "judge": (14.0, 0.35),
    "background": (78.0, 0.75),
}


LOAD_CONFIG = {
    "light": {"mean_interarrival": 55.0, "burst_probability": 0.05, "capacity": 16.0},
    "medium": {"mean_interarrival": 36.0, "burst_probability": 0.12, "capacity": 16.0},
    "heavy": {"mean_interarrival": 24.0, "burst_probability": 0.20, "capacity": 16.0},
}

NETWORK_MODELS = ("single_bottleneck", "service_paths", "service_paths_borrowing")
SERVICE_PATH_BY_TYPE = {
    "planner": "control",
    "judge": "control",
    "retrieval": "data",
    "tool": "data",
    "storage": "data",
    "background": "data",
    "llm": "model",
}
SERVICE_PATH_ORDER = ("control", "data", "model")
SERVICE_PATH_CAPACITY = 16.0
BASE_REQUIRED_QUALITY = 0.76
DEFAULT_QUALITY_TARGET = 0.95
OPTIONAL_SERVICE_UTILITY = {
    "retrieval": 1.00,
    "tool": 1.10,
    "storage": 0.75,
    "llm": 1.25,
}
JUDGE_RETAIN_LIMIT = {
    "rag_qa": 3,
    "coding": 2,
    "research": 4,
    "debate": 2,
}


@dataclass
class BranchSpec:
    service_type: str
    size: float
    required: bool
    branch_index: int = 0
    selection_probability: float = 0.0
    expected_utility: float = 0.0


@dataclass
class WorkflowSpec:
    workflow_id: int
    arrival_time: int
    template: str
    deadline: float
    planner_size: float
    branches: List[BranchSpec]
    llm_size: float
    judge_size: float
    background_sizes: List[float]
    workload_profile: str = "synthetic"
    workload_source: str = "synthetic"
    record_source: str = "synthetic"
    source_split: str = "synthetic"
    source_record_id: str = ""
    mapping_version: str = "synthetic_v1"


@dataclass
class Flow:
    flow_id: int
    workflow_id: int
    service_type: str
    size: float
    remaining: float
    role: str
    stage: str
    required: bool = False
    speculative: bool = False
    background: bool = False
    created_at: int = 0
    completed_at: Optional[int] = None
    cancelled: bool = False
    served: float = 0.0
    path_id: str = "shared"
    selection_probability: float = 0.0
    expected_utility: float = 0.0
    retained: bool = False
    used_by_judge: bool = False


@dataclass
class WorkflowRuntime:
    spec: WorkflowSpec
    stage: str = "not_arrived"
    start_time: Optional[int] = None
    complete_time: Optional[int] = None
    planner_flow: Optional[int] = None
    branch_flows: List[int] = field(default_factory=list)
    required_branch_flows: List[int] = field(default_factory=list)
    speculative_branch_flows: List[int] = field(default_factory=list)
    background_flows: List[int] = field(default_factory=list)
    llm_flow: Optional[int] = None
    judge_flow: Optional[int] = None
    action: Optional[str] = None
    raw_action: Optional[str] = None
    safe_action: Optional[str] = None
    guard_overridden: bool = False
    override_reason: str = ""
    quality_constraint_infeasible: bool = False
    quality_violation: bool = False
    decision_state: Optional[StateKey] = None
    decision_time: Optional[int] = None
    decision_remaining_budget: Optional[float] = None
    decision_required_work: Optional[float] = None
    decision_active_work: Optional[float] = None
    decision_link_capacity: Optional[float] = None
    decision_active_flow_count: Optional[int] = None
    decision_active_critical_work: Optional[float] = None
    decision_active_normal_work: Optional[float] = None
    decision_active_speculative_work: Optional[float] = None
    decision_active_background_work: Optional[float] = None
    decision_active_other_work: Optional[float] = None
    decision_active_weighted_work: Optional[float] = None
    decision_active_weight_sum: Optional[float] = None
    decision_congestion_ratio: Optional[float] = None
    decision_congestion_bucket: Optional[str] = None
    decision_spec_pressure_ratio: Optional[float] = None
    decision_spec_pressure_bucket: Optional[str] = None
    decision_queue_time: Optional[float] = None
    decision_estimated_remaining_time: Optional[float] = None
    decision_slack_ratio: Optional[float] = None
    decision_slack_bucket: Optional[str] = None
    decision_required_path_pressure_ratio: Optional[float] = None
    decision_required_path_pressure_bucket: Optional[str] = None
    decision_optional_headroom_ratio: Optional[float] = None
    decision_optional_headroom_bucket: Optional[str] = None
    predicted_quality: float = 1.0
    quality: float = BASE_REQUIRED_QUALITY
    wasted_speculative_bytes: float = 0.0
    useful_speculative_bytes: float = 0.0
    unused_speculative_bytes: float = 0.0
    retained_optional_count: int = 0
    selected_optional_utility: float = 0.0
    total_optional_utility: float = 0.0
    background_bytes_served: float = 0.0
    quality_accounted: bool = False

    @property
    def deadline_time(self) -> float:
        return self.spec.arrival_time + self.spec.deadline


def lognormal_size(rng: random.Random, service_type: str) -> float:
    mean, sigma = SERVICE_SIZE[service_type]
    # Convert a target mean into the mu parameter of a log-normal distribution.
    mu = math.log(mean) - 0.5 * sigma * sigma
    return max(2.0, rng.lognormvariate(mu, sigma))


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def optional_branch_value(service_type: str, optional_rank: int) -> Tuple[float, float]:
    """Return deterministic selection probability and expected quality utility."""
    base_utility = OPTIONAL_SERVICE_UTILITY.get(service_type, 0.80)
    selection_probability = max(0.20, 0.90 / (1.0 + 0.22 * optional_rank))
    expected_utility = selection_probability * base_utility
    return selection_probability, expected_utility


def generate_synthetic_workload(
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
) -> List[WorkflowSpec]:
    rng = random.Random(seed)
    config = LOAD_CONFIG[load]
    specs: List[WorkflowSpec] = []
    t = 0.0
    workflow_id = 0
    template_names = list(TEMPLATES.keys())
    template_weights = [0.28, 0.24, 0.30, 0.18]

    while t < duration and len(specs) < max_workflows:
        interarrival = rng.expovariate(1.0 / config["mean_interarrival"])
        if rng.random() < config["burst_probability"]:
            interarrival *= 0.18
        t += interarrival
        if t >= duration:
            break

        template = rng.choices(template_names, weights=template_weights, k=1)[0]
        meta = TEMPLATES[template]
        required_count = meta["required_branches"]
        branches: List[BranchSpec] = []
        optional_rank = 0
        for idx, service_type in enumerate(meta["branch_types"]):
            required = idx < required_count
            if required:
                selection_probability = 1.0
                expected_utility = 0.0
            else:
                selection_probability, expected_utility = optional_branch_value(
                    service_type,
                    optional_rank,
                )
                optional_rank += 1
            branches.append(
                BranchSpec(
                    service_type=service_type,
                    size=lognormal_size(rng, service_type),
                    required=required,
                    branch_index=idx,
                    selection_probability=selection_probability,
                    expected_utility=expected_utility,
                )
            )

        background_sizes = [
            lognormal_size(rng, "background") for _ in range(meta["background_count"])
        ]
        deadline_noise = rng.uniform(0.88, 1.18)
        specs.append(
            WorkflowSpec(
                workflow_id=workflow_id,
                arrival_time=int(t),
                template=template,
                deadline=meta["deadline_base"] * deadline_noise,
                planner_size=lognormal_size(rng, "planner"),
                branches=branches,
                llm_size=lognormal_size(rng, "llm"),
                judge_size=lognormal_size(rng, "judge"),
                background_sizes=background_sizes,
            )
        )
        workflow_id += 1

    return specs


def generate_workload(
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    workload_profile: str = "synthetic",
    phase: str = "train",
    trace_profile_path: Optional[str] = None,
) -> List[WorkflowSpec]:
    if workload_profile == "synthetic":
        return generate_synthetic_workload(seed, load, duration, max_workflows)
    if workload_profile not in TRACE_WORKLOAD_PROFILES:
        raise ValueError(f"unknown workload profile: {workload_profile}")

    target_count = min(
        max_workflows,
        max(1, int(round(duration / LOAD_CONFIG[load]["mean_interarrival"]))),
    )
    if workload_profile == "trace_driven_v2":
        rows = generate_v2_trace_workload(
            profile_path=trace_profile_path,
            seed=seed,
            load=load,
            duration=duration,
            max_workflows=max_workflows,
            target_count=target_count,
            phase=phase,
        )
    elif workload_profile == "trace_driven_v3_candidate":
        rows = generate_v3_trace_workload(
            profile_path=trace_profile_path,
            seed=seed,
            load=load,
            duration=duration,
            max_workflows=max_workflows,
            target_count=target_count,
            phase=phase,
        )
    else:
        rows = generate_v1_trace_workload(
            profile_path=trace_profile_path,
            seed=seed,
            load=load,
            duration=duration,
            max_workflows=max_workflows,
            target_count=target_count,
            phase=phase,
            fill_to_target=workload_profile == "trace_driven_v1_1",
        )

    specs: List[WorkflowSpec] = []
    for row in rows:
        branches: List[BranchSpec] = []
        optional_rank = 0
        for branch_index, branch in enumerate(row["branches"]):
            required = bool(branch["required"])
            if required:
                selection_probability = 1.0
                expected_utility = 0.0
            else:
                selection_probability, expected_utility = optional_branch_value(
                    str(branch["service_type"]),
                    optional_rank,
                )
                optional_rank += 1
            branches.append(
                BranchSpec(
                    service_type=str(branch["service_type"]),
                    size=float(branch["size"]),
                    required=required,
                    branch_index=branch_index,
                    selection_probability=selection_probability,
                    expected_utility=expected_utility,
                )
            )
        specs.append(
            WorkflowSpec(
                workflow_id=int(row["workflow_id"]),
                arrival_time=int(row["arrival_time"]),
                template=str(row["template"]),
                deadline=float(row["deadline"]),
                planner_size=float(row["planner_size"]),
                branches=branches,
                llm_size=float(row["llm_size"]),
                judge_size=float(row["judge_size"]),
                background_sizes=[
                    float(value) for value in row["background_sizes"]
                ],
                workload_profile=workload_profile,
                workload_source=str(row.get("workload_source", "trace")),
                record_source=str(
                    row.get("record_source", "tracelab")
                ),
                source_split=str(row["source_split"]),
                source_record_id=str(row["source_record_id"]),
                mapping_version=str(
                    row.get("mapping_version", "fixed_template_v1")
                ),
            )
        )
    return specs


class Policy:
    name = "base"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)
        self.action_counter: Counter[str] = Counter()
        self.raw_action_counter: Counter[str] = Counter()

    def reset_for_run(self) -> None:
        self.action_counter.clear()
        self.raw_action_counter.clear()

    def decide_action(self, sim: "Simulator", workflow: WorkflowRuntime) -> str:
        return "full"

    def flow_weight(self, flow: Flow, sim: "Simulator") -> float:
        return 1.0

    def on_workflow_complete(self, workflow: WorkflowRuntime, sim: "Simulator") -> None:
        return None

    def metadata(self) -> Dict[str, object]:
        return {}


class FIFOPolicy(Policy):
    name = "fifo"

    def flow_weight(self, flow: Flow, sim: "Simulator") -> float:
        return 1.0


class StaticPriorityPolicy(Policy):
    name = "static_priority"

    def flow_weight(self, flow: Flow, sim: "Simulator") -> float:
        service_weights = {
            "llm": 5.0,
            "planner": 4.0,
            "judge": 4.0,
            "retrieval": 2.5,
            "tool": 2.0,
            "storage": 1.5,
            "background": 0.6,
        }
        return service_weights.get(flow.service_type, 1.0)


class CriticalPathOnlyPolicy(Policy):
    name = "critical_path_only"

    def flow_weight(self, flow: Flow, sim: "Simulator") -> float:
        if flow.role == "critical_control":
            return 12.0
        if flow.role == "critical_bulk":
            return 8.0
        if flow.role == "normal":
            return 3.0
        if flow.speculative:
            return 0.9
        if flow.background:
            return 0.5
        return 1.0


class RuleBasedFeedbackPolicy(CriticalPathOnlyPolicy):
    name = "rule_based_feedback"

    def __init__(self, seed: int = 0, profile: str = "balanced", name: Optional[str] = None) -> None:
        super().__init__(seed=seed)
        self.profile = profile
        self.name = name or f"rule_{profile}"

    def decide_action(self, sim: "Simulator", workflow: WorkflowRuntime) -> str:
        congestion = sim.congestion_level()
        slack_ratio = sim.workflow_budget_ratio(workflow)
        background_pressure = sim.background_pressure()

        if self.profile == "aggressive":
            if congestion in {"high", "medium"} or slack_ratio < 0.30:
                action = "critical_only"
            elif slack_ratio < 0.55:
                action = "conservative"
            elif congestion == "low" and background_pressure > 0.20:
                action = "recovery"
            else:
                action = "moderate"
        elif self.profile == "balanced":
            if congestion == "high" or slack_ratio < 0.18:
                action = "critical_only"
            elif congestion == "medium" or slack_ratio < 0.35:
                action = "conservative"
            elif congestion == "low" and background_pressure > 0.25:
                action = "recovery"
            else:
                action = "moderate"
        elif self.profile == "quality_preserving":
            if congestion == "high" and slack_ratio < 0.15:
                action = "critical_only"
            elif congestion == "high" or slack_ratio < 0.30:
                action = "conservative"
            elif congestion == "medium" and slack_ratio < 0.20:
                action = "conservative"
            elif congestion == "low" and slack_ratio > 0.60 and background_pressure < 0.20:
                action = "full"
            elif congestion == "low" and background_pressure > 0.25:
                action = "recovery"
            else:
                action = "moderate"
        else:
            raise ValueError(f"unknown rule profile: {self.profile}")
        return action


class SpecNetAgentBanditPolicy(CriticalPathOnlyPolicy):
    name = "specnet_agent"

    def __init__(
        self,
        seed: int = 0,
        epsilon: float = 0.12,
        learning_rate: float = 0.22,
        train: bool = True,
        name: str = "specnet_agent",
        quality_weight: float = 1.60,
        controller_variant: str = "full",
        epsilon_schedule: str = "linear",
        epsilon_end: float = 0.03,
        epsilon_decay_fraction: float = 0.80,
        learning_rate_schedule: str = "visit_decay",
        learning_rate_min: float = 0.03,
        slack_queue_basis: str = DEFAULT_SLACK_QUEUE_BASIS,
        slack_queue_weight: float = DEFAULT_SLACK_QUEUE_WEIGHT,
        quality_target: float = DEFAULT_QUALITY_TARGET,
        quality_hard_floor: float = DEFAULT_QUALITY_HARD_FLOOR,
        lambda_initial: float = DEFAULT_LAMBDA_INITIAL,
        lambda_learning_rate: float = DEFAULT_LAMBDA_LEARNING_RATE,
        lambda_max: float = DEFAULT_LAMBDA_MAX,
    ) -> None:
        super().__init__(seed=seed)
        if controller_variant not in CONTROLLER_VARIANT_FEATURES:
            raise ValueError(f"unknown controller variant: {controller_variant}")
        if epsilon_schedule not in {"fixed", "linear"}:
            raise ValueError(f"unknown epsilon schedule: {epsilon_schedule}")
        if learning_rate_schedule not in {"fixed", "visit_decay"}:
            raise ValueError(f"unknown learning-rate schedule: {learning_rate_schedule}")
        if not 0.0 <= epsilon <= 1.0 or not 0.0 <= epsilon_end <= 1.0:
            raise ValueError("epsilon values must be in [0, 1]")
        if not 0.0 < epsilon_decay_fraction <= 1.0:
            raise ValueError("epsilon decay fraction must be in (0, 1]")
        if learning_rate <= 0.0 or learning_rate_min <= 0.0:
            raise ValueError("learning rates must be positive")
        if slack_queue_basis not in SLACK_QUEUE_BASES:
            raise ValueError(f"unknown Slack queue basis: {slack_queue_basis}")
        if slack_queue_weight < 0.0:
            raise ValueError("Slack queue weight must be non-negative")
        if not 0.0 <= quality_hard_floor <= quality_target <= 1.0:
            raise ValueError("quality constraints must satisfy 0 <= hard floor <= target <= 1")
        if lambda_initial < 0.0 or lambda_learning_rate < 0.0 or lambda_max < 0.0:
            raise ValueError("lambda parameters must be non-negative")
        if lambda_initial > lambda_max:
            raise ValueError("lambda initial value must not exceed lambda max")
        self.name = name
        self.quality_weight = quality_weight
        self.controller_variant = controller_variant
        self.state_features = CONTROLLER_VARIANT_FEATURES[controller_variant]
        self.epsilon_start = epsilon
        self.epsilon = epsilon
        self.epsilon_end = epsilon_end
        self.epsilon_schedule = epsilon_schedule
        self.epsilon_decay_fraction = epsilon_decay_fraction
        self.learning_rate_start = learning_rate
        self.learning_rate = learning_rate
        self.learning_rate_min = learning_rate_min
        self.learning_rate_schedule = learning_rate_schedule
        self.slack_queue_basis = slack_queue_basis
        self.slack_queue_weight = slack_queue_weight
        self.quality_target = quality_target
        self.quality_hard_floor = quality_hard_floor
        self.quality_lagrange_multiplier = lambda_initial
        self.lambda_learning_rate = lambda_learning_rate
        self.lambda_max = lambda_max
        self.lambda_updates: List[Dict[str, object]] = []
        self.train = train
        self.final_training_epsilon = epsilon
        self.selected_checkpoint_episode: Optional[int] = None
        self.training_checkpoints: List[Dict[str, object]] = []
        self.q_values: Dict[StateKey, Dict[str, float]] = defaultdict(
            lambda: {action: 0.0 for action in ACTIONS}
        )
        self.counts: Dict[StateKey, Counter[str]] = defaultdict(Counter)
        self.training_info: Dict[str, object] = {}

    def set_training_progress(self, episode_index: int, total_episodes: int) -> None:
        """Update epsilon for a zero-based training episode."""
        if self.epsilon_schedule == "fixed":
            self.epsilon = self.epsilon_start
            return
        total_transitions = max(1, total_episodes - 1)
        overall_progress = min(1.0, max(0.0, episode_index / total_transitions))
        decay_progress = min(1.0, overall_progress / self.epsilon_decay_fraction)
        self.epsilon = self.epsilon_start + decay_progress * (self.epsilon_end - self.epsilon_start)

    def effective_learning_rate(self, state: StateKey, action: str) -> float:
        if self.learning_rate_schedule == "fixed":
            return self.learning_rate_start
        next_visit = self.counts[state][action] + 1
        return max(self.learning_rate_min, self.learning_rate_start / math.sqrt(next_visit))

    def state_key(self, sim: "Simulator", workflow: WorkflowRuntime) -> StateKey:
        state_getters = {
            "congestion": sim.congestion_level,
            "slack": lambda: sim.workflow_slack_bucket(workflow),
            "spec_pressure": sim.speculative_pressure_bucket,
            "required_path_pressure": lambda: sim.required_path_pressure_bucket(workflow),
            "optional_headroom": lambda: sim.optional_headroom_bucket(workflow),
        }
        return tuple(state_getters[feature]() for feature in self.state_features)

    def decide_action(self, sim: "Simulator", workflow: WorkflowRuntime) -> str:
        state = self.state_key(sim, workflow)
        if self.train and self.rng.random() < self.epsilon:
            action = self.rng.choice(ACTIONS)
        else:
            q_for_state = self.q_values[state]
            action = max(ACTIONS, key=lambda a: (q_for_state[a], -ACTIONS.index(a)))
        workflow.decision_state = state
        return action

    def on_workflow_complete(self, workflow: WorkflowRuntime, sim: "Simulator") -> None:
        if not self.train or workflow.decision_state is None or workflow.action is None:
            return
        reward = sim.workflow_reward(workflow)
        state = workflow.decision_state
        action = workflow.action
        old_value = self.q_values[state][action]
        learning_rate = self.effective_learning_rate(state, action)
        self.q_values[state][action] = old_value + learning_rate * (reward - old_value)
        self.counts[state][action] += 1

    def update_quality_multiplier(
        self,
        load_summaries: Dict[str, Dict[str, object]],
        episode: int,
    ) -> None:
        missing_loads = [
            load
            for load, summary in load_summaries.items()
            if int(summary["completed"]) == 0
        ]
        if missing_loads:
            self.lambda_updates.append(
                {
                    "episode": episode,
                    "quality_by_load": {
                        load: (
                            float(summary["avg_quality"])
                            if int(summary["completed"]) > 0
                            else None
                        )
                        for load, summary in load_summaries.items()
                    },
                    "worst_load_quality": "",
                    "quality_gap": "",
                    "lambda_before": self.quality_lagrange_multiplier,
                    "lambda_after": self.quality_lagrange_multiplier,
                    "updated": False,
                    "missing_loads": missing_loads,
                }
            )
            return
        quality_by_load = {
            load: float(summary["avg_quality"])
            for load, summary in load_summaries.items()
        }
        worst_gap = max(
            self.quality_target - average_quality
            for average_quality in quality_by_load.values()
        )
        old_multiplier = self.quality_lagrange_multiplier
        self.quality_lagrange_multiplier = min(
            self.lambda_max,
            max(0.0, old_multiplier + self.lambda_learning_rate * worst_gap),
        )
        self.lambda_updates.append(
            {
                "episode": episode,
                "quality_by_load": quality_by_load,
                "worst_load_quality": min(quality_by_load.values()),
                "quality_gap": worst_gap,
                "lambda_before": old_multiplier,
                "lambda_after": self.quality_lagrange_multiplier,
                "updated": True,
                "missing_loads": [],
            }
        )

    def set_evaluation_mode(self) -> None:
        if self.train:
            self.final_training_epsilon = self.epsilon
        self.train = False
        self.epsilon = 0.0

    def model_snapshot(self) -> Dict[str, object]:
        return {
            "q_values": {state: dict(values) for state, values in self.q_values.items()},
            "counts": {state: Counter(values) for state, values in self.counts.items()},
            "quality_lagrange_multiplier": self.quality_lagrange_multiplier,
        }

    def restore_snapshot(self, snapshot: Dict[str, object]) -> None:
        q_values = snapshot["q_values"]
        counts = snapshot["counts"]
        self.q_values = defaultdict(
            lambda: {action: 0.0 for action in ACTIONS},
            {state: dict(values) for state, values in q_values.items()},
        )
        self.counts = defaultdict(
            Counter,
            {state: Counter(values) for state, values in counts.items()},
        )
        self.quality_lagrange_multiplier = float(
            snapshot.get("quality_lagrange_multiplier", self.quality_lagrange_multiplier)
        )

    def metadata(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "quality_weight": self.quality_weight,
            "quality_target": self.quality_target,
            "quality_hard_floor": self.quality_hard_floor,
            "quality_lagrange_multiplier": self.quality_lagrange_multiplier,
            "lambda_learning_rate": self.lambda_learning_rate,
            "lambda_max": self.lambda_max,
            "lambda_updates": self.lambda_updates,
            "controller_variant": self.controller_variant,
            "state_features": list(self.state_features),
            "training_schedule": {
                "epsilon_schedule": self.epsilon_schedule,
                "epsilon_start": self.epsilon_start,
                "epsilon_end": self.epsilon_end,
                "epsilon_decay_fraction": self.epsilon_decay_fraction,
                "final_training_epsilon": self.final_training_epsilon,
                "learning_rate_schedule": self.learning_rate_schedule,
                "learning_rate_start": self.learning_rate_start,
                "learning_rate_min": self.learning_rate_min,
            },
            "selected_checkpoint_episode": self.selected_checkpoint_episode,
            "training_checkpoints": self.training_checkpoints,
            "slack_estimator": SLACK_ESTIMATORS[self.slack_queue_basis],
            "slack_queue_basis": self.slack_queue_basis,
            "slack_queue_weight": self.slack_queue_weight,
            "slack_thresholds": {
                "tight_below": SLACK_TIGHT_THRESHOLD,
                "loose_at_or_above": SLACK_LOOSE_THRESHOLD,
            },
            "training_info": self.training_info,
            "q_values": {str(k): v for k, v in self.q_values.items()},
            "counts": {str(k): dict(v) for k, v in self.counts.items()},
        }


class Simulator:
    def __init__(
        self,
        specs: List[WorkflowSpec],
        policy: Policy,
        load: str,
        seed: int,
        duration: int,
        max_time: int,
        quality_weight: float = 1.60,
        slack_queue_basis: str = DEFAULT_SLACK_QUEUE_BASIS,
        slack_queue_weight: float = DEFAULT_SLACK_QUEUE_WEIGHT,
        network_model: str = "single_bottleneck",
        single_bottleneck_capacity: Optional[float] = None,
        action_coupling: str = DEFAULT_ACTION_COUPLING,
        quality_target: float = DEFAULT_QUALITY_TARGET,
        quality_hard_floor: float = DEFAULT_QUALITY_HARD_FLOOR,
        safety_guard: bool = False,
    ) -> None:
        if slack_queue_basis not in SLACK_QUEUE_BASES:
            raise ValueError(f"unknown Slack queue basis: {slack_queue_basis}")
        if slack_queue_weight < 0.0:
            raise ValueError("Slack queue weight must be non-negative")
        if not 0.0 <= quality_hard_floor <= quality_target <= 1.0:
            raise ValueError("quality constraints must satisfy 0 <= hard floor <= target <= 1")
        if network_model not in NETWORK_MODELS:
            raise ValueError(f"unknown network model: {network_model}")
        if single_bottleneck_capacity is not None and single_bottleneck_capacity <= 0.0:
            raise ValueError("single-bottleneck capacity must be positive")
        if network_model != "single_bottleneck" and single_bottleneck_capacity is not None:
            raise ValueError("single-bottleneck capacity only applies to single_bottleneck")
        if action_coupling not in ACTION_COUPLING_MODES:
            raise ValueError(f"unknown action coupling mode: {action_coupling}")
        self.specs = list(specs)
        self.policy = policy
        self.load = load
        self.seed = seed
        self.duration = duration
        self.max_time = max_time
        self.quality_weight = quality_weight
        self.slack_queue_basis = slack_queue_basis
        self.slack_queue_weight = slack_queue_weight
        self.network_model = network_model
        self.action_coupling = action_coupling
        self.quality_target = quality_target
        self.quality_hard_floor = quality_hard_floor
        self.safety_guard = safety_guard
        self.capacity = (
            single_bottleneck_capacity
            if single_bottleneck_capacity is not None
            else LOAD_CONFIG[load]["capacity"]
        )
        self.path_capacities = (
            {"shared": self.capacity}
            if network_model == "single_bottleneck"
            else {path_id: SERVICE_PATH_CAPACITY for path_id in SERVICE_PATH_ORDER}
        )
        self.time = 0
        self.next_flow_id = 0
        self.flows: Dict[int, Flow] = {}
        self.workflows: Dict[int, WorkflowRuntime] = {
            spec.workflow_id: WorkflowRuntime(spec=spec) for spec in specs
        }
        self.completed_workflows: List[WorkflowRuntime] = []
        self.total_capacity = 0.0
        self.total_served = 0.0
        self.queue_pressure_samples: List[float] = []
        self.path_total_capacity: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_total_served: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_queue_pressure_samples: Dict[str, List[float]] = {
            path_id: [] for path_id in self.path_capacities
        }
        self.path_total_base_served: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_total_lent_served: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_total_borrowed_received: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_total_unused_after_lending: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }
        self.path_total_home_flow_served: Dict[str, float] = {
            path_id: 0.0 for path_id in self.path_capacities
        }

    def path_for_service_type(self, service_type: str) -> str:
        if self.network_model == "single_bottleneck":
            return "shared"
        try:
            return SERVICE_PATH_BY_TYPE[service_type]
        except KeyError as exc:
            raise ValueError(f"unknown service type for service_paths: {service_type}") from exc

    def new_flow(
        self,
        workflow: WorkflowRuntime,
        service_type: str,
        size: float,
        role: str,
        stage: str,
        required: bool = False,
        speculative: bool = False,
        background: bool = False,
        selection_probability: float = 0.0,
        expected_utility: float = 0.0,
    ) -> int:
        flow = Flow(
            flow_id=self.next_flow_id,
            workflow_id=workflow.spec.workflow_id,
            service_type=service_type,
            size=size,
            remaining=size,
            role=role,
            stage=stage,
            required=required,
            speculative=speculative,
            background=background,
            created_at=self.time,
            path_id=self.path_for_service_type(service_type),
            selection_probability=selection_probability,
            expected_utility=expected_utility,
        )
        self.flows[flow.flow_id] = flow
        self.next_flow_id += 1
        return flow.flow_id

    def active_flows(self) -> List[Flow]:
        return [
            flow
            for flow in self.flows.values()
            if flow.completed_at is None and not flow.cancelled and flow.remaining > 1e-9
        ]

    def remaining_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows())

    def speculative_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows() if flow.speculative)

    def background_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows() if flow.background)

    def active_queue_diagnostics(self) -> Dict[str, float]:
        diagnostics = {
            "flow_count": 0.0,
            "critical_work": 0.0,
            "normal_work": 0.0,
            "speculative_work": 0.0,
            "background_work": 0.0,
            "other_work": 0.0,
            "weighted_work": 0.0,
            "weight_sum": 0.0,
        }
        for flow in self.active_flows():
            diagnostics["flow_count"] += 1.0
            if flow.speculative:
                category = "speculative_work"
            elif flow.background:
                category = "background_work"
            elif flow.role in {"critical_control", "critical_bulk"}:
                category = "critical_work"
            elif flow.role == "normal":
                category = "normal_work"
            else:
                category = "other_work"
            diagnostics[category] += flow.remaining
            weight = max(0.0, self.policy.flow_weight(flow, self))
            diagnostics["weighted_work"] += flow.remaining * weight
            diagnostics["weight_sum"] += weight
        return diagnostics

    def congestion_ratio(self) -> float:
        # A rough pressure metric: active bytes relative to 12 scheduling epochs of capacity.
        return self.remaining_active_bytes() / max(1.0, self.capacity * 12.0)

    def congestion_level(self) -> str:
        ratio = self.congestion_ratio()
        if ratio < 0.85:
            return "low"
        if ratio < 1.85:
            return "medium"
        return "high"

    def background_pressure(self) -> float:
        total = self.remaining_active_bytes()
        return self.background_active_bytes() / total if total else 0.0

    def speculative_pressure_bucket(self) -> str:
        total = self.remaining_active_bytes()
        ratio = self.speculative_active_bytes() / total if total else 0.0
        if ratio < 0.15:
            return "low_spec"
        if ratio < 0.35:
            return "mid_spec"
        return "high_spec"

    def workflow_required_work(self, workflow: WorkflowRuntime) -> float:
        required_branch_work = sum(branch.size for branch in workflow.spec.branches if branch.required)
        return required_branch_work + workflow.spec.llm_size + workflow.spec.judge_size

    def required_work_by_path(self, workflow: WorkflowRuntime) -> Dict[str, float]:
        required_work = {path_id: 0.0 for path_id in self.path_capacities}
        for flow in self.active_flows():
            if flow.required:
                required_work[flow.path_id] += flow.remaining
        for branch in workflow.spec.branches:
            if branch.required:
                required_work[self.path_for_service_type(branch.service_type)] += branch.size
        required_work[self.path_for_service_type("llm")] += workflow.spec.llm_size
        required_work[self.path_for_service_type("judge")] += workflow.spec.judge_size
        return required_work

    def required_path_pressure_ratio(self, workflow: WorkflowRuntime) -> float:
        required_work = self.required_work_by_path(workflow)
        return max(
            required_work[path_id] / max(1.0, capacity * 12.0)
            for path_id, capacity in self.path_capacities.items()
        )

    def required_path_pressure_bucket(self, workflow: WorkflowRuntime) -> str:
        ratio = self.required_path_pressure_ratio(workflow)
        if ratio < 0.85:
            return "low_required_path"
        if ratio < 1.85:
            return "mid_required_path"
        return "high_required_path"

    def optional_headroom_ratio(self, workflow: WorkflowRuntime) -> float:
        required_work = self.required_work_by_path(workflow)
        total_horizon_capacity = sum(self.path_capacities.values()) * 12.0
        headroom = sum(
            max(capacity * 12.0 - required_work[path_id], 0.0)
            for path_id, capacity in self.path_capacities.items()
        )
        return headroom / max(1.0, total_horizon_capacity)

    def optional_headroom_bucket(self, workflow: WorkflowRuntime) -> str:
        ratio = self.optional_headroom_ratio(workflow)
        if ratio < 0.25:
            return "low_optional_headroom"
        if ratio < 0.60:
            return "mid_optional_headroom"
        return "high_optional_headroom"

    def slack_queue_work(self, queue_diagnostics: Optional[Dict[str, float]] = None) -> float:
        if self.slack_queue_basis == "total":
            return self.remaining_active_bytes()
        diagnostics = queue_diagnostics or self.active_queue_diagnostics()
        return diagnostics["weighted_work"]

    def workflow_estimated_remaining_time(self, workflow: WorkflowRuntime) -> float:
        own_service_time = self.workflow_required_work(workflow) / max(1.0, self.capacity)
        queue_time = self.slack_queue_work() / max(1.0, self.capacity)
        return own_service_time + self.slack_queue_weight * queue_time

    def workflow_budget_ratio(self, workflow: WorkflowRuntime) -> float:
        remaining_budget = workflow.deadline_time - self.time
        return remaining_budget / max(1.0, workflow.spec.deadline)

    def workflow_slack_ratio(self, workflow: WorkflowRuntime) -> float:
        remaining_budget = workflow.deadline_time - self.time
        estimated_remaining_time = self.workflow_estimated_remaining_time(workflow)
        absolute_slack = remaining_budget - estimated_remaining_time
        return absolute_slack / max(1.0, estimated_remaining_time)

    def workflow_slack_bucket(self, workflow: WorkflowRuntime) -> str:
        ratio = self.workflow_slack_ratio(workflow)
        if ratio < SLACK_TIGHT_THRESHOLD:
            return "tight"
        if ratio < SLACK_LOOSE_THRESHOLD:
            return "normal"
        return "loose"

    def record_slack_decision(self, workflow: WorkflowRuntime) -> None:
        queue_diagnostics = self.active_queue_diagnostics()
        workflow.decision_time = self.time
        workflow.decision_remaining_budget = workflow.deadline_time - self.time
        workflow.decision_required_work = self.workflow_required_work(workflow)
        workflow.decision_active_work = self.remaining_active_bytes()
        workflow.decision_link_capacity = self.capacity
        workflow.decision_active_flow_count = int(queue_diagnostics["flow_count"])
        workflow.decision_active_critical_work = queue_diagnostics["critical_work"]
        workflow.decision_active_normal_work = queue_diagnostics["normal_work"]
        workflow.decision_active_speculative_work = queue_diagnostics["speculative_work"]
        workflow.decision_active_background_work = queue_diagnostics["background_work"]
        workflow.decision_active_other_work = queue_diagnostics["other_work"]
        workflow.decision_active_weighted_work = queue_diagnostics["weighted_work"]
        workflow.decision_active_weight_sum = queue_diagnostics["weight_sum"]
        workflow.decision_congestion_ratio = self.congestion_ratio()
        workflow.decision_congestion_bucket = self.congestion_level()
        workflow.decision_spec_pressure_ratio = (
            workflow.decision_active_speculative_work / workflow.decision_active_work
            if workflow.decision_active_work
            else 0.0
        )
        workflow.decision_spec_pressure_bucket = self.speculative_pressure_bucket()
        workflow.decision_queue_time = self.slack_queue_work(queue_diagnostics) / max(1.0, self.capacity)
        workflow.decision_estimated_remaining_time = self.workflow_estimated_remaining_time(workflow)
        workflow.decision_slack_ratio = self.workflow_slack_ratio(workflow)
        workflow.decision_slack_bucket = self.workflow_slack_bucket(workflow)
        workflow.decision_required_path_pressure_ratio = self.required_path_pressure_ratio(workflow)
        workflow.decision_required_path_pressure_bucket = self.required_path_pressure_bucket(workflow)
        workflow.decision_optional_headroom_ratio = self.optional_headroom_ratio(workflow)
        workflow.decision_optional_headroom_bucket = self.optional_headroom_bucket(workflow)

    def spawn_arrivals(self) -> None:
        for workflow in self.workflows.values():
            if workflow.stage == "not_arrived" and workflow.spec.arrival_time <= self.time:
                workflow.stage = "planner"
                workflow.start_time = self.time
                workflow.planner_flow = self.new_flow(
                    workflow,
                    "planner",
                    workflow.spec.planner_size,
                    role="critical_control",
                    stage="planner",
                    required=True,
                )

    def completed(self, flow_id: Optional[int]) -> bool:
        if flow_id is None:
            return False
        flow = self.flows[flow_id]
        return flow.completed_at is not None

    def all_completed(self, flow_ids: Iterable[int]) -> bool:
        return all(self.completed(flow_id) for flow_id in flow_ids)

    def branch_count_for_action(self, workflow: WorkflowRuntime, action: str) -> int:
        meta = TEMPLATES[workflow.spec.template]
        max_branches = meta["max_branches"]
        required = meta["required_branches"]
        config = ACTION_CONFIG[action]
        if action == "critical_only":
            return required
        by_fraction = math.ceil(max_branches * config["fanout_fraction"])
        by_extra = required + int(config["extra_branches"])
        return max(required, min(max_branches, by_fraction, by_extra))

    def branches_for_action(
        self,
        workflow: WorkflowRuntime,
        action: str,
    ) -> List[BranchSpec]:
        branch_count = self.branch_count_for_action(workflow, action)
        required = [branch for branch in workflow.spec.branches if branch.required]
        optional_slots = max(0, branch_count - len(required))
        optional = sorted(
            (branch for branch in workflow.spec.branches if not branch.required),
            key=lambda branch: (
                -(branch.expected_utility / max(1e-9, branch.size)),
                branch.branch_index,
            ),
        )
        return required + optional[:optional_slots]

    def quality_for_action(self, workflow: WorkflowRuntime, action: str, branch_count: int) -> float:
        """Estimate quality before execution; realized quality is computed at completion."""
        del branch_count
        optional_specs = [branch for branch in workflow.spec.branches if not branch.required]
        retain_limit = JUDGE_RETAIN_LIMIT.get(workflow.spec.template, len(optional_specs))
        potential_utility = sum(
            sorted(
                (branch.expected_utility for branch in optional_specs),
                reverse=True,
            )[:retain_limit]
        )
        if potential_utility <= 1e-12:
            return 1.0
        selected_optional = [
            branch
            for branch in self.branches_for_action(workflow, action)
            if not branch.required
        ]
        selected_utility = sum(
            sorted(
                (branch.expected_utility for branch in selected_optional),
                reverse=True,
            )[:retain_limit]
        )
        retained_fraction = min(1.0, selected_utility / potential_utility)
        return BASE_REQUIRED_QUALITY + (1.0 - BASE_REQUIRED_QUALITY) * retained_fraction

    def guard_action(
        self,
        workflow: WorkflowRuntime,
        raw_action: str,
    ) -> Tuple[str, float, bool, str]:
        predictions = {
            action: self.quality_for_action(
                workflow,
                action,
                self.branch_count_for_action(workflow, action),
            )
            for action in ACTIONS
        }
        if not self.safety_guard:
            return raw_action, predictions[raw_action], False, ""
        feasible = [
            action
            for action in ACTIONS
            if predictions[action] >= self.quality_hard_floor
        ]
        if raw_action in feasible:
            return raw_action, predictions[raw_action], False, ""
        if not feasible:
            safe_action = max(
                ACTIONS,
                key=lambda action: (predictions[action], -ACTIONS.index(action)),
            )
            return safe_action, predictions[safe_action], True, "quality_constraint_infeasible"
        if isinstance(self.policy, SpecNetAgentBanditPolicy) and workflow.decision_state is not None:
            q_values = self.policy.q_values[workflow.decision_state]
            safe_action = max(
                feasible,
                key=lambda action: (q_values[action], -ACTIONS.index(action)),
            )
        else:
            safe_action = min(
                feasible,
                key=lambda action: (
                    predictions[action],
                    self.branch_count_for_action(workflow, action),
                    ACTIONS.index(action),
                ),
            )
        return safe_action, predictions[safe_action], False, "predicted_quality_below_hard_floor"

    def background_scale_for_action(self, action: str) -> float:
        if self.action_coupling == "legacy":
            return float(ACTION_CONFIG[action]["background_scale"])
        return DECOUPLED_BACKGROUND_SCALE[action]

    def spawn_branches(self, workflow: WorkflowRuntime) -> None:
        self.record_slack_decision(workflow)
        raw_action = self.policy.decide_action(self, workflow)
        action, predicted_quality, infeasible, override_reason = self.guard_action(
            workflow,
            raw_action,
        )
        self.policy.raw_action_counter[raw_action] += 1
        self.policy.action_counter[action] += 1
        workflow.raw_action = raw_action
        workflow.safe_action = action
        workflow.action = action
        workflow.guard_overridden = raw_action != action
        workflow.override_reason = override_reason
        workflow.quality_constraint_infeasible = infeasible
        selected_branches = self.branches_for_action(workflow, action)
        workflow.predicted_quality = predicted_quality
        workflow.stage = "branches"

        for branch in selected_branches:
            required = branch.required
            speculative = not required
            role = "critical_bulk" if required and branch.size >= 32.0 else "critical_control" if required else "speculative"
            flow_id = self.new_flow(
                workflow,
                branch.service_type,
                branch.size,
                role=role,
                stage="branch",
                required=required,
                speculative=speculative,
                selection_probability=branch.selection_probability,
                expected_utility=branch.expected_utility,
            )
            workflow.branch_flows.append(flow_id)
            if required:
                workflow.required_branch_flows.append(flow_id)
            else:
                workflow.speculative_branch_flows.append(flow_id)

        background_scale = self.background_scale_for_action(action)
        if background_scale > 0.0:
            for size in workflow.spec.background_sizes:
                scaled_size = max(1.0, size * background_scale)
                flow_id = self.new_flow(
                    workflow,
                    "background",
                    scaled_size,
                    role="background",
                    stage="background",
                    background=True,
                )
                workflow.background_flows.append(flow_id)

    def progress_workflows(self) -> None:
        for workflow in self.workflows.values():
            if workflow.complete_time is not None:
                continue
            if workflow.stage == "planner" and self.completed(workflow.planner_flow):
                self.spawn_branches(workflow)
            elif workflow.stage == "branches" and self.all_completed(workflow.required_branch_flows):
                workflow.stage = "llm"
                workflow.llm_flow = self.new_flow(
                    workflow,
                    "llm",
                    workflow.spec.llm_size,
                    role="critical_bulk",
                    stage="llm",
                    required=True,
                )
            elif workflow.stage == "llm" and self.completed(workflow.llm_flow):
                workflow.stage = "judge"
                workflow.judge_flow = self.new_flow(
                    workflow,
                    "judge",
                    workflow.spec.judge_size,
                    role="critical_control",
                    stage="judge",
                    required=True,
                )
            elif workflow.stage == "judge" and self.completed(workflow.judge_flow):
                self.finish_workflow(workflow)

    def finish_workflow(self, workflow: WorkflowRuntime) -> None:
        workflow.stage = "done"
        workflow.complete_time = self.time
        self.finalize_quality_and_speculation(workflow)
        for flow_id in workflow.background_flows:
            flow = self.flows[flow_id]
            workflow.background_bytes_served += flow.served
            if flow.completed_at is None:
                flow.cancelled = True
        self.completed_workflows.append(workflow)
        self.policy.on_workflow_complete(workflow, self)

    def finalize_quality_and_speculation(self, workflow: WorkflowRuntime) -> None:
        if workflow.quality_accounted:
            return

        optional_specs = [branch for branch in workflow.spec.branches if not branch.required]
        retain_limit = JUDGE_RETAIN_LIMIT.get(workflow.spec.template, len(optional_specs))
        potential = sorted(
            (branch.expected_utility for branch in optional_specs),
            reverse=True,
        )[:retain_limit]
        workflow.total_optional_utility = sum(potential)

        completed_optional = [
            self.flows[flow_id]
            for flow_id in workflow.speculative_branch_flows
            if self.flows[flow_id].completed_at is not None
        ]
        for flow in completed_optional:
            flow.retained = True
        used = sorted(
            completed_optional,
            key=lambda flow: (-flow.expected_utility, flow.flow_id),
        )[:retain_limit]
        used_ids = {flow.flow_id for flow in used}
        for flow in used:
            flow.used_by_judge = True
            workflow.useful_speculative_bytes += flow.served
            workflow.selected_optional_utility += flow.expected_utility
        workflow.retained_optional_count = len(used)

        for flow_id in workflow.speculative_branch_flows:
            flow = self.flows[flow_id]
            if flow_id not in used_ids:
                workflow.wasted_speculative_bytes += flow.served
                workflow.unused_speculative_bytes += flow.served
            if flow.completed_at is None:
                flow.cancelled = True

        if workflow.total_optional_utility <= 1e-12:
            workflow.quality = 1.0
        else:
            retained_fraction = min(
                1.0,
                workflow.selected_optional_utility / workflow.total_optional_utility,
            )
            workflow.quality = BASE_REQUIRED_QUALITY + (
                1.0 - BASE_REQUIRED_QUALITY
            ) * retained_fraction
        workflow.quality_violation = workflow.quality < self.quality_hard_floor
        workflow.quality_accounted = True

    def serve_capacity_pool(self, active: List[Flow], capacity: float) -> float:
        # Weighted max-min style allocation. It avoids wasting capacity when
        # small flows finish during the epoch.
        remaining_capacity = capacity
        candidates = list(active)
        total_served = 0.0
        while candidates and remaining_capacity > 1e-9:
            weighted = [(flow, max(0.0, self.policy.flow_weight(flow, self))) for flow in candidates]
            total_weight = sum(weight for _, weight in weighted)
            if total_weight <= 1e-12:
                break
            served_this_round = 0.0
            next_candidates: List[Flow] = []
            progressed: List[Flow] = []
            for flow, weight in weighted:
                if remaining_capacity <= 1e-9:
                    break
                share = remaining_capacity * weight / total_weight
                served = min(flow.remaining, share)
                if served <= 1e-12:
                    continue
                flow.remaining -= served
                flow.served += served
                self.total_served += served
                served_this_round += served
                total_served += served
                progressed.append(flow)
            for flow in candidates:
                if flow.remaining <= 1e-9 and flow.completed_at is None:
                    flow.completed_at = self.time + 1
                elif flow.remaining > 1e-9:
                    next_candidates.append(flow)
            remaining_capacity -= served_this_round
            if served_this_round <= 1e-12 or not progressed:
                break
            candidates = next_candidates
        return total_served

    def serve_active_flows(self) -> None:
        active = self.active_flows()
        epoch_capacity = sum(self.path_capacities.values())
        self.total_capacity += epoch_capacity
        for path_id, capacity in self.path_capacities.items():
            self.path_total_capacity[path_id] += capacity
        if not active:
            if self.network_model == "service_paths_borrowing":
                for path_id, capacity in self.path_capacities.items():
                    self.path_total_unused_after_lending[path_id] += capacity
            return

        # Keep this global pressure definition unchanged for Controller and
        # historical avg_queue_pressure compatibility.
        pressure = sum(flow.remaining for flow in active) / max(1.0, self.capacity)
        self.queue_pressure_samples.append(pressure)

        if self.network_model != "service_paths_borrowing":
            for path_id, capacity in self.path_capacities.items():
                path_active = [flow for flow in active if flow.path_id == path_id]
                if not path_active:
                    continue
                path_pressure = sum(flow.remaining for flow in path_active) / max(1.0, capacity)
                self.path_queue_pressure_samples[path_id].append(path_pressure)
                served = self.serve_capacity_pool(path_active, capacity)
                self.path_total_served[path_id] += served
            return

        base_served: Dict[str, float] = {}
        spare_capacity: Dict[str, float] = {}
        for path_id, capacity in self.path_capacities.items():
            path_active = [flow for flow in active if flow.path_id == path_id]
            if path_active:
                path_pressure = sum(flow.remaining for flow in path_active) / max(1.0, capacity)
                self.path_queue_pressure_samples[path_id].append(path_pressure)
                served = self.serve_capacity_pool(path_active, capacity)
            else:
                served = 0.0
            base_served[path_id] = served
            spare_capacity[path_id] = max(0.0, capacity - served)
            self.path_total_base_served[path_id] += served
            self.path_total_home_flow_served[path_id] += served

        borrow_candidates = [flow for flow in active if flow.remaining > 1e-9]
        borrow_pool = sum(spare_capacity.values())
        served_before = {flow.flow_id: flow.served for flow in borrow_candidates}
        borrowed_total = self.serve_capacity_pool(borrow_candidates, borrow_pool)
        for flow in borrow_candidates:
            borrowed = flow.served - served_before[flow.flow_id]
            if borrowed > 1e-12:
                self.path_total_borrowed_received[flow.path_id] += borrowed
                self.path_total_home_flow_served[flow.path_id] += borrowed

        remaining_borrowed = borrowed_total
        for path_id in self.path_capacities:
            lent = min(spare_capacity[path_id], remaining_borrowed)
            unused = spare_capacity[path_id] - lent
            self.path_total_lent_served[path_id] += lent
            self.path_total_unused_after_lending[path_id] += unused
            self.path_total_served[path_id] += base_served[path_id] + lent
            remaining_borrowed -= lent
        if remaining_borrowed > 1e-8:
            raise RuntimeError("borrowed service exceeds available path capacity")

    def workflow_reward(self, workflow: WorkflowRuntime) -> float:
        if workflow.complete_time is None:
            return -10.0
        latency = workflow.complete_time - workflow.spec.arrival_time
        normalized_latency = latency / max(1.0, workflow.spec.deadline)
        deadline_miss = 1.0 if latency > workflow.spec.deadline else 0.0
        wasted_norm = workflow.wasted_speculative_bytes / max(1.0, sum(b.size for b in workflow.spec.branches))
        quality_loss = 1.0 - workflow.quality
        quality_gap = max(0.0, self.quality_target - workflow.quality)
        quality_multiplier = float(
            getattr(self.policy, "quality_lagrange_multiplier", 0.0)
        )
        background_norm = workflow.background_bytes_served / max(1.0, sum(workflow.spec.background_sizes))
        return -(
            1.00 * normalized_latency
            + 3.00 * deadline_miss
            + 0.80 * wasted_norm
            + self.quality_weight * quality_loss
            + quality_multiplier * quality_gap
            + 0.15 * background_norm
        )

    def run(self) -> Dict[str, object]:
        self.policy.reset_for_run()
        for self.time in range(self.max_time):
            self.spawn_arrivals()
            self.progress_workflows()
            self.serve_active_flows()
            self.progress_workflows()

            all_arrived = all(w.stage != "not_arrived" for w in self.workflows.values())
            no_active = not self.active_flows()
            all_done_or_arrived = all(
                w.complete_time is not None or w.stage != "not_arrived"
                for w in self.workflows.values()
            )
            if all_arrived and no_active and all_done_or_arrived:
                break

        # Mark unfinished workflows as timed out at max_time.
        for workflow in self.workflows.values():
            if workflow.complete_time is None and workflow.stage != "not_arrived":
                workflow.complete_time = self.max_time
                self.finalize_quality_and_speculation(workflow)
                for flow_id in workflow.background_flows:
                    flow = self.flows[flow_id]
                    workflow.background_bytes_served += flow.served
                    if flow.completed_at is None:
                        flow.cancelled = True
                self.completed_workflows.append(workflow)

        return self.summary()

    def summary(self) -> Dict[str, object]:
        records = []
        for workflow in self.completed_workflows:
            latency = workflow.complete_time - workflow.spec.arrival_time if workflow.complete_time is not None else 0.0
            records.append(
                {
                    "workflow_id": workflow.spec.workflow_id,
                    "template": workflow.spec.template,
                    "workload_profile": workflow.spec.workload_profile,
                    "workload_source": workflow.spec.workload_source,
                    "record_source": workflow.spec.record_source,
                    "source_split": workflow.spec.source_split,
                    "source_record_id": workflow.spec.source_record_id,
                    "mapping_version": workflow.spec.mapping_version,
                    "branch_count": len(workflow.spec.branches),
                    "required_branch_count": sum(
                        branch.required for branch in workflow.spec.branches
                    ),
                    "planner_work": workflow.spec.planner_size,
                    "required_branch_work": sum(
                        branch.size
                        for branch in workflow.spec.branches
                        if branch.required
                    ),
                    "optional_branch_work": sum(
                        branch.size
                        for branch in workflow.spec.branches
                        if not branch.required
                    ),
                    "llm_work": workflow.spec.llm_size,
                    "judge_work": workflow.spec.judge_size,
                    "background_work": sum(workflow.spec.background_sizes),
                    "total_declared_work": (
                        workflow.spec.planner_size
                        + sum(branch.size for branch in workflow.spec.branches)
                        + workflow.spec.llm_size
                        + workflow.spec.judge_size
                        + sum(workflow.spec.background_sizes)
                    ),
                    "arrival_time": workflow.spec.arrival_time,
                    "deadline": workflow.spec.deadline,
                    "latency": latency,
                    "deadline_miss": 1 if latency > workflow.spec.deadline else 0,
                    "predicted_quality": workflow.predicted_quality,
                    "quality": workflow.quality,
                    "quality_target_met": 1
                    if workflow.quality >= self.quality_target
                    else 0,
                    "action": workflow.action or "none",
                    "raw_action": workflow.raw_action or "none",
                    "safe_action": workflow.safe_action or "none",
                    "guard_overridden": 1 if workflow.guard_overridden else 0,
                    "override_reason": workflow.override_reason,
                    "quality_constraint_infeasible": (
                        1 if workflow.quality_constraint_infeasible else 0
                    ),
                    "quality_violation": 1 if workflow.quality_violation else 0,
                    "decision_time": workflow.decision_time if workflow.decision_time is not None else "",
                    "decision_remaining_budget": workflow.decision_remaining_budget
                    if workflow.decision_remaining_budget is not None
                    else "",
                    "decision_required_work": workflow.decision_required_work
                    if workflow.decision_required_work is not None
                    else "",
                    "decision_active_work": workflow.decision_active_work
                    if workflow.decision_active_work is not None
                    else "",
                    "decision_link_capacity": workflow.decision_link_capacity
                    if workflow.decision_link_capacity is not None
                    else "",
                    "decision_active_flow_count": workflow.decision_active_flow_count
                    if workflow.decision_active_flow_count is not None
                    else "",
                    "decision_active_critical_work": workflow.decision_active_critical_work
                    if workflow.decision_active_critical_work is not None
                    else "",
                    "decision_active_normal_work": workflow.decision_active_normal_work
                    if workflow.decision_active_normal_work is not None
                    else "",
                    "decision_active_speculative_work": workflow.decision_active_speculative_work
                    if workflow.decision_active_speculative_work is not None
                    else "",
                    "decision_active_background_work": workflow.decision_active_background_work
                    if workflow.decision_active_background_work is not None
                    else "",
                    "decision_active_other_work": workflow.decision_active_other_work
                    if workflow.decision_active_other_work is not None
                    else "",
                    "decision_active_weighted_work": workflow.decision_active_weighted_work
                    if workflow.decision_active_weighted_work is not None
                    else "",
                    "decision_active_weight_sum": workflow.decision_active_weight_sum
                    if workflow.decision_active_weight_sum is not None
                    else "",
                    "decision_congestion_ratio": workflow.decision_congestion_ratio
                    if workflow.decision_congestion_ratio is not None
                    else "",
                    "decision_congestion_bucket": workflow.decision_congestion_bucket or "",
                    "decision_spec_pressure_ratio": workflow.decision_spec_pressure_ratio
                    if workflow.decision_spec_pressure_ratio is not None
                    else "",
                    "decision_spec_pressure_bucket": workflow.decision_spec_pressure_bucket or "",
                    "decision_queue_time": workflow.decision_queue_time
                    if workflow.decision_queue_time is not None
                    else "",
                    "decision_estimated_remaining_time": workflow.decision_estimated_remaining_time
                    if workflow.decision_estimated_remaining_time is not None
                    else "",
                    "decision_slack_ratio": workflow.decision_slack_ratio
                    if workflow.decision_slack_ratio is not None
                    else "",
                    "decision_slack_bucket": workflow.decision_slack_bucket or "",
                    "decision_required_path_pressure_ratio": (
                        workflow.decision_required_path_pressure_ratio
                        if workflow.decision_required_path_pressure_ratio is not None
                        else ""
                    ),
                    "decision_required_path_pressure_bucket": (
                        workflow.decision_required_path_pressure_bucket or ""
                    ),
                    "decision_optional_headroom_ratio": (
                        workflow.decision_optional_headroom_ratio
                        if workflow.decision_optional_headroom_ratio is not None
                        else ""
                    ),
                    "decision_optional_headroom_bucket": (
                        workflow.decision_optional_headroom_bucket or ""
                    ),
                    "actual_remaining_latency": workflow.complete_time - workflow.decision_time
                    if workflow.decision_time is not None
                    else "",
                    "wasted_speculative_bytes": workflow.wasted_speculative_bytes,
                    "useful_speculative_bytes": workflow.useful_speculative_bytes,
                    "unused_speculative_bytes": workflow.unused_speculative_bytes,
                    "retained_optional_count": workflow.retained_optional_count,
                    "selected_optional_utility": workflow.selected_optional_utility,
                    "total_optional_utility": workflow.total_optional_utility,
                    "background_bytes_served": workflow.background_bytes_served,
                }
            )
        latencies = [row["latency"] for row in records]
        completed = len(records)
        miss_ratio = sum(row["deadline_miss"] for row in records) / max(1, completed)
        total_wasted = sum(row["wasted_speculative_bytes"] for row in records)
        total_useful = sum(row["useful_speculative_bytes"] for row in records)
        total_unused = sum(row["unused_speculative_bytes"] for row in records)
        total_bg = sum(row["background_bytes_served"] for row in records)
        avg_quality = sum(row["quality"] for row in records) / max(1, completed)
        quality_target_ratio = sum(row["quality_target_met"] for row in records) / max(1, completed)
        quality_violation_ratio = sum(row["quality_violation"] for row in records) / max(
            1,
            completed,
        )
        guard_override_ratio = sum(row["guard_overridden"] for row in records) / max(
            1,
            completed,
        )
        infeasible_ratio = sum(
            row["quality_constraint_infeasible"] for row in records
        ) / max(1, completed)
        path_records = [
            {
                "network_model": self.network_model,
                "path_id": path_id,
                "capacity": capacity,
                "total_served": self.path_total_served[path_id],
                "total_capacity": self.path_total_capacity[path_id],
                "utilization": self.path_total_served[path_id]
                / max(1.0, self.path_total_capacity[path_id]),
                "avg_queue_pressure": statistics.mean(self.path_queue_pressure_samples[path_id])
                if self.path_queue_pressure_samples[path_id]
                else 0.0,
            }
            for path_id, capacity in self.path_capacities.items()
        ]
        path_borrowing_records = (
            [
                {
                    "network_model": self.network_model,
                    "path_id": path_id,
                    "base_capacity": capacity,
                    "base_served": self.path_total_base_served[path_id],
                    "lent_served": self.path_total_lent_served[path_id],
                    "borrowed_received": self.path_total_borrowed_received[path_id],
                    "unused_after_lending": self.path_total_unused_after_lending[path_id],
                    "total_home_flow_served": self.path_total_home_flow_served[path_id],
                }
                for path_id, capacity in self.path_capacities.items()
            ]
            if self.network_model == "service_paths_borrowing"
            else []
        )
        return {
            "policy": self.policy.name,
            "load": self.load,
            "seed": self.seed,
            "action_coupling": self.action_coupling,
            "quality_target": self.quality_target,
            "quality_hard_floor": self.quality_hard_floor,
            "safety_guard": "on" if self.safety_guard else "off",
            "slack_queue_basis": self.slack_queue_basis,
            "slack_queue_weight": self.slack_queue_weight,
            "completed": completed,
            "mean_latency": statistics.mean(latencies) if latencies else 0.0,
            "p95_latency": percentile(latencies, 0.95),
            "p99_latency": percentile(latencies, 0.99),
            "deadline_miss_ratio": miss_ratio,
            "wasted_speculative_bytes_per_workflow": total_wasted / max(1, completed),
            "useful_speculative_bytes_per_workflow": total_useful / max(1, completed),
            "unused_speculative_bytes_per_workflow": total_unused / max(1, completed),
            "background_bytes_served_per_workflow": total_bg / max(1, completed),
            "avg_quality": avg_quality,
            "quality_target_met_ratio": quality_target_ratio,
            "quality_violation_ratio": quality_violation_ratio,
            "guard_override_ratio": guard_override_ratio,
            "quality_constraint_infeasible_ratio": infeasible_ratio,
            "link_utilization": self.total_served / max(1.0, self.total_capacity),
            "avg_queue_pressure": statistics.mean(self.queue_pressure_samples) if self.queue_pressure_samples else 0.0,
            "action_counts": dict(self.policy.action_counter),
            "raw_action_counts": dict(self.policy.raw_action_counter),
            "workflow_records": records,
            "path_records": path_records,
            "path_borrowing_records": path_borrowing_records,
        }


def make_policy(name: str, seed: int, trained_bandit: Optional[SpecNetAgentBanditPolicy] = None) -> Policy:
    if name == "fifo":
        return FIFOPolicy(seed)
    if name == "static_priority":
        return StaticPriorityPolicy(seed)
    if name == "critical_path_only":
        return CriticalPathOnlyPolicy(seed)
    if name in {"rule_based_feedback", "rule_balanced"}:
        return RuleBasedFeedbackPolicy(seed, profile="balanced", name="rule_balanced")
    if name == "rule_aggressive":
        return RuleBasedFeedbackPolicy(seed, profile="aggressive", name="rule_aggressive")
    if name == "rule_quality_preserving":
        return RuleBasedFeedbackPolicy(seed, profile="quality_preserving", name="rule_quality_preserving")
    if name == "specnet_agent":
        if trained_bandit is None:
            return SpecNetAgentBanditPolicy(seed=seed, train=False, epsilon=0.0)
        trained_bandit.set_evaluation_mode()
        return trained_bandit
    raise ValueError(f"unknown policy: {name}")


def quality_weight_policy_name(
    weight: float,
    multi_weight: bool,
    train_seed: Optional[int] = None,
    multi_train_seed: bool = False,
    controller_variant: str = "full",
    multi_controller_variant: bool = False,
) -> str:
    include_variant = multi_controller_variant or controller_variant != "full"
    include_weight = multi_weight or include_variant
    name_parts = ["specnet_agent"]
    if include_variant:
        name_parts.append(controller_variant)
    if include_weight:
        name_parts.extend(("qw", f"{weight:.2f}".replace(".", "_")))
    if multi_train_seed:
        name_parts.extend(("ts", str(train_seed)))
    return "_".join(name_parts)


def parse_quality_weights(args: argparse.Namespace) -> List[float]:
    if not args.quality_weights:
        return [args.quality_weight]
    weights: List[float] = []
    for item in args.quality_weights.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            weights.append(float(item))
        except ValueError as exc:
            raise SystemExit(f"Invalid quality weight: {item}") from exc
    if not weights:
        raise SystemExit("At least one quality weight is required.")
    return weights


def parse_int_list(text: str, label: str) -> List[int]:
    values: List[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise SystemExit(f"Invalid {label}: {item}") from exc
    if not values:
        raise SystemExit(f"At least one {label} is required.")
    return values


def parse_train_seeds(args: argparse.Namespace) -> List[int]:
    if args.train_seeds:
        return parse_int_list(args.train_seeds, "train seed")
    if args.train_seed is not None:
        return [args.train_seed]
    return [args.seed]


def parse_controller_variants(args: argparse.Namespace) -> List[str]:
    variants = [item.strip() for item in args.controller_variants.split(",") if item.strip()]
    if not variants:
        raise SystemExit("At least one controller variant is required.")
    invalid_variants = [variant for variant in variants if variant not in CONTROLLER_VARIANT_FEATURES]
    if invalid_variants:
        valid_text = ",".join(CONTROLLER_VARIANT_FEATURES)
        raise SystemExit(f"Invalid controller variants: {invalid_variants}. Valid variants: {valid_text}")
    if len(set(variants)) != len(variants):
        raise SystemExit("Controller variants must not contain duplicates.")
    return variants


def parse_checkpoint_episodes(text: str) -> List[int]:
    if not text.strip():
        return []
    episodes = parse_int_list(text, "checkpoint episode")
    if any(episode <= 0 for episode in episodes):
        raise SystemExit("Checkpoint episodes must be positive.")
    return sorted(set(episodes))


def serialize_model_snapshot(snapshot: Dict[str, object]) -> Dict[str, object]:
    q_values = snapshot["q_values"]
    counts = snapshot["counts"]
    return {
        "q_values": {str(state): dict(values) for state, values in q_values.items()},
        "counts": {str(state): dict(values) for state, values in counts.items()},
        "quality_lagrange_multiplier": snapshot.get("quality_lagrange_multiplier", 0.0),
    }


def summarize_training_window(summaries: List[Dict[str, object]]) -> Dict[str, object]:
    by_load: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    action_counts: Counter[str] = Counter()
    for summary in summaries:
        by_load[str(summary["load"])].append(summary)
        action_counts.update(summary["action_counts"])

    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "useful_speculative_bytes_per_workflow",
        "unused_speculative_bytes_per_workflow",
        "avg_quality",
        "quality_target_met_ratio",
        "quality_violation_ratio",
        "guard_override_ratio",
        "quality_constraint_infeasible_ratio",
    )
    return {
        "episodes": len(summaries),
        "loads": {
            load: {
                metric: statistics.mean(float(summary[metric]) for summary in load_summaries)
                for metric in metrics
            }
            for load, load_summaries in sorted(by_load.items())
        },
        "action_counts": dict(action_counts),
    }


def policy_from_snapshot(
    source: SpecNetAgentBanditPolicy,
    snapshot: Dict[str, object],
    seed: int,
) -> SpecNetAgentBanditPolicy:
    policy = SpecNetAgentBanditPolicy(
        seed=seed,
        epsilon=source.epsilon_start,
        learning_rate=source.learning_rate_start,
        train=False,
        name=source.name,
        quality_weight=source.quality_weight,
        controller_variant=source.controller_variant,
        epsilon_schedule=source.epsilon_schedule,
        epsilon_end=source.epsilon_end,
        epsilon_decay_fraction=source.epsilon_decay_fraction,
        learning_rate_schedule=source.learning_rate_schedule,
        learning_rate_min=source.learning_rate_min,
        slack_queue_basis=source.slack_queue_basis,
        slack_queue_weight=source.slack_queue_weight,
        quality_target=source.quality_target,
        quality_hard_floor=source.quality_hard_floor,
        lambda_initial=source.quality_lagrange_multiplier,
        lambda_learning_rate=source.lambda_learning_rate,
        lambda_max=source.lambda_max,
    )
    policy.restore_snapshot(snapshot)
    policy.set_evaluation_mode()
    return policy


def evaluate_training_checkpoint(
    source: SpecNetAgentBanditPolicy,
    snapshot: Dict[str, object],
    loads: List[str],
    duration: int,
    max_workflows: int,
    max_time: int,
    validation_seed: int,
    validation_runs: int,
    network_model: str = "single_bottleneck",
    single_bottleneck_capacity: Optional[float] = None,
    action_coupling: str = DEFAULT_ACTION_COUPLING,
    quality_target: float = DEFAULT_QUALITY_TARGET,
    quality_hard_floor: float = DEFAULT_QUALITY_HARD_FLOOR,
    safety_guard: bool = False,
    workload_profile: str = "synthetic",
    trace_profile_path: Optional[str] = None,
) -> Dict[str, object]:
    by_load: Dict[str, List[Dict[str, float]]] = defaultdict(list)
    run_rewards: List[float] = []
    for load_index, load in enumerate(loads):
        for run_index in range(validation_runs):
            workload_seed = validation_seed + 30000 + 1000 * run_index + 17 * load_index
            specs = generate_workload(
                workload_seed,
                load,
                duration,
                max_workflows,
                workload_profile=workload_profile,
                phase="validation",
                trace_profile_path=trace_profile_path,
            )
            policy = policy_from_snapshot(source, snapshot, workload_seed)
            sim = Simulator(
                specs,
                policy,
                load,
                workload_seed,
                duration,
                max_time,
                quality_weight=source.quality_weight,
                slack_queue_basis=source.slack_queue_basis,
                slack_queue_weight=source.slack_queue_weight,
                network_model=network_model,
                single_bottleneck_capacity=single_bottleneck_capacity,
                action_coupling=action_coupling,
                quality_target=quality_target,
                quality_hard_floor=quality_hard_floor,
                safety_guard=safety_guard,
            )
            summary = sim.run()
            rewards = [sim.workflow_reward(workflow) for workflow in sim.completed_workflows]
            mean_reward = statistics.mean(rewards) if rewards else -10.0
            run_rewards.append(mean_reward)
            by_load[load].append(
                {
                    "mean_reward": mean_reward,
                    "p99_latency": float(summary["p99_latency"]),
                    "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
                    "wasted_speculative_bytes_per_workflow": float(
                        summary["wasted_speculative_bytes_per_workflow"]
                    ),
                    "useful_speculative_bytes_per_workflow": float(
                        summary["useful_speculative_bytes_per_workflow"]
                    ),
                    "avg_quality": float(summary["avg_quality"]),
                    "quality_target_met_ratio": float(summary["quality_target_met_ratio"]),
                    "quality_violation_ratio": float(summary["quality_violation_ratio"]),
                }
            )

    load_metrics = {
        load: {
            metric: statistics.mean(item[metric] for item in items)
            for metric in items[0]
        }
        for load, items in sorted(by_load.items())
    }
    max_quality_gap = max(
        max(0.0, quality_target - float(metrics["avg_quality"]))
        for metrics in load_metrics.values()
    )
    max_violation_ratio = max(
        float(metrics["quality_violation_ratio"])
        for metrics in load_metrics.values()
    )
    return {
        "score": statistics.mean(run_rewards),
        "seed": validation_seed,
        "runs_per_load": validation_runs,
        "quality_target": quality_target,
        "quality_hard_floor": quality_hard_floor,
        "constraint_feasible": max_quality_gap <= 1e-12 and max_violation_ratio <= 1e-12,
        "max_quality_gap": max_quality_gap,
        "max_quality_violation_ratio": max_violation_ratio,
        "loads": load_metrics,
    }


def train_specnet_agent(
    episodes: int,
    loads: List[str],
    duration: int,
    max_workflows: int,
    max_time: int,
    seed: int,
    quality_weight: float,
    policy_name: str = "specnet_agent",
    controller_variant: str = "full",
    epsilon_schedule: str = "linear",
    epsilon_start: float = 0.20,
    epsilon_end: float = 0.03,
    epsilon_decay_fraction: float = 0.80,
    learning_rate_schedule: str = "visit_decay",
    learning_rate_start: float = 0.25,
    learning_rate_min: float = 0.03,
    checkpoint_episodes: Optional[List[int]] = None,
    checkpoint_selection: str = "last",
    validation_seed: int = 500007,
    checkpoint_eval_runs: int = 5,
    slack_queue_basis: str = DEFAULT_SLACK_QUEUE_BASIS,
    slack_queue_weight: float = DEFAULT_SLACK_QUEUE_WEIGHT,
    network_model: str = "single_bottleneck",
    single_bottleneck_capacity: Optional[float] = None,
    action_coupling: str = DEFAULT_ACTION_COUPLING,
    quality_target: float = DEFAULT_QUALITY_TARGET,
    quality_hard_floor: float = DEFAULT_QUALITY_HARD_FLOOR,
    safety_guard: bool = False,
    lambda_initial: float = DEFAULT_LAMBDA_INITIAL,
    lambda_learning_rate: float = DEFAULT_LAMBDA_LEARNING_RATE,
    lambda_max: float = DEFAULT_LAMBDA_MAX,
    workload_profile: str = "synthetic",
    trace_profile_path: Optional[str] = None,
) -> SpecNetAgentBanditPolicy:
    if episodes <= 0:
        raise ValueError("training episodes must be positive")
    if checkpoint_selection not in {"last", "best_validation"}:
        raise ValueError(f"unknown checkpoint selection: {checkpoint_selection}")
    if checkpoint_eval_runs <= 0:
        raise ValueError("checkpoint evaluation runs must be positive")
    if network_model not in NETWORK_MODELS:
        raise ValueError(f"unknown network model: {network_model}")
    if action_coupling not in ACTION_COUPLING_MODES:
        raise ValueError(f"unknown action coupling mode: {action_coupling}")
    policy = SpecNetAgentBanditPolicy(
        seed=seed,
        train=True,
        epsilon=epsilon_start,
        learning_rate=learning_rate_start,
        name=policy_name,
        quality_weight=quality_weight,
        controller_variant=controller_variant,
        epsilon_schedule=epsilon_schedule,
        epsilon_end=epsilon_end,
        epsilon_decay_fraction=epsilon_decay_fraction,
        learning_rate_schedule=learning_rate_schedule,
        learning_rate_min=learning_rate_min,
        slack_queue_basis=slack_queue_basis,
        slack_queue_weight=slack_queue_weight,
        quality_target=quality_target,
        quality_hard_floor=quality_hard_floor,
        lambda_initial=lambda_initial,
        lambda_learning_rate=lambda_learning_rate,
        lambda_max=lambda_max,
    )
    requested_checkpoints = checkpoint_episodes or []
    checkpoints = sorted({episode for episode in requested_checkpoints if episode <= episodes} | {episodes})
    checkpoint_models: Dict[int, Dict[str, object]] = {}
    training_window: List[Dict[str, object]] = []
    constraint_window: Dict[str, Dict[str, object]] = {}
    window_start_episode = 1
    for episode in range(episodes):
        policy.set_training_progress(episode, episodes)
        load = loads[episode % len(loads)]
        workload_seed = seed + 10000 + episode
        specs = generate_workload(
            workload_seed,
            load,
            duration,
            max_workflows,
            workload_profile=workload_profile,
            phase="train",
            trace_profile_path=trace_profile_path,
        )
        sim = Simulator(
            specs,
            policy,
            load,
            workload_seed,
            duration,
            max_time,
            quality_weight=quality_weight,
            slack_queue_basis=slack_queue_basis,
            slack_queue_weight=slack_queue_weight,
            network_model=network_model,
            single_bottleneck_capacity=single_bottleneck_capacity,
            action_coupling=action_coupling,
            quality_target=quality_target,
            quality_hard_floor=quality_hard_floor,
            safety_guard=safety_guard,
        )
        episode_summary = sim.run()
        training_window.append(episode_summary)
        constraint_window[load] = episode_summary
        episode_number = episode + 1
        if len(constraint_window) == len(loads):
            policy.update_quality_multiplier(constraint_window, episode_number)
            constraint_window = {}
        if episode_number in checkpoints:
            snapshot = policy.model_snapshot()
            checkpoint_models[episode_number] = snapshot
            total_updates = sum(sum(values.values()) for values in policy.counts.values())
            checkpoint_record = {
                "episode": episode_number,
                "epsilon": policy.epsilon,
                "total_updates": total_updates,
                "states_seen": len(policy.counts),
                "window_start_episode": window_start_episode,
                "window_metrics": summarize_training_window(training_window),
                **serialize_model_snapshot(snapshot),
            }
            policy.training_checkpoints.append(checkpoint_record)
            training_window = []
            window_start_episode = episode_number + 1

    selected_episode = episodes
    if checkpoint_selection == "best_validation":
        for checkpoint_record in policy.training_checkpoints:
            episode_number = int(checkpoint_record["episode"])
            checkpoint_record["validation"] = evaluate_training_checkpoint(
                policy,
                checkpoint_models[episode_number],
                loads,
                duration,
                max_workflows,
                max_time,
                validation_seed,
                checkpoint_eval_runs,
                network_model,
                single_bottleneck_capacity,
                action_coupling,
                quality_target,
                quality_hard_floor,
                safety_guard,
                workload_profile,
                trace_profile_path,
            )
        feasible_records = [
            record
            for record in policy.training_checkpoints
            if bool(record["validation"]["constraint_feasible"])
        ]
        if feasible_records:
            selected_record = max(
                feasible_records,
                key=lambda record: float(record["validation"]["score"]),
            )
        else:
            selected_record = min(
                policy.training_checkpoints,
                key=lambda record: (
                    float(record["validation"]["max_quality_gap"]),
                    float(record["validation"]["max_quality_violation_ratio"]),
                    -float(record["validation"]["score"]),
                ),
            )
        selected_episode = int(selected_record["episode"])

    policy.restore_snapshot(checkpoint_models[selected_episode])
    policy.selected_checkpoint_episode = selected_episode
    policy.training_info = {
        "checkpoint_selection": checkpoint_selection,
        "requested_checkpoint_episodes": requested_checkpoints,
        "saved_checkpoint_episodes": checkpoints,
        "selected_checkpoint_episode": selected_episode,
        "validation_seed": validation_seed if checkpoint_selection == "best_validation" else None,
        "checkpoint_eval_runs": checkpoint_eval_runs if checkpoint_selection == "best_validation" else 0,
        "quality_target": quality_target,
        "quality_hard_floor": quality_hard_floor,
        "safety_guard": "on" if safety_guard else "off",
        "lambda_initial": lambda_initial,
        "lambda_learning_rate": lambda_learning_rate,
        "lambda_max": lambda_max,
        "lambda_updates": policy.lambda_updates,
        "workload_profile": workload_profile,
        "trace_profile_path": (
            trace_profile_path
            if workload_profile in TRACE_WORKLOAD_PROFILES
            else None
        ),
        "selected_checkpoint_constraint_feasible": (
            bool(selected_record["validation"]["constraint_feasible"])
            if checkpoint_selection == "best_validation"
            else None
        ),
    }
    policy.set_evaluation_mode()
    return policy


def aggregate_summaries(summaries: List[Dict[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str], List[Dict[str, object]]] = defaultdict(list)
    for summary in summaries:
        groups[(str(summary["load"]), str(summary["policy"]))].append(summary)

    rows: List[Dict[str, object]] = []
    for (load, policy), items in sorted(groups.items()):
        row = {
            "load": load,
            "policy": policy,
            "controller_variant": items[0].get("controller_variant", ""),
            "state_features": items[0].get("state_features", ""),
            "quality_weight": items[0].get("quality_weight", ""),
            "slack_queue_basis": items[0].get("slack_queue_basis", ""),
            "slack_queue_weight": items[0].get("slack_queue_weight", ""),
            "action_coupling": items[0].get("action_coupling", ""),
            "quality_target": items[0].get("quality_target", ""),
            "quality_hard_floor": items[0].get("quality_hard_floor", ""),
            "safety_guard": items[0].get("safety_guard", ""),
            "workload_profile": items[0].get("workload_profile", "synthetic"),
            "train_seed": items[0].get("train_seed", ""),
            "eval_seed": items[0].get("eval_seed", ""),
            "runs": len(items),
            "completed": sum(int(item["completed"]) for item in items),
        }
        metric_names = [
            "mean_latency",
            "p95_latency",
            "p99_latency",
            "deadline_miss_ratio",
            "wasted_speculative_bytes_per_workflow",
            "useful_speculative_bytes_per_workflow",
            "unused_speculative_bytes_per_workflow",
            "background_bytes_served_per_workflow",
            "avg_quality",
            "quality_target_met_ratio",
            "quality_violation_ratio",
            "guard_override_ratio",
            "quality_constraint_infeasible_ratio",
            "link_utilization",
            "avg_queue_pressure",
        ]
        for metric in metric_names:
            row[metric] = statistics.mean(float(item[metric]) for item in items)
        rows.append(row)
    return rows


def write_csv(path: str, rows: List[Dict[str, object]]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def parse_args() -> argparse.Namespace:
    default_quality_weights_text = ",".join(str(weight) for weight in DEFAULT_QUALITY_WEIGHTS)
    parser = argparse.ArgumentParser(description="Run SpecNet-Agent simulation experiments.")
    parser.add_argument("--output-dir", default="specnet_agent_experiments/results", help="Directory for CSV/JSON outputs.")
    parser.add_argument(
        "--workload-profile",
        choices=WORKLOAD_PROFILES,
        default="synthetic",
        help=(
            "Workload generator used consistently for training, checkpoint "
            "validation, and evaluation."
        ),
    )
    parser.add_argument(
        "--trace-profile-path",
        default="",
        help=(
            "Profile JSON for the selected trace workload. When omitted, "
            "uses $SPECNET_DATA_ROOT/processed/<profile>/profile.json."
        ),
    )
    parser.add_argument("--seed", type=int, default=7, help="Base random seed used when train/eval seeds are not set.")
    parser.add_argument(
        "--train-seed",
        type=int,
        default=None,
        help="Training seed for SpecNet-Agent. Defaults to --seed.",
    )
    parser.add_argument(
        "--train-seeds",
        default="",
        help="Comma-separated training seeds. When set, trains one SpecNet-Agent per quality weight and seed.",
    )
    parser.add_argument(
        "--eval-seed",
        type=int,
        default=None,
        help="Evaluation workload seed base. Defaults to --seed.",
    )
    parser.add_argument("--train-episodes", type=int, default=45, help="Training episodes for SpecNet-Agent bandit.")
    parser.add_argument(
        "--epsilon-schedule",
        choices=("fixed", "linear"),
        default="linear",
        help="Exploration schedule during training. Use fixed with 0.18 to reproduce the legacy trainer.",
    )
    parser.add_argument("--epsilon-start", type=float, default=0.20, help="Initial training exploration rate.")
    parser.add_argument("--epsilon-end", type=float, default=0.03, help="Final exploration rate for linear decay.")
    parser.add_argument(
        "--epsilon-decay-fraction",
        type=float,
        default=0.80,
        help="Fraction of training over which linear epsilon decay is completed.",
    )
    parser.add_argument(
        "--learning-rate-schedule",
        choices=("fixed", "visit_decay"),
        default="visit_decay",
        help="Q-value learning-rate schedule.",
    )
    parser.add_argument("--learning-rate-start", type=float, default=0.25, help="Initial Q-value learning rate.")
    parser.add_argument(
        "--learning-rate-min",
        type=float,
        default=0.03,
        help="Minimum Q-value learning rate for visit-count decay.",
    )
    parser.add_argument(
        "--checkpoint-episodes",
        default="30,45,60,75,90",
        help="Comma-separated 1-based training episodes at which to record model snapshots.",
    )
    parser.add_argument(
        "--checkpoint-selection",
        choices=("last", "best_validation"),
        default="last",
        help="Use the final model or select a recorded checkpoint on held-out validation workloads.",
    )
    parser.add_argument(
        "--validation-seed",
        type=int,
        default=None,
        help="Held-out checkpoint-validation seed. Defaults to eval seed + 500000 and never reuses eval workloads.",
    )
    parser.add_argument(
        "--checkpoint-eval-runs",
        type=int,
        default=5,
        help="Validation runs per load and checkpoint when best_validation selection is enabled.",
    )
    parser.add_argument("--eval-runs", type=int, default=5, help="Evaluation runs per load and policy.")
    parser.add_argument("--duration", type=int, default=2600, help="Workflow arrival duration in simulator time units.")
    parser.add_argument("--max-time", type=int, default=7000, help="Maximum simulator time per run.")
    parser.add_argument("--max-workflows", type=int, default=120, help="Maximum workflows per run.")
    parser.add_argument(
        "--quality-weight",
        type=float,
        default=1.60,
        help="Reward penalty weight for quality loss when training/evaluating SpecNet-Agent.",
    )
    parser.add_argument(
        "--quality-weights",
        default="",
        help=(
            "Comma-separated quality-loss reward weights. When set, the simulator trains "
            f"one SpecNet-Agent per weight, e.g. {default_quality_weights_text}."
        ),
    )
    parser.add_argument(
        "--controller-variants",
        default="full",
        help=(
            "Comma-separated SpecNet controller state variants. Valid values: "
            f"{','.join(CONTROLLER_VARIANT_FEATURES)}."
        ),
    )
    parser.add_argument(
        "--slack-queue-basis",
        choices=SLACK_QUEUE_BASES,
        default=DEFAULT_SLACK_QUEUE_BASIS,
        help=(
            "Queue-work estimator used by deadline Slack. 'total' preserves Slack v2; "
            "'policy_weighted' enables the role-aware Slack v2.1 candidate."
        ),
    )
    parser.add_argument(
        "--slack-queue-weight",
        type=float,
        default=DEFAULT_SLACK_QUEUE_WEIGHT,
        help="Non-negative multiplier applied to the selected Slack queue-work estimate.",
    )
    parser.add_argument(
        "--loads",
        default="light,medium,heavy",
        help="Comma-separated loads to evaluate: light,medium,heavy.",
    )
    parser.add_argument(
        "--network-model",
        choices=NETWORK_MODELS,
        default="single_bottleneck",
        help=(
            "Network capacity model: one shared bottleneck, three fixed service paths, "
            "or service paths with idle-capacity borrowing."
        ),
    )
    parser.add_argument(
        "--single-bottleneck-capacity",
        type=float,
        default=None,
        help="Optional shared-link capacity override for single_bottleneck only (default: 16).",
    )
    parser.add_argument(
        "--action-coupling",
        choices=ACTION_COUPLING_MODES,
        default=DEFAULT_ACTION_COUPLING,
        help=(
            "Use independent background scaling (decoupled), or reproduce the historical "
            "action-to-background coupling (legacy)."
        ),
    )
    parser.add_argument(
        "--quality-target",
        type=float,
        default=DEFAULT_QUALITY_TARGET,
        help="Fixed service-level average quality target; validation does not select it.",
    )
    parser.add_argument(
        "--quality-hard-floor",
        type=float,
        default=DEFAULT_QUALITY_HARD_FLOOR,
        help="Per-workflow quality floor enforced by the optional Safety Guard.",
    )
    parser.add_argument(
        "--safety-guard",
        choices=("off", "on"),
        default="off",
        help="Apply the per-workflow predicted-quality guard before executing an action.",
    )
    parser.add_argument(
        "--lambda-initial",
        type=float,
        default=DEFAULT_LAMBDA_INITIAL,
        help="Initial Lagrange multiplier for average-quality constraint violations.",
    )
    parser.add_argument(
        "--lambda-learning-rate",
        type=float,
        default=DEFAULT_LAMBDA_LEARNING_RATE,
        help="Window-level Lagrange multiplier update rate.",
    )
    parser.add_argument(
        "--lambda-max",
        type=float,
        default=DEFAULT_LAMBDA_MAX,
        help="Upper bound for the quality Lagrange multiplier.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loads = [item.strip() for item in args.loads.split(",") if item.strip()]
    quality_weights = parse_quality_weights(args)
    train_seeds = parse_train_seeds(args)
    controller_variants = parse_controller_variants(args)
    checkpoint_episodes = parse_checkpoint_episodes(args.checkpoint_episodes)
    eval_seed = args.eval_seed if args.eval_seed is not None else args.seed
    validation_seed = args.validation_seed if args.validation_seed is not None else eval_seed + 500000
    multi_weight = len(quality_weights) > 1 or bool(args.quality_weights)
    multi_train_seed = len(train_seeds) > 1 or bool(args.train_seeds)
    multi_controller_variant = len(controller_variants) > 1
    trace_profile_path: Optional[str] = None
    if args.workload_profile in TRACE_WORKLOAD_PROFILES:
        resolver = {
            "trace_driven_v1": resolve_v1_profile_path,
            "trace_driven_v1_1": resolve_v1_profile_path,
            "trace_driven_v2": resolve_v2_profile_path,
            "trace_driven_v3_candidate": resolve_v3_profile_path,
        }[args.workload_profile]
        try:
            trace_profile_path = str(
                resolver(args.trace_profile_path or None)
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not os.path.isfile(trace_profile_path):
            raise SystemExit(
                f"Trace workload profile not found: {trace_profile_path}"
            )
    invalid_loads = [load for load in loads if load not in LOAD_CONFIG]
    if invalid_loads:
        raise SystemExit(f"Invalid loads: {invalid_loads}")
    if args.slack_queue_weight < 0.0:
        raise SystemExit("--slack-queue-weight must be non-negative")
    if not 0.0 <= args.quality_hard_floor <= args.quality_target <= 1.0:
        raise SystemExit(
            "quality constraints must satisfy 0 <= --quality-hard-floor "
            "<= --quality-target <= 1"
        )
    if args.lambda_initial < 0.0 or args.lambda_learning_rate < 0.0 or args.lambda_max < 0.0:
        raise SystemExit("lambda parameters must be non-negative")
    if args.lambda_initial > args.lambda_max:
        raise SystemExit("--lambda-initial must not exceed --lambda-max")
    if args.single_bottleneck_capacity is not None:
        if args.single_bottleneck_capacity <= 0.0:
            raise SystemExit("--single-bottleneck-capacity must be positive")
        if args.network_model != "single_bottleneck":
            raise SystemExit(
                "--single-bottleneck-capacity only applies to --network-model single_bottleneck"
            )

    os.makedirs(args.output_dir, exist_ok=True)
    safety_guard = args.safety_guard == "on"
    trained_policies: Dict[str, Tuple[float, int, str, SpecNetAgentBanditPolicy]] = {}
    trained_agent_rows: List[Dict[str, object]] = []
    lambda_update_rows: List[Dict[str, object]] = []
    for train_seed in train_seeds:
        for quality_weight in quality_weights:
            for controller_variant in controller_variants:
                policy_name = quality_weight_policy_name(
                    quality_weight,
                    multi_weight,
                    train_seed=train_seed,
                    multi_train_seed=multi_train_seed,
                    controller_variant=controller_variant,
                    multi_controller_variant=multi_controller_variant,
                )
                policy = train_specnet_agent(
                    episodes=args.train_episodes,
                    loads=loads,
                    duration=args.duration,
                    max_workflows=args.max_workflows,
                    max_time=args.max_time,
                    seed=train_seed,
                    quality_weight=quality_weight,
                    policy_name=policy_name,
                    controller_variant=controller_variant,
                    epsilon_schedule=args.epsilon_schedule,
                    epsilon_start=args.epsilon_start,
                    epsilon_end=args.epsilon_end,
                    epsilon_decay_fraction=args.epsilon_decay_fraction,
                    learning_rate_schedule=args.learning_rate_schedule,
                    learning_rate_start=args.learning_rate_start,
                    learning_rate_min=args.learning_rate_min,
                    checkpoint_episodes=checkpoint_episodes,
                    checkpoint_selection=args.checkpoint_selection,
                    validation_seed=validation_seed,
                    checkpoint_eval_runs=args.checkpoint_eval_runs,
                    slack_queue_basis=args.slack_queue_basis,
                    slack_queue_weight=args.slack_queue_weight,
                    network_model=args.network_model,
                    single_bottleneck_capacity=args.single_bottleneck_capacity,
                    action_coupling=args.action_coupling,
                    quality_target=args.quality_target,
                    quality_hard_floor=args.quality_hard_floor,
                    safety_guard=safety_guard,
                    lambda_initial=args.lambda_initial,
                    lambda_learning_rate=args.lambda_learning_rate,
                    lambda_max=args.lambda_max,
                    workload_profile=args.workload_profile,
                    trace_profile_path=trace_profile_path,
                )
                state_features = ",".join(CONTROLLER_VARIANT_FEATURES[controller_variant])
                training_info = {
                    "policy": policy_name,
                    "controller_variant": controller_variant,
                    "state_features": state_features,
                    "quality_weight": quality_weight,
                    "slack_queue_basis": args.slack_queue_basis,
                    "slack_queue_weight": args.slack_queue_weight,
                    "action_coupling": args.action_coupling,
                    "quality_target": args.quality_target,
                    "quality_hard_floor": args.quality_hard_floor,
                    "safety_guard": args.safety_guard,
                    "workload_profile": args.workload_profile,
                    "trace_profile_path": trace_profile_path or "",
                    "lambda_initial": args.lambda_initial,
                    "lambda_learning_rate": args.lambda_learning_rate,
                    "lambda_max": args.lambda_max,
                    "quality_lagrange_multiplier": policy.quality_lagrange_multiplier,
                    "selected_checkpoint_constraint_feasible": policy.training_info.get(
                        "selected_checkpoint_constraint_feasible"
                    ),
                    "train_seed": train_seed,
                    "eval_seed": eval_seed,
                    "train_episodes": args.train_episodes,
                    "training_loads": ",".join(loads),
                    "duration": args.duration,
                    "max_workflows": args.max_workflows,
                    "max_time": args.max_time,
                    "epsilon_schedule": args.epsilon_schedule,
                    "epsilon_start": args.epsilon_start,
                    "epsilon_end": args.epsilon_end,
                    "epsilon_decay_fraction": args.epsilon_decay_fraction,
                    "learning_rate_schedule": args.learning_rate_schedule,
                    "learning_rate_start": args.learning_rate_start,
                    "learning_rate_min": args.learning_rate_min,
                    "checkpoint_selection": args.checkpoint_selection,
                    "saved_checkpoint_episodes": ",".join(
                        str(record["episode"]) for record in policy.training_checkpoints
                    ),
                    "selected_checkpoint_episode": policy.selected_checkpoint_episode,
                    "validation_seed": validation_seed if args.checkpoint_selection == "best_validation" else "",
                    "checkpoint_eval_runs": args.checkpoint_eval_runs
                    if args.checkpoint_selection == "best_validation"
                    else 0,
                }
                policy.training_info = {
                    **policy.training_info,
                    **training_info,
                    "state_features": list(policy.state_features),
                }
                trained_policies[policy_name] = (quality_weight, train_seed, controller_variant, policy)
                trained_agent_rows.append(training_info)
                for update in policy.lambda_updates:
                    quality_by_load = update["quality_by_load"]
                    lambda_update_rows.append(
                        {
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "quality_weight": quality_weight,
                            "train_seed": train_seed,
                            "network_model": args.network_model,
                            "action_coupling": args.action_coupling,
                            "safety_guard": args.safety_guard,
                            "workload_profile": args.workload_profile,
                            "quality_target": args.quality_target,
                            "episode": update["episode"],
                            "updated": update["updated"],
                            "missing_loads": ",".join(update["missing_loads"]),
                            "light_avg_quality": quality_by_load.get("light", ""),
                            "medium_avg_quality": quality_by_load.get("medium", ""),
                            "heavy_avg_quality": quality_by_load.get("heavy", ""),
                            "worst_load_quality": update["worst_load_quality"],
                            "quality_gap": update["quality_gap"],
                            "lambda_before": update["lambda_before"],
                            "lambda_after": update["lambda_after"],
                        }
                    )

    policies = [
        "fifo",
        "static_priority",
        "critical_path_only",
        "rule_aggressive",
        "rule_balanced",
        "rule_quality_preserving",
    ] + list(trained_policies.keys())

    summaries: List[Dict[str, object]] = []
    workflow_rows: List[Dict[str, object]] = []
    action_rows: List[Dict[str, object]] = []
    raw_action_rows: List[Dict[str, object]] = []
    path_rows: List[Dict[str, object]] = []
    path_borrowing_rows: List[Dict[str, object]] = []

    for load in loads:
        for run_index in range(args.eval_runs):
            workload_seed = eval_seed + 20000 + 1000 * run_index + 17 * list(LOAD_CONFIG).index(load)
            specs = generate_workload(
                workload_seed,
                load,
                args.duration,
                args.max_workflows,
                workload_profile=args.workload_profile,
                phase="test",
                trace_profile_path=trace_profile_path,
            )
            for policy_name in policies:
                if policy_name in trained_policies:
                    # Reuse learned Q values but reset per-run counters.
                    quality_weight, train_seed, controller_variant, policy = trained_policies[policy_name]
                    state_features = ",".join(policy.state_features)
                    policy.reset_for_run()
                else:
                    quality_weight = ""
                    train_seed = ""
                    controller_variant = ""
                    state_features = ""
                    policy = make_policy(policy_name, seed=eval_seed + run_index)
                sim = Simulator(
                    specs,
                    policy,
                    load,
                    workload_seed,
                    args.duration,
                    args.max_time,
                    quality_weight=float(quality_weight) if quality_weight != "" else args.quality_weight,
                    slack_queue_basis=args.slack_queue_basis,
                    slack_queue_weight=args.slack_queue_weight,
                    network_model=args.network_model,
                    single_bottleneck_capacity=args.single_bottleneck_capacity,
                    action_coupling=args.action_coupling,
                    quality_target=args.quality_target,
                    quality_hard_floor=args.quality_hard_floor,
                    safety_guard=safety_guard,
                )
                summary = sim.run()
                summary["policy"] = policy_name
                summary["controller_variant"] = controller_variant
                summary["state_features"] = state_features
                summary["quality_weight"] = quality_weight
                summary["slack_queue_basis"] = args.slack_queue_basis
                summary["slack_queue_weight"] = args.slack_queue_weight
                summary["action_coupling"] = args.action_coupling
                summary["quality_target"] = args.quality_target
                summary["quality_hard_floor"] = args.quality_hard_floor
                summary["safety_guard"] = args.safety_guard
                summary["workload_profile"] = args.workload_profile
                summary["train_seed"] = train_seed
                summary["eval_seed"] = eval_seed
                summary["run"] = run_index
                summaries.append(
                    {
                        k: v
                        for k, v in summary.items()
                        if k
                        not in (
                            "workflow_records",
                            "action_counts",
                            "raw_action_counts",
                            "path_records",
                            "path_borrowing_records",
                        )
                    }
                )
                for row in summary["workflow_records"]:
                    row_with_context = dict(row)
                    row_with_context.update(
                        {
                            "load": load,
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "state_features": state_features,
                            "quality_weight": quality_weight,
                            "slack_queue_basis": args.slack_queue_basis,
                            "slack_queue_weight": args.slack_queue_weight,
                            "action_coupling": args.action_coupling,
                            "quality_target": args.quality_target,
                            "quality_hard_floor": args.quality_hard_floor,
                            "safety_guard": args.safety_guard,
                            "workload_profile": args.workload_profile,
                            "train_seed": train_seed,
                            "eval_seed": eval_seed,
                            "run": run_index,
                            "seed": workload_seed,
                        }
                    )
                    workflow_rows.append(row_with_context)
                for action, count in summary["action_counts"].items():
                    action_rows.append(
                        {
                            "load": load,
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "state_features": state_features,
                            "quality_weight": quality_weight,
                            "slack_queue_basis": args.slack_queue_basis,
                            "slack_queue_weight": args.slack_queue_weight,
                            "action_coupling": args.action_coupling,
                            "quality_target": args.quality_target,
                            "quality_hard_floor": args.quality_hard_floor,
                            "safety_guard": args.safety_guard,
                            "workload_profile": args.workload_profile,
                            "train_seed": train_seed,
                            "eval_seed": eval_seed,
                            "run": run_index,
                            "action": action,
                            "count": count,
                        }
                    )
                for action, count in summary["raw_action_counts"].items():
                    raw_action_rows.append(
                        {
                            "load": load,
                            "policy": policy_name,
                            "controller_variant": controller_variant,
                            "state_features": state_features,
                            "quality_weight": quality_weight,
                            "slack_queue_basis": args.slack_queue_basis,
                            "slack_queue_weight": args.slack_queue_weight,
                            "action_coupling": args.action_coupling,
                            "quality_target": args.quality_target,
                            "quality_hard_floor": args.quality_hard_floor,
                            "safety_guard": args.safety_guard,
                            "workload_profile": args.workload_profile,
                            "train_seed": train_seed,
                            "eval_seed": eval_seed,
                            "run": run_index,
                            "action": action,
                            "count": count,
                        }
                    )
                for path_record in summary["path_records"]:
                    path_row = {
                        "load": load,
                        "policy": policy_name,
                        "controller_variant": controller_variant,
                        "state_features": state_features,
                        "quality_weight": quality_weight,
                        "slack_queue_basis": args.slack_queue_basis,
                        "slack_queue_weight": args.slack_queue_weight,
                        "train_seed": train_seed,
                        "eval_seed": eval_seed,
                        "run": run_index,
                        "seed": workload_seed,
                    }
                    path_row.update(path_record)
                    path_rows.append(path_row)
                for borrowing_record in summary["path_borrowing_records"]:
                    borrowing_row = {
                        "load": load,
                        "policy": policy_name,
                        "controller_variant": controller_variant,
                        "state_features": state_features,
                        "quality_weight": quality_weight,
                        "slack_queue_basis": args.slack_queue_basis,
                        "slack_queue_weight": args.slack_queue_weight,
                        "train_seed": train_seed,
                        "eval_seed": eval_seed,
                        "run": run_index,
                        "seed": workload_seed,
                    }
                    borrowing_row.update(borrowing_record)
                    path_borrowing_rows.append(borrowing_row)

    aggregate_rows = aggregate_summaries(summaries)
    write_csv(os.path.join(args.output_dir, "summary_by_run.csv"), summaries)
    write_csv(os.path.join(args.output_dir, "summary_aggregate.csv"), aggregate_rows)
    write_csv(os.path.join(args.output_dir, "workflow_results.csv"), workflow_rows)
    write_csv(os.path.join(args.output_dir, "action_counts.csv"), action_rows)
    write_csv(os.path.join(args.output_dir, "raw_action_counts.csv"), raw_action_rows)
    write_csv(os.path.join(args.output_dir, "trained_agents.csv"), trained_agent_rows)
    write_csv(os.path.join(args.output_dir, "lambda_updates.csv"), lambda_update_rows)
    write_csv(os.path.join(args.output_dir, "path_results.csv"), path_rows)
    write_csv(
        os.path.join(args.output_dir, "path_borrowing_results.csv"),
        path_borrowing_rows,
    )
    write_json(
        os.path.join(args.output_dir, "specnet_agent_model.json"),
        {
            "network_model": args.network_model,
            "action_coupling": args.action_coupling,
            "workload": {
                "profile": args.workload_profile,
                "trace_profile_path": trace_profile_path,
                "phase_split": {
                    "training": "train",
                    "checkpoint_validation": "validation",
                    "evaluation": "test",
                },
            },
            "quality_constraints": {
                "quality_target": args.quality_target,
                "quality_hard_floor": args.quality_hard_floor,
                "safety_guard": args.safety_guard,
                "target_selected_by_validation": False,
                "lambda_initial": args.lambda_initial,
                "lambda_learning_rate": args.lambda_learning_rate,
                "lambda_max": args.lambda_max,
                "lambda_update_window": "one_complete_load_cycle",
            },
            **(
                {"borrowing_enabled": True}
                if args.network_model == "service_paths_borrowing"
                else {}
            ),
            "path_capacities": (
                {"shared": args.single_bottleneck_capacity or LOAD_CONFIG[loads[0]]["capacity"]}
                if args.network_model == "single_bottleneck"
                else {path_id: SERVICE_PATH_CAPACITY for path_id in SERVICE_PATH_ORDER}
            ),
            "slack_estimator": SLACK_ESTIMATORS[args.slack_queue_basis],
            "slack_queue_basis": args.slack_queue_basis,
            "slack_queue_weight": args.slack_queue_weight,
            "slack_thresholds": {
                "tight_below": SLACK_TIGHT_THRESHOLD,
                "loose_at_or_above": SLACK_LOOSE_THRESHOLD,
            },
            "controller_variants": controller_variants,
            "quality_weights": quality_weights,
            "train_seeds": train_seeds,
            "eval_seed": eval_seed,
            "train_episodes": args.train_episodes,
            "training_schedule": {
                "epsilon_schedule": args.epsilon_schedule,
                "epsilon_start": args.epsilon_start,
                "epsilon_end": args.epsilon_end,
                "epsilon_decay_fraction": args.epsilon_decay_fraction,
                "learning_rate_schedule": args.learning_rate_schedule,
                "learning_rate_start": args.learning_rate_start,
                "learning_rate_min": args.learning_rate_min,
            },
            "checkpointing": {
                "requested_episodes": checkpoint_episodes,
                "selection": args.checkpoint_selection,
                "validation_seed": validation_seed if args.checkpoint_selection == "best_validation" else None,
                "validation_runs_per_load": args.checkpoint_eval_runs
                if args.checkpoint_selection == "best_validation"
                else 0,
            },
            "loads": loads,
            "policies": {
                policy_name: {
                    "controller_variant": controller_variant,
                    "state_features": list(policy.state_features),
                    "quality_weight": quality_weight,
                    "train_seed": train_seed,
                    "eval_seed": eval_seed,
                    "model": policy.metadata(),
                }
                for policy_name, (quality_weight, train_seed, controller_variant, policy) in trained_policies.items()
            },
        },
    )

    print("Wrote results to:", os.path.abspath(args.output_dir))
    print()
    print("Aggregate summary:")
    for row in aggregate_rows:
        print(
            f"{row['load']:>6} | {row['policy']:<19} "
            f"p99={row['p99_latency']:.2f} "
            f"miss={row['deadline_miss_ratio']:.3f} "
            f"waste={row['wasted_speculative_bytes_per_workflow']:.2f} "
            f"quality={row['avg_quality']:.3f}"
        )


if __name__ == "__main__":
    main()
