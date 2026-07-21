"""Policy implementations and compatibility factory."""
from __future__ import annotations

from typing import Optional

from .base import FIFOPolicy, Policy
from .baselines import CriticalPathOnlyPolicy, RuleBasedFeedbackPolicy, StaticPriorityPolicy
from .bandit import SpecNetAgentBanditPolicy


def make_policy(
    name: str,
    seed: int,
    trained_bandit: Optional[SpecNetAgentBanditPolicy] = None,
) -> Policy:
    """Create a policy while preserving the historical names and behavior."""
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
        return RuleBasedFeedbackPolicy(
            seed,
            profile="quality_preserving",
            name="rule_quality_preserving",
        )
    if name == "specnet_agent":
        if trained_bandit is None:
            return SpecNetAgentBanditPolicy(seed=seed, train=False, epsilon=0.0)
        trained_bandit.set_evaluation_mode()
        return trained_bandit
    raise ValueError(f"unknown policy: {name}")



__all__ = [
    "Policy", "FIFOPolicy", "StaticPriorityPolicy", "CriticalPathOnlyPolicy",
    "RuleBasedFeedbackPolicy", "SpecNetAgentBanditPolicy", "make_policy",
]
