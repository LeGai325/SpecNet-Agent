"""Shadow-score preflight on the four dynamic DAG network fixtures.

This module observes the existing fixture runner through a read-only epoch
hook.  Scores never enter the weighted-capacity scheduler or policy.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Tuple

try:
    from criticality_history import SelectionHistory, SelectionHistoryKey
    from criticality_scoring import (
        CRITICALITY_PROFILES,
        CriticalityInputs,
        config_for_profile,
        derive_all_graph_features,
        score_criticality,
    )
    from dynamic_dag_preflight import (
        FIXTURE_NAMES,
        PREFLIGHT_CAPACITIES,
        NetworkFixtureRunner,
        NetworkPreflightResult,
    )
except ImportError:  # pragma: no cover - package-style imports
    from .criticality_history import SelectionHistory, SelectionHistoryKey
    from .criticality_scoring import (
        CRITICALITY_PROFILES,
        CriticalityInputs,
        config_for_profile,
        derive_all_graph_features,
        score_criticality,
    )
    from .dynamic_dag_preflight import (
        FIXTURE_NAMES,
        PREFLIGHT_CAPACITIES,
        NetworkFixtureRunner,
        NetworkPreflightResult,
    )


@dataclass(frozen=True)
class CriticalityPreflightResult:
    network: NetworkPreflightResult
    score_records: Tuple[Dict[str, object], ...]
    checks: Mapping[str, bool]

    def summary_dict(self) -> Dict[str, object]:
        scores = [float(row["score"]) for row in self.score_records]
        pcrits = [float(row["pcrit"]) for row in self.score_records]
        profile = str(self.score_records[0]["profile"]) if self.score_records else ""
        score_epoch = int(self.score_records[0]["score_epoch"]) if self.score_records else 0
        return {
            **self.network.summary_dict(),
            "profile": profile,
            "score_epoch": score_epoch,
            "score_records": len(self.score_records),
            "min_score": min(scores) if scores else 0.0,
            "max_score": max(scores) if scores else 0.0,
            "mean_pcrit": sum(pcrits) / len(pcrits) if pcrits else 0.0,
            "finite_scores": all(math.isfinite(value) for value in scores),
            "checks": dict(self.checks),
            "checks_passed": all(self.checks.values()),
            "affects_policy": False,
        }


class CriticalityFixtureRunner(NetworkFixtureRunner):
    """NetworkFixtureRunner with deterministic read-only score observations."""

    def __init__(
        self,
        fixture: str,
        capacity: float,
        capacity_label: str,
        *,
        profile: str = "balanced",
        score_epoch: int = 5,
    ) -> None:
        super().__init__(fixture, capacity, capacity_label)
        if score_epoch <= 0:
            raise ValueError("score_epoch must be positive")
        self.criticality_config = config_for_profile(profile)
        self.criticality_history = SelectionHistory()
        self.score_epoch = score_epoch
        self.score_records: List[Dict[str, object]] = []
        self._seen_flow_ids: set[int] = set()

    def dynamic_slack_ratio(self, timestamp: float) -> float:
        required_active = sum(
            flow.remaining
            for flow in self.simulator.active_flows()
            if flow.required
        )
        estimated = required_active / max(1.0, self.capacity)
        estimated += (
            self.simulator.slack_queue_weight
            * self.simulator.slack_queue_work()
            / max(1.0, self.capacity)
        )
        remaining_budget = self.graph.deadline_hint - timestamp
        return (remaining_budget - estimated) / max(1.0, estimated)

    def observe_epoch(self, timestamp: float) -> None:
        active = self.simulator.active_flows()
        active_ids = {flow.flow_id for flow in active}
        force = bool(active_ids - self._seen_flow_ids)
        if not force and int(timestamp) % self.score_epoch != 0:
            return

        snapshot = self.engine.snapshot(0)
        graph_features = derive_all_graph_features(snapshot)
        slack_ratio = self.dynamic_slack_ratio(timestamp)
        for flow in sorted(active, key=lambda value: value.flow_id):
            binding = self.engine.flow_binding(flow.flow_id)
            features = graph_features[binding.step_id]
            history_key = SelectionHistoryKey(
                template=self.fixture,
                request_type=flow.service_type,
                dependency_role=features.dependency_role,
                optional_rank=features.optional_rank,
            )
            history_probability, sample_count = self.criticality_history.probability(
                history_key
            )
            inputs = CriticalityInputs(
                workflow_id=binding.workflow_id,
                step_id=binding.step_id,
                flow_id=binding.flow_id,
                timestamp=timestamp,
                request_type=flow.service_type,
                required=flow.required,
                speculation_level=(0.0 if flow.required else 1.0),
                size=flow.size,
                remaining_size=flow.remaining,
                created_at=float(flow.created_at),
                slack_ratio=slack_ratio,
                historical_selection_rate=history_probability,
                history_sample_count=sample_count,
                template=self.fixture,
                state=snapshot["steps"][binding.step_id]["state"],
                attempt_id=binding.attempt_id,
                graph=features,
            )
            score = score_criticality(inputs, self.criticality_config)
            self.score_records.append(
                {
                    "fixture": self.fixture,
                    "capacity_label": self.capacity_label,
                    "capacity": self.capacity,
                    "profile": self.criticality_config.profile,
                    "score_epoch": self.score_epoch,
                    "workflow_id": binding.workflow_id,
                    "step_id": binding.step_id,
                    "flow_id": binding.flow_id,
                    "attempt_id": binding.attempt_id,
                    "timestamp": timestamp,
                    "request_type": flow.service_type,
                    "required": flow.required,
                    "speculation_level": inputs.speculation_level,
                    "remaining_size": flow.remaining,
                    "slack_ratio": slack_ratio,
                    "dependency_role": features.dependency_role,
                    "optional_rank": features.optional_rank,
                    "direct_hard_children": features.direct_hard_children,
                    "downstream_hard_reachable": features.downstream_hard_reachable,
                    "downstream_total_reachable": features.downstream_total_reachable,
                    "blocks_final": features.blocks_final,
                    "history_sample_count": sample_count,
                    **score.to_dict(),
                }
            )
        self._seen_flow_ids.update(active_ids)

    def run_criticality(self, *, max_time: int = 2000) -> CriticalityPreflightResult:
        network = super().run(max_time=max_time)
        checks = self._semantic_checks()
        return CriticalityPreflightResult(
            network=network,
            score_records=tuple(self.score_records),
            checks=checks,
        )

    def _semantic_checks(self) -> Dict[str, bool]:
        rows = self.score_records
        scored_steps = {str(row["step_id"]) for row in rows}
        active_after_prune = [
            row
            for row in rows
            if row["step_id"] in {"branch:b", "branch:c"}
            and float(row["timestamp"]) > float(self.network_prune_time())
        ]
        checks = {
            "all_scores_finite": all(
                math.isfinite(float(row["score"])) and math.isfinite(float(row["pcrit"]))
                for row in rows
            ),
            "affects_policy_false": all(row["affects_policy"] is False for row in rows),
        }
        if self.fixture == "rag_supplemental":
            checks["dynamic_retrieval_scored"] = "retrieval:1" in scored_steps
        elif self.fixture == "coding_retry":
            checks["retry_attempt_scored"] = any(
                row["step_id"] == "tool" and int(row["attempt_id"]) == 1
                for row in rows
            )
        elif self.fixture == "judge_pruning":
            checks["all_candidates_scored"] = {
                "branch:a",
                "branch:b",
                "branch:c",
            }.issubset(scored_steps)
            checks["pruned_candidates_stop_scoring"] = not active_after_prune
            first_candidate_scores = {
                step_id: float(
                    next(row["score"] for row in rows if row["step_id"] == step_id)
                )
                for step_id in ("branch:a", "branch:b", "branch:c")
                if any(row["step_id"] == step_id for row in rows)
            }
            checks["selected_candidate_ranked_first"] = (
                len(first_candidate_scores) == 3
                and first_candidate_scores["branch:a"]
                > max(
                    first_candidate_scores["branch:b"],
                    first_candidate_scores["branch:c"],
                )
            )
        elif self.fixture == "parallel_join":
            for step_id in ("retrieval", "tool", "llm_branch"):
                candidate_rows = [row for row in rows if row["step_id"] == step_id]
                checks[f"{step_id}_blocks_join"] = any(
                    int(row["direct_hard_children"]) >= 1 for row in candidate_rows
                )
        return checks

    def network_prune_time(self) -> float:
        cancelled = [
            float(event["timestamp"])
            for event in self.simulator.workflow_hint_collector.event_dicts()
            if event["event"] == "cancelled" and event.get("reason") == "judge_pruned"
        ]
        return min(cancelled) if cancelled else math.inf


def run_criticality_preflight(
    capacities: Dict[str, float] | None = None,
    *,
    profile: str = "balanced",
    score_epoch: int = 5,
) -> Tuple[CriticalityPreflightResult, ...]:
    selected = capacities or PREFLIGHT_CAPACITIES
    return tuple(
        CriticalityFixtureRunner(
            fixture,
            capacity,
            label,
            profile=profile,
            score_epoch=score_epoch,
        ).run_criticality()
        for fixture in FIXTURE_NAMES
        for label, capacity in selected.items()
    )


def write_criticality_preflight_outputs(
    results: Tuple[CriticalityPreflightResult, ...],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = [result.summary_dict() for result in results]
    (output_dir / "criticality_preflight_summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "criticality_preflight_scores.jsonl").open(
        "w",
        encoding="utf-8",
    ) as handle:
        for result in results:
            for row in result.score_records:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    (output_dir / "criticality_preflight_metadata.json").write_text(
        json.dumps(
            {
                "mode": "shadow",
                "affects_policy": False,
                "profiles": sorted({row["profile"] for row in summaries}),
                "score_epochs": sorted({row["score_epoch"] for row in summaries}),
                "config": config_for_profile(
                    str(results[0].score_records[0]["profile"])
                ).to_dict()
                if results and results[0].score_records
                else {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--profile", choices=CRITICALITY_PROFILES, default="balanced")
    parser.add_argument("--score-epoch", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = run_criticality_preflight(
        profile=args.profile,
        score_epoch=args.score_epoch,
    )
    if args.output_dir:
        write_criticality_preflight_outputs(results, Path(args.output_dir))
    print(json.dumps([result.summary_dict() for result in results], indent=2))


if __name__ == "__main__":
    main()
