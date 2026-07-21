"""Immutable experiment constants and configuration vocabulary."""
from __future__ import annotations

from typing import Tuple



ACTIONS = ("full", "moderate", "conservative", "critical_only", "recovery")
DEFAULT_QUALITY_WEIGHTS = (0.5, 1.0, 1.6, 2.5, 4.0, 6.0)
CONTROLLER_VARIANT_FEATURES = {
    "full": ("congestion", "slack", "spec_pressure"),
    "congestion_only": ("congestion",),
    "no_slack": ("congestion", "spec_pressure"),
    "no_spec_pressure": ("congestion", "slack"),
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

