"""Fixed and rule-based policy baselines."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..models import Flow, WorkflowRuntime
from .base import Policy

if TYPE_CHECKING:
    from ..simulator import Simulator

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
        self.action_counter[action] += 1
        return action
