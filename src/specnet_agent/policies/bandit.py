"""Contextual-bandit SpecNet controller."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Dict, List, Optional

from ..config import (
    ACTIONS, CONTROLLER_VARIANT_FEATURES, DEFAULT_SLACK_QUEUE_BASIS,
    DEFAULT_SLACK_QUEUE_WEIGHT, SLACK_ESTIMATORS, SLACK_QUEUE_BASES,
    SLACK_LOOSE_THRESHOLD, SLACK_TIGHT_THRESHOLD, StateKey,
)
from ..models import WorkflowRuntime
from .baselines import CriticalPathOnlyPolicy

if TYPE_CHECKING:
    from ..simulator import Simulator


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
        }
        return tuple(state_getters[feature]() for feature in self.state_features)

    def decide_action(self, sim: "Simulator", workflow: WorkflowRuntime) -> str:
        state = self.state_key(sim, workflow)
        if self.train and self.rng.random() < self.epsilon:
            action = self.rng.choice(ACTIONS)
        else:
            q_for_state = self.q_values[state]
            action = max(ACTIONS, key=lambda a: (q_for_state[a], -ACTIONS.index(a)))
        self.action_counter[action] += 1
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

    def set_evaluation_mode(self) -> None:
        if self.train:
            self.final_training_epsilon = self.epsilon
        self.train = False
        self.epsilon = 0.0

    def model_snapshot(self) -> Dict[str, object]:
        return {
            "q_values": {state: dict(values) for state, values in self.q_values.items()},
            "counts": {state: Counter(values) for state, values in self.counts.items()},
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

    def metadata(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "quality_weight": self.quality_weight,
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
