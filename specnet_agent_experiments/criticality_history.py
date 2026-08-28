"""Leakage-safe historical selection statistics for flow criticality scoring.

The history store is deliberately independent from the simulator and DAG
runtime.  A workflow contributes observations only through
``record_finalized_workflow`` so its own future Judge outcome cannot affect an
earlier score from the same workflow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple


@dataclass(frozen=True, order=True)
class SelectionHistoryKey:
    """Content-free context used to aggregate Judge selection outcomes."""

    template: str
    request_type: str
    dependency_role: str
    optional_rank: int = -1

    def to_dict(self) -> Dict[str, object]:
        return {
            "template": self.template,
            "request_type": self.request_type,
            "dependency_role": self.dependency_role,
            "optional_rank": self.optional_rank,
        }


@dataclass(frozen=True)
class SelectionObservation:
    key: SelectionHistoryKey
    selected: bool


@dataclass
class _SelectionCounts:
    selected: int = 0
    observed: int = 0


class SelectionHistory:
    """Beta-smoothed selection history updated only by finalized workflows."""

    def __init__(self, *, alpha: float = 1.0, beta: float = 1.0) -> None:
        self.alpha = self._positive_finite(alpha, "alpha")
        self.beta = self._positive_finite(beta, "beta")
        self._counts: Dict[SelectionHistoryKey, _SelectionCounts] = {}
        self._finalized_workflow_ids: set[str] = set()

    @staticmethod
    def _positive_finite(value: float, label: str) -> float:
        result = float(value)
        if not math.isfinite(result) or result <= 0.0:
            raise ValueError(f"{label} must be finite and positive")
        return result

    def probability(self, key: SelectionHistoryKey) -> Tuple[float, int]:
        """Return smoothed probability and the number of completed samples."""

        counts = self._counts.get(key, _SelectionCounts())
        probability = (counts.selected + self.alpha) / (
            counts.observed + self.alpha + self.beta
        )
        return probability, counts.observed

    def record_finalized_workflow(
        self,
        workflow_id: object,
        observations: Iterable[SelectionObservation],
    ) -> None:
        """Atomically add outcomes from one already-finalized workflow."""

        workflow_key = str(workflow_id)
        if not workflow_key:
            raise ValueError("workflow_id cannot be empty")
        if workflow_key in self._finalized_workflow_ids:
            raise ValueError(f"workflow history already recorded: {workflow_key}")

        materialized = tuple(observations)
        for observation in materialized:
            if not isinstance(observation, SelectionObservation):
                raise TypeError("observations must contain SelectionObservation values")

        for observation in materialized:
            counts = self._counts.setdefault(observation.key, _SelectionCounts())
            counts.observed += 1
            counts.selected += int(observation.selected)
        self._finalized_workflow_ids.add(workflow_key)

    def snapshot(self) -> Dict[str, object]:
        rows = []
        for key in sorted(self._counts):
            counts = self._counts[key]
            probability, _ = self.probability(key)
            rows.append(
                {
                    **key.to_dict(),
                    "selected": counts.selected,
                    "observed": counts.observed,
                    "probability": probability,
                }
            )
        return {
            "smoothing": {"alpha": self.alpha, "beta": self.beta},
            "finalized_workflows": len(self._finalized_workflow_ids),
            "rows": rows,
        }

    @property
    def finalized_workflow_ids(self) -> frozenset[str]:
        return frozenset(self._finalized_workflow_ids)


def history_key_from_mapping(values: Mapping[str, object]) -> SelectionHistoryKey:
    """Build a normalized key from a plain mapping or output record."""

    return SelectionHistoryKey(
        template=str(values.get("template", "unknown")),
        request_type=str(values.get("request_type", "unknown")),
        dependency_role=str(values.get("dependency_role", "unknown")),
        optional_rank=int(values.get("optional_rank", -1)),
    )
