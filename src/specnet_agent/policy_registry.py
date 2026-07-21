"""Shared policy identifiers, display metadata, and legacy aliases."""

POLICY_ALIASES = {
    "rule_balanced": "rule_based_feedback",
}

POLICY_ORDER = [
    "fifo",
    "static_priority",
    "critical_path_only",
    "rule_based_feedback",
    "specnet_agent",
]

POLICY_LABEL = {
    "fifo": "FIFO",
    "static_priority": "Static priority",
    "critical_path_only": "Critical-path only",
    "rule_based_feedback": "Rule feedback",
    "specnet_agent": "SpecNet-Agent",
}

POLICY_SHORT = {
    "fifo": "FIFO",
    "static_priority": "Static",
    "critical_path_only": "CritPath",
    "rule_based_feedback": "Rule",
    "specnet_agent": "SpecNet",
}

POLICY_COLOR = {
    "fifo": "#64748b",
    "static_priority": "#7c3aed",
    "critical_path_only": "#2563eb",
    "rule_based_feedback": "#f59e0b",
    "specnet_agent": "#059669",
}

POLICY_MARKER = {
    "fifo": "o",
    "static_priority": "s",
    "critical_path_only": "^",
    "rule_based_feedback": "D",
    "specnet_agent": "P",
}


def canonical_policy_name(name: str) -> str:
    return POLICY_ALIASES.get(name, name)
