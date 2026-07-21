"""Public compatibility surface for the SpecNet-Agent research framework."""

from .config import *  # noqa: F401,F403
from .math_utils import lognormal_size, percentile
from .models import BranchSpec, Flow, WorkflowRuntime, WorkflowSpec
from .outputs import aggregate_summaries, write_csv, write_json
from .policies import (
    CriticalPathOnlyPolicy,
    FIFOPolicy,
    Policy,
    RuleBasedFeedbackPolicy,
    SpecNetAgentBanditPolicy,
    StaticPriorityPolicy,
    make_policy,
)
from .simulator import Simulator
from .training import (
    evaluate_training_checkpoint,
    parse_checkpoint_episodes,
    parse_controller_variants,
    parse_int_list,
    parse_quality_weights,
    parse_train_seeds,
    policy_from_snapshot,
    quality_weight_policy_name,
    serialize_model_snapshot,
    summarize_training_window,
    train_specnet_agent,
)
from .workload import generate_workload

__version__ = "0.1.0"

__all__ = [
    "ACTIONS", "DEFAULT_QUALITY_WEIGHTS", "CONTROLLER_VARIANT_FEATURES", "StateKey",
    "SLACK_QUEUE_BASES", "DEFAULT_SLACK_QUEUE_BASIS", "DEFAULT_SLACK_QUEUE_WEIGHT",
    "SLACK_ESTIMATORS", "SLACK_TIGHT_THRESHOLD", "SLACK_LOOSE_THRESHOLD",
    "ACTION_CONFIG", "TEMPLATES", "SERVICE_SIZE", "LOAD_CONFIG",
    "BranchSpec", "WorkflowSpec", "Flow", "WorkflowRuntime", "Policy", "FIFOPolicy",
    "StaticPriorityPolicy", "CriticalPathOnlyPolicy", "RuleBasedFeedbackPolicy",
    "SpecNetAgentBanditPolicy", "Simulator", "generate_workload", "train_specnet_agent",
    "evaluate_training_checkpoint", "make_policy", "aggregate_summaries", "write_csv",
    "write_json", "lognormal_size", "percentile", "quality_weight_policy_name",
    "parse_quality_weights", "parse_int_list", "parse_train_seeds",
    "parse_controller_variants", "parse_checkpoint_episodes", "serialize_model_snapshot",
    "summarize_training_window", "policy_from_snapshot",
]
