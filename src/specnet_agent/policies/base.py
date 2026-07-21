"""Policy base classes."""
from __future__ import annotations

import random
from collections import Counter
from typing import TYPE_CHECKING, Dict

from ..models import Flow, WorkflowRuntime

if TYPE_CHECKING:
    from ..simulator import Simulator

class Policy:
    name = "base"

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed)
        self.action_counter: Counter[str] = Counter()

    def reset_for_run(self) -> None:
        self.action_counter.clear()

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
