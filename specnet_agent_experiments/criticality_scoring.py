"""Interpretable Pcrit and paper-form Score computation.

The paper defines the Score components and says Pcrit uses DAG position,
deadline Slack, and historical selection.  It does not publish the concrete
combination weights or normalizations.  This module therefore keeps those
implementation choices explicit, bounded, deterministic, and configurable.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, replace
from typing import Dict, Mapping, Sequence, Tuple


SCORER_VERSION = "pcrit_score_shadow_v1"
CRITICALITY_PROFILES = (
    "balanced",
    "structure_heavy",
    "urgency_heavy",
    "no_cost_urgency",
)
TERMINAL_EXCLUDED_STATES = {"cancelled", "failed"}
FINAL_REQUEST_TYPES = {"judge", "final", "finalizer", "summarizer"}
RANK_PATTERN = re.compile(r"(?:^|:)(\d+)$")


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _finite(value: object, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        constraint = f" >= {minimum}" if minimum is not None else ""
        raise ValueError(f"{label} must be a finite number{constraint}")
    return result


@dataclass(frozen=True)
class CriticalityConfig:
    """All unpublished implementation choices for the shadow scorer."""

    profile: str = "balanced"
    structure_weight: float = 0.45
    urgency_weight: float = 0.35
    history_weight: float = 0.20
    urgency_scale: float = 1.0
    size_scale: float = 16.0
    epsilon: float = 0.25
    age_weight: float = 0.15
    age_scale: float = 25.0
    age_cap: float = 2.0
    spec_penalty_weight: float = 0.40
    cost_urgency_weight: float = 1.0
    cost_direct_block_weight: float = 0.35
    cost_hard_downstream_weight: float = 0.20

    def __post_init__(self) -> None:
        if self.profile not in CRITICALITY_PROFILES:
            raise ValueError(f"unknown criticality profile: {self.profile}")
        weights = (
            _finite(self.structure_weight, "structure_weight", minimum=0.0),
            _finite(self.urgency_weight, "urgency_weight", minimum=0.0),
            _finite(self.history_weight, "history_weight", minimum=0.0),
        )
        if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("Pcrit weights must sum to 1")
        for label in ("urgency_scale", "size_scale", "epsilon", "age_scale"):
            if _finite(getattr(self, label), label) <= 0.0:
                raise ValueError(f"{label} must be positive")
        for label in (
            "age_weight",
            "age_cap",
            "spec_penalty_weight",
            "cost_urgency_weight",
            "cost_direct_block_weight",
            "cost_hard_downstream_weight",
        ):
            _finite(getattr(self, label), label, minimum=0.0)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def config_for_profile(profile: str) -> CriticalityConfig:
    """Return one preregistered Pcrit mixture for sensitivity analysis."""

    base = CriticalityConfig()
    if profile == "balanced":
        return base
    if profile == "structure_heavy":
        return replace(
            base,
            profile=profile,
            structure_weight=0.65,
            urgency_weight=0.20,
            history_weight=0.15,
        )
    if profile == "urgency_heavy":
        return replace(
            base,
            profile=profile,
            structure_weight=0.30,
            urgency_weight=0.55,
            history_weight=0.15,
        )
    if profile == "no_cost_urgency":
        return replace(
            base,
            profile=profile,
            cost_urgency_weight=0.0,
        )
    raise ValueError(f"unknown criticality profile: {profile}")


@dataclass(frozen=True)
class GraphCriticalityFeatures:
    graph_known: bool = True
    is_final_step: bool = False
    blocks_final: bool = False
    direct_hard_children: int = 0
    downstream_hard_reachable: int = 0
    downstream_total_reachable: int = 0
    dependency_role: str = "root"
    optional_rank: int = -1


@dataclass(frozen=True)
class CriticalityInputs:
    workflow_id: str
    step_id: str
    flow_id: str
    timestamp: float
    request_type: str
    required: bool
    speculation_level: float
    size: float
    remaining_size: float
    created_at: float
    slack_ratio: float
    historical_selection_rate: float
    history_sample_count: int = 0
    template: str = "unknown"
    state: str = "running"
    attempt_id: int = 0
    graph: GraphCriticalityFeatures = GraphCriticalityFeatures()

    def __post_init__(self) -> None:
        for label in ("workflow_id", "step_id", "flow_id", "request_type"):
            if not str(getattr(self, label)):
                raise ValueError(f"{label} cannot be empty")
        _finite(self.timestamp, "timestamp", minimum=0.0)
        _finite(self.created_at, "created_at", minimum=0.0)
        if self.created_at > self.timestamp:
            raise ValueError("created_at cannot be after timestamp")
        _finite(self.speculation_level, "speculation_level", minimum=0.0)
        _finite(self.size, "size", minimum=0.0)
        _finite(self.remaining_size, "remaining_size", minimum=0.0)
        _finite(self.slack_ratio, "slack_ratio")
        history = _finite(
            self.historical_selection_rate,
            "historical_selection_rate",
            minimum=0.0,
        )
        if history > 1.0:
            raise ValueError("historical_selection_rate must be <= 1")
        if self.history_sample_count < 0:
            raise ValueError("history_sample_count must be non-negative")
        if self.attempt_id < 0:
            raise ValueError("attempt_id must be non-negative")


@dataclass(frozen=True)
class CriticalityResult:
    pcrit: float
    structural_prior: float
    urgency: float
    history_probability: float
    cost_delay: float
    fanout_factor: float
    size_cost: float
    age_boost: float
    spec_penalty: float
    score: float
    age: float
    score_reason: str
    scorer_version: str = SCORER_VERSION
    affects_policy: bool = False

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def urgency_from_slack(slack_ratio: float, scale: float = 1.0) -> float:
    """Map continuous work-aware Slack to a bounded deadline-risk signal."""

    slack = _finite(slack_ratio, "slack_ratio")
    scale_value = _finite(scale, "urgency_scale")
    if scale_value <= 0.0:
        raise ValueError("urgency_scale must be positive")
    exponent = max(-60.0, min(60.0, slack / scale_value))
    return 1.0 / (1.0 + math.exp(exponent))


def structural_prior(inputs: CriticalityInputs) -> float:
    """Estimate DAG-position prior without treating service type as destiny."""

    graph = inputs.graph
    if inputs.request_type == "background":
        return 0.05
    if graph.graph_known:
        if graph.is_final_step:
            base = 0.90
        elif graph.blocks_final:
            base = 0.68
        elif inputs.required:
            base = 0.52
        else:
            base = 0.18
        base += 0.05 * min(3, graph.direct_hard_children)
        base += 0.07 * min(3.0, math.log1p(graph.downstream_hard_reachable))
        return _clip(base, 0.05, 0.95)

    fallback = {
        "judge": 0.85,
        "planner": 0.65,
        "llm": 0.60,
        "tool": 0.40,
        "retrieval": 0.35,
        "storage": 0.30,
        "background": 0.05,
    }
    prior = fallback.get(inputs.request_type, 0.35)
    return _clip(max(prior, 0.50 if inputs.required else prior), 0.05, 0.90)


def score_criticality(
    inputs: CriticalityInputs,
    config: CriticalityConfig | None = None,
) -> CriticalityResult:
    """Compute Pcrit and the paper's complete flow Score in shadow mode."""

    selected_config = config or CriticalityConfig()
    structure = structural_prior(inputs)
    urgency = urgency_from_slack(inputs.slack_ratio, selected_config.urgency_scale)
    history = 1.0 if inputs.required else inputs.historical_selection_rate
    pcrit = _clip(
        selected_config.structure_weight * structure
        + selected_config.urgency_weight * urgency
        + selected_config.history_weight * history
    )

    direct_block = float(
        inputs.graph.is_final_step or inputs.graph.direct_hard_children > 0
    )
    cost_delay = (
        1.0
        + selected_config.cost_urgency_weight * urgency
        + selected_config.cost_direct_block_weight * direct_block
        + selected_config.cost_hard_downstream_weight
        * math.log1p(inputs.graph.downstream_hard_reachable)
    )
    fanout_factor = 1.0 + math.log1p(inputs.graph.downstream_total_reachable)
    size_cost = inputs.remaining_size / selected_config.size_scale
    age = max(0.0, inputs.timestamp - inputs.created_at)
    age_boost = selected_config.age_weight * min(
        age / selected_config.age_scale,
        selected_config.age_cap,
    )
    spec_penalty = 0.0
    if not inputs.required:
        spec_penalty = (
            selected_config.spec_penalty_weight
            * _clip(inputs.speculation_level)
            * (1.0 - inputs.historical_selection_rate)
        )

    # This is the paper-defined composition.  Each normalized component above
    # remains visible in the output so the unpublished choices are auditable.
    score = (
        (pcrit * cost_delay / (selected_config.epsilon + size_cost))
        * fanout_factor
        + age_boost
        - spec_penalty
    )
    if not math.isfinite(score):
        raise ValueError("criticality score is not finite")

    reasons = []
    if inputs.graph.is_final_step:
        reasons.append("final_step")
    elif inputs.graph.blocks_final:
        reasons.append("blocks_final")
    elif inputs.required:
        reasons.append("required")
    else:
        reasons.append("optional")
    if inputs.slack_ratio < 0.0:
        reasons.append("tight_deadline")
    if inputs.graph.downstream_total_reachable:
        reasons.append(f"downstream={inputs.graph.downstream_total_reachable}")
    if spec_penalty:
        reasons.append("spec_penalty")

    return CriticalityResult(
        pcrit=pcrit,
        structural_prior=structure,
        urgency=urgency,
        history_probability=history,
        cost_delay=cost_delay,
        fanout_factor=fanout_factor,
        size_cost=size_cost,
        age_boost=age_boost,
        spec_penalty=spec_penalty,
        score=score,
        age=age,
        score_reason="|".join(reasons),
    )


def optional_rank(step_id: object) -> int:
    match = RANK_PATTERN.search(str(step_id))
    return int(match.group(1)) if match else -1


def derive_graph_features(
    snapshot: Mapping[str, object],
    step_id: object,
) -> GraphCriticalityFeatures:
    """Derive structural features from DynamicDAG or replay snapshots."""

    step_key = str(step_id)
    return derive_all_graph_features(snapshot).get(
        step_key,
        GraphCriticalityFeatures(
            graph_known=False,
            optional_rank=optional_rank(step_key),
        ),
    )


def derive_all_graph_features(
    snapshot: Mapping[str, object],
) -> Dict[str, GraphCriticalityFeatures]:
    """Derive every step's features while constructing graph indexes once."""

    raw_steps = snapshot.get("steps", {})
    if not isinstance(raw_steps, Mapping):
        return {}
    steps: Dict[str, Mapping[str, object]] = {
        str(key): value
        for key, value in raw_steps.items()
        if isinstance(value, Mapping)
    }
    children: Dict[str, set[str]] = {key: set() for key in steps}
    hard_children: Dict[str, set[str]] = {key: set() for key in steps}
    for child_id, child in steps.items():
        parents = tuple(str(parent) for parent in child.get("parents", ()))
        dependency_kinds = child.get("dependency_kinds", {})
        kinds = dependency_kinds if isinstance(dependency_kinds, Mapping) else {}
        for parent in parents:
            if parent not in steps:
                continue
            children[parent].add(child_id)
            if str(kinds.get(parent, "hard_dependency")) == "hard_dependency":
                hard_children[parent].add(child_id)

    def available(node: str) -> bool:
        return str(steps[node].get("state", "created")) not in TERMINAL_EXCLUDED_STATES

    def reachable(step_key: str, adjacency: Mapping[str, set[str]]) -> set[str]:
        found: set[str] = set()
        frontier = list(adjacency.get(step_key, ()))
        while frontier:
            node = frontier.pop()
            if node in found or not available(node):
                continue
            found.add(node)
            frontier.extend(adjacency.get(node, ()))
        return found

    final_nodes = {
        key
        for key, value in steps.items()
        if str(value.get("request_type", "unknown")) in FINAL_REQUEST_TYPES
        or key in FINAL_REQUEST_TYPES
    }

    features: Dict[str, GraphCriticalityFeatures] = {}
    for step_key, step in steps.items():
        hard_reachable = reachable(step_key, hard_children)
        total_reachable = reachable(step_key, children)
        request_type = str(step.get("request_type", "unknown"))
        is_final = request_type in FINAL_REQUEST_TYPES or step_key in FINAL_REQUEST_TYPES

        outgoing_roles = []
        for child in children.get(step_key, ()):
            kinds = steps[child].get("dependency_kinds", {})
            if isinstance(kinds, Mapping):
                outgoing_roles.append(str(kinds.get(step_key, "hard_dependency")))
        if "hard_dependency" in outgoing_roles:
            dependency_role = "hard_dependency"
        elif "optional_evidence" in outgoing_roles:
            dependency_role = "optional_evidence"
        elif "control_trigger" in outgoing_roles:
            dependency_role = "control_trigger"
        else:
            incoming = step.get("dependency_kinds", {})
            incoming_roles = (
                set(str(value) for value in incoming.values())
                if isinstance(incoming, Mapping)
                else set()
            )
            if "hard_dependency" in incoming_roles:
                dependency_role = "hard_dependency"
            elif "optional_evidence" in incoming_roles:
                dependency_role = "optional_evidence"
            elif "control_trigger" in incoming_roles:
                dependency_role = "control_trigger"
            else:
                dependency_role = "root"

        features[step_key] = GraphCriticalityFeatures(
            graph_known=True,
            is_final_step=is_final,
            blocks_final=is_final or bool(hard_reachable & final_nodes),
            direct_hard_children=sum(
                1 for child in hard_children[step_key] if available(child)
            ),
            downstream_hard_reachable=len(hard_reachable),
            downstream_total_reachable=len(total_reachable),
            dependency_role=dependency_role,
            optional_rank=optional_rank(step_key),
        )
    return features
