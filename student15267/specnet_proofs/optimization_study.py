#!/usr/bin/env python3
"""Controlled optimization study for the SpecNet-Agent proof harness.

This module keeps the original proof protocol intact. It compares several
training and deployment refinements on matched held-out workload seeds and
writes a separate, auditable evidence directory.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from specnet_proofs import proof_harness as h


ROOT = Path(__file__).resolve().parent
PROTOCOL_VERSION = "2026-07-29.optimization-v4.3"
STATE = Tuple[str, str, str]
SCENARIO = Tuple[str, float, float, float]
QUALITY_FLOOR = 0.95
QUALITY_RELATIVE_MARGIN = -0.01
P99_RELATIVE_NONINFERIORITY_MARGIN = 0.10
DEADLINE_MISS_NONINFERIORITY_MARGIN = 0.005
BACKGROUND_SERVICE_FLOOR = 0.20
WORST_LOAD_QUALITY_FLOOR = 0.95
QUALITY_FEASIBLE_FRACTION_FLOOR = 0.75
TEST_SEED_BASE = 92000


def _factor_levels(matrix: Sequence[SCENARIO]) -> List[List[object]]:
    return [list(dict.fromkeys(row[index] for row in matrix)) for index in range(4)]


def _greedy_balanced_subset(
    matrix: Sequence[SCENARIO],
    count: int,
    seed: int,
    excluded: Sequence[SCENARIO] = (),
) -> List[SCENARIO]:
    """Choose a deterministic marginally balanced subset for non-3x3x3x3 grids."""
    excluded_set = set(excluded)
    pool = [row for row in matrix if row not in excluded_set]
    if count > len(pool):
        raise ValueError("balanced subset exceeds available scenarios")
    levels = _factor_levels(matrix)
    rng = random.Random(seed)
    tie_break = {row: rng.random() for row in pool}
    selected: List[SCENARIO] = []

    def imbalance(candidate: SCENARIO) -> Tuple[float, float]:
        trial = selected + [candidate]
        size = len(trial)
        score = 0.0
        for index, values in enumerate(levels):
            target = size / len(values)
            counts = Counter(row[index] for row in trial)
            score += sum((counts[value] - target) ** 2 for value in values)
        # A light pairwise term avoids a marginally balanced but aliased split.
        for left, right in itertools.combinations(range(4), 2):
            target = size / (len(levels[left]) * len(levels[right]))
            counts = Counter((row[left], row[right]) for row in trial)
            score += 0.2 * sum(
                (counts[(a, b)] - target) ** 2
                for a in levels[left]
                for b in levels[right]
            )
        return (round(score, 12), tie_break[candidate])

    while len(selected) < count:
        candidate = min(pool, key=imbalance)
        selected.append(candidate)
        pool.remove(candidate)
    return selected


def balanced_scenario_split(
    matrix: Sequence[SCENARIO],
    validation_count: int,
    evaluation_count: int,
    seed: int = 15267,
) -> Tuple[List[SCENARIO], List[SCENARIO]]:
    """Build disjoint validation/test splits without dropping factor levels.

    A complete 3^4 matrix is partitioned into nine disjoint OA(9, 4, 3, 2)
    batches. Other grids use a deterministic marginal/pairwise balancing
    heuristic, which covers the smoke matrix's mixed two- and three-level
    factors.
    """
    matrix = list(matrix)
    levels = _factor_levels(matrix)
    full_three_level = (
        [len(values) for values in levels] == [3, 3, 3, 3]
        and len(matrix) == 81
        and set(matrix) == set(itertools.product(*levels))
    )
    if full_three_level and validation_count % 9 == 0 and evaluation_count % 9 == 0:
        batches: List[List[SCENARIO]] = []
        for shift_optional, shift_capacity in itertools.product(range(3), repeat=2):
            batch = []
            for load_index, deadline_index in itertools.product(range(3), repeat=2):
                optional_index = (load_index + deadline_index + shift_optional) % 3
                capacity_index = (load_index + 2 * deadline_index + shift_capacity) % 3
                batch.append(
                    (
                        levels[0][load_index],
                        levels[1][deadline_index],
                        levels[2][optional_index],
                        levels[3][capacity_index],
                    )
                )
            batches.append(batch)
        random.Random(seed).shuffle(batches)
        validation_batches = validation_count // 9
        evaluation_batches = evaluation_count // 9
        required = validation_batches + evaluation_batches
        if required > len(batches):
            raise ValueError("orthogonal split exceeds the 3^4 scenario matrix")
        validation = list(itertools.chain.from_iterable(batches[:validation_batches]))
        evaluation = list(
            itertools.chain.from_iterable(batches[validation_batches:required])
        )
        return validation, evaluation

    validation = _greedy_balanced_subset(matrix, validation_count, seed)
    evaluation = _greedy_balanced_subset(
        matrix, evaluation_count, seed + 1, excluded=validation
    )
    return validation, evaluation


def protocol_scenario_split(
    mode: str,
    matrix: Sequence[SCENARIO],
    validation_count: int,
    evaluation_count: int,
) -> Tuple[List[SCENARIO], List[SCENARIO], List[SCENARIO]]:
    """Use an unseen third smoke block after v4.2 evaluation was observed."""
    validation, prior_evaluation = balanced_scenario_split(
        matrix, validation_count, evaluation_count
    )
    if mode != "smoke":
        return validation, prior_evaluation, []
    evaluation = _greedy_balanced_subset(
        matrix,
        evaluation_count,
        seed=15269,
        excluded=validation + prior_evaluation,
    )
    return validation, evaluation, prior_evaluation


class FairProofSimulator(h.ProofSimulator):
    """Reward background starvation instead of charging for useful service."""

    background_floor = 0.20

    def workflow_reward(self, workflow) -> float:
        if workflow.complete_time is None:
            return -10.0
        latency = workflow.complete_time - workflow.spec.arrival_time
        normalized_latency = latency / max(1.0, workflow.spec.deadline)
        deadline_miss = 1.0 if latency > workflow.spec.deadline else 0.0
        wasted_norm = workflow.wasted_speculative_bytes / max(
            1.0, sum(branch.size for branch in workflow.spec.branches)
        )
        quality_loss = 1.0 - workflow.quality
        background_ratio = workflow.background_bytes_served / max(
            1.0, sum(workflow.spec.background_sizes)
        )
        background_shortfall = max(0.0, self.background_floor - background_ratio)
        return -(
            normalized_latency
            + 3.00 * deadline_miss
            + 0.80 * wasted_norm
            + 1.60 * quality_loss
            + 0.75 * background_shortfall / self.background_floor
        )


class AlignedRewardSimulator(FairProofSimulator):
    """Train against tail, quality-floor, and background deployment goals."""

    quality_floor = 0.95
    tail_threshold = 0.60

    def workflow_reward(self, workflow) -> float:
        if workflow.complete_time is None:
            return -10.0
        latency = workflow.complete_time - workflow.spec.arrival_time
        normalized_latency = latency / max(1.0, workflow.spec.deadline)
        tail_excess = max(0.0, normalized_latency - self.tail_threshold)
        deadline_miss = 1.0 if latency > workflow.spec.deadline else 0.0
        wasted_norm = workflow.wasted_speculative_bytes / max(
            1.0, sum(branch.size for branch in workflow.spec.branches)
        )
        quality_loss = 1.0 - workflow.quality
        quality_shortfall = max(0.0, self.quality_floor - workflow.quality)
        background_ratio = workflow.background_bytes_served / max(
            1.0, sum(workflow.spec.background_sizes)
        )
        background_shortfall = max(0.0, self.background_floor - background_ratio)
        return -(
            normalized_latency
            + 2.00 * tail_excess
            + 3.00 * deadline_miss
            + 0.80 * wasted_norm
            + 0.25 * quality_loss
            + 10.00 * quality_shortfall
            + 0.50 * background_shortfall / self.background_floor
        )


class StrictAlignedRewardSimulator(FairProofSimulator):
    """Pre-registered miss-sensitive reward for the fresh v4.3 holdout."""

    quality_floor = QUALITY_FLOOR
    tail_threshold = 0.45

    def workflow_reward(self, workflow) -> float:
        if workflow.complete_time is None:
            return -10.0
        latency = workflow.complete_time - workflow.spec.arrival_time
        normalized_latency = latency / max(1.0, workflow.spec.deadline)
        tail_excess = max(0.0, normalized_latency - self.tail_threshold)
        deadline_miss = 1.0 if latency > workflow.spec.deadline else 0.0
        wasted_norm = workflow.wasted_speculative_bytes / max(
            1.0, sum(branch.size for branch in workflow.spec.branches)
        )
        quality_loss = 1.0 - workflow.quality
        quality_shortfall = max(0.0, self.quality_floor - workflow.quality)
        background_ratio = workflow.background_bytes_served / max(
            1.0, sum(workflow.spec.background_sizes)
        )
        background_shortfall = max(0.0, self.background_floor - background_ratio)
        return -(
            normalized_latency
            + 3.00 * tail_excess
            + 8.00 * deadline_miss
            + 0.80 * wasted_norm
            + 0.25 * quality_loss
            + 12.00 * quality_shortfall
            + 1.00 * background_shortfall / self.background_floor
        )


def train_controller(
    seed: int,
    episodes: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    matrix: Sequence[Tuple[str, float, float, float]],
    scheduled: bool,
    reward_mode: str,
) -> h.AuditedBandit:
    policy = h.AuditedBandit(seed=seed, train=True, epsilon=0.18, learning_rate=0.25)
    simulator_class = {
        "base": h.ProofSimulator,
        "fair": FairProofSimulator,
        "aligned": AlignedRewardSimulator,
        "strict_aligned": StrictAlignedRewardSimulator,
    }[reward_mode]
    for episode in range(episodes):
        if scheduled:
            progress = episode / max(1, episodes - 1)
            policy.epsilon = 0.18 + progress * (0.03 - 0.18)
            policy.learning_rate = 0.25 + progress * (0.05 - 0.25)
        scenario = matrix[episode % len(matrix)]
        load, deadline_scale, optional_scale, capacity_scale = scenario
        workload_seed = seed + 10000 + episode
        specs = h.scaled_workload(
            workload_seed,
            load,
            duration,
            max_workflows,
            deadline_scale,
            optional_scale,
        )
        sim = simulator_class(
            specs,
            policy,
            load,
            workload_seed,
            duration,
            max_time,
            capacity_scale=capacity_scale,
        )
        sim.run()
    policy.set_evaluation_mode()
    return policy


class MedianEnsemble(h.AuditedBandit):
    """One auditable Q table formed from medians of independently trained tables."""

    name = "scheduled_ensemble"

    def __init__(self, members: Sequence[h.AuditedBandit]) -> None:
        super().__init__(seed=0, train=False, epsilon=0.0)
        self.member_count = len(members)
        for state in h.ALL_STATES:
            for action in h.up.ACTIONS:
                observed = [
                    member.q_values[state][action]
                    for member in members
                    if member.counts[state][action] > 0
                ]
                self.q_values[state][action] = statistics.median(observed) if observed else 0.0
                self.counts[state][action] = sum(
                    member.counts[state][action] for member in members
                )
        self.set_evaluation_mode()


def rule_action_from_state(params: Mapping[str, float], state: STATE) -> str:
    congestion = {"low": 0.0, "medium": 0.5, "high": 1.0}[state[0]]
    slack = {"loose": 0.0, "normal": 0.5, "tight": 1.0}[state[1]]
    pressure = {"low_spec": 0.0, "mid_spec": 0.5, "high_spec": 1.0}[state[2]]
    risk = (
        params["wc"] * congestion
        + params["ws"] * slack
        + params["wp"] * pressure
        + params["wcs"] * congestion * slack
        + params["wcp"] * congestion * pressure
    )
    thresholds = [params[f"t{index}"] for index in range(4)]
    if risk >= thresholds[3]:
        return "critical_only"
    if risk >= thresholds[2]:
        return "conservative"
    if risk >= thresholds[1]:
        return "moderate"
    if risk >= thresholds[0]:
        return "recovery"
    return "full"


def rule_action_count(params: Mapping[str, float]) -> int:
    return len({rule_action_from_state(params, state) for state in h.ALL_STATES})


def bounded_recovery_candidates() -> List[Dict[str, float]]:
    """Monotone two-action rules that cannot fall below moderate."""
    profiles = (
        (1.0, 1.0, 1.0, 0.0, 0.0),
        (1.3, 0.8, 1.2, 0.5, 0.7),
        (0.8, 1.4, 0.9, 0.8, 0.4),
    )
    candidates = []
    for wc, ws, wp, wcs, wcp in profiles:
        max_risk = wc + ws + wp + wcs + wcp
        for fraction in (0.25, 0.40, 0.55, 0.70, 0.85):
            candidates.append(
                {
                    "wc": wc,
                    "ws": ws,
                    "wp": wp,
                    "wcs": wcs,
                    "wcp": wcp,
                    "t0": 0.0,
                    "t1": max_risk * fraction,
                    "t2": max_risk + 1.0,
                    "t3": max_risk + 2.0,
                }
            )
    return candidates


def choose_robust_candidate(
    scored: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    """Choose a genuinely adaptive rule and expose failed feasibility honestly."""
    adaptive = [row for row in scored if bool(row["adaptive_candidate"])]
    if not adaptive:
        raise ValueError("candidate search produced no adaptive robust rule")
    return min(
        adaptive,
        key=lambda row: (
            not bool(row["meets_robust_feasibility"]),
            float(row["robust_cost"]),
            int(row["candidate_id"]),
        ),
    )


class ConfidenceHybrid(h.up.CriticalPathOnlyPolicy):
    """Use learned Q only when the table has support and a clear winner."""

    name = "confidence_hybrid"

    def __init__(
        self,
        table: MedianEnsemble,
        fallback_params: Mapping[str, float],
        min_visits: int = 30,
        min_margin: float = 0.05,
    ) -> None:
        super().__init__(seed=0)
        self.table = table
        self.fallback_params = dict(fallback_params)
        self.min_visits = min_visits
        self.min_margin = min_margin
        self.fallback_counter: Counter[str] = Counter()

    def reset_for_run(self) -> None:
        super().reset_for_run()
        self.fallback_counter.clear()

    def action_for_state(self, state: STATE) -> Tuple[str, str]:
        visits = sum(self.table.counts[state].values()) / max(1, self.table.member_count)
        learned, _, margin = h.best_action(self.table, state)
        if visits >= self.min_visits and margin >= self.min_margin:
            return learned, "learned"
        reason = "low_support" if visits < self.min_visits else "low_margin"
        return rule_action_from_state(self.fallback_params, state), reason

    def decide_action(self, sim, workflow) -> str:
        state = sim.observable_state(workflow)
        action, reason = self.action_for_state(state)
        self.action_counter[action] += 1
        self.fallback_counter[reason] += 1
        workflow.decision_state = state
        return action


def run_controller(
    policy,
    scenario: Tuple[str, float, float, float],
    seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Dict[str, float]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = h.scaled_workload(
        seed, load, duration, max_workflows, deadline_scale, optional_scale
    )
    sim = h.ProofSimulator(
        specs,
        policy,
        load,
        seed,
        duration,
        max_time,
        capacity_scale=capacity_scale,
    )
    summary = sim.run()
    background_ratios = [
        workflow.background_bytes_served
        / max(1.0, sum(workflow.spec.background_sizes))
        for workflow in sim.completed_workflows
    ]
    decisions = sum(policy.action_counter.values())
    fallback_decisions = (
        sum(count for reason, count in policy.fallback_counter.items() if reason != "learned")
        if isinstance(policy, ConfidenceHybrid)
        else 0
    )
    return {
        "p99_latency": float(summary["p99_latency"]),
        "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
        "waste": float(summary["wasted_speculative_bytes_per_workflow"]),
        "quality": float(summary["avg_quality"]),
        "background_service_ratio": statistics.mean(background_ratios)
        if background_ratios
        else 0.0,
        "fallback_fraction": fallback_decisions / max(1, decisions),
    }


def select_fallback_rule(
    mode: str,
    validation_matrix: Sequence[Tuple[str, float, float, float]],
    baseline_policies: Sequence[h.AuditedBandit],
    duration: int,
    max_workflows: int,
    max_time: int,
) -> Tuple[Dict[str, float], Dict[str, object]]:
    random_candidate_count = 64 if mode == "smoke" else 192
    structured_candidates = bounded_recovery_candidates()
    candidates = h.candidate_rules(random_candidate_count, seed=1907)
    candidates.extend(structured_candidates)
    baseline_quality = []
    for policy_index, policy in enumerate(baseline_policies):
        for scenario_index, scenario in enumerate(validation_matrix):
            metrics = run_controller(
                policy,
                scenario,
                80000 + 1000 * policy_index + scenario_index,
                duration,
                max_workflows,
                max_time,
            )
            baseline_quality.append(metrics["quality"])
    quality_floor = max(0.95, statistics.mean(baseline_quality) - 0.01)

    candidate_metrics: Dict[int, List[Dict[str, object]]] = defaultdict(list)
    for candidate_id, params in enumerate(candidates):
        for scenario_index, scenario in enumerate(validation_matrix):
            metrics = run_controller(
                h.TunedRiskRulePolicy(params, seed=81000 + scenario_index),
                scenario,
                81000 + scenario_index,
                duration,
                max_workflows,
                max_time,
            )
            candidate_metrics[candidate_id].append(
                {**metrics, "load": scenario[0], "scenario": scenario_index}
            )
    p99_scale = statistics.median(
        statistics.mean(row["p99_latency"] for row in rows)
        for rows in candidate_metrics.values()
    )
    waste_scale = statistics.median(
        statistics.mean(row["waste"] for row in rows)
        for rows in candidate_metrics.values()
    )
    scored = []
    robust_scored: List[Dict[str, object]] = []
    for candidate_id, rows in candidate_metrics.items():
        distinct_actions = rule_action_count(candidates[candidate_id])
        metrics = {
            key: statistics.mean(row[key] for row in rows)
            for key in ("p99_latency", "deadline_miss_ratio", "waste", "quality")
        }
        feasible = metrics["quality"] >= quality_floor
        cost = (
            metrics["p99_latency"] / max(p99_scale, 1e-9)
            + 3.0 * metrics["deadline_miss_ratio"]
            + metrics["waste"] / max(waste_scale, 1e-9)
            + 100.0 * max(0.0, quality_floor - metrics["quality"])
        )
        scored.append((not feasible, cost, candidate_id, metrics))
        scenario_costs = []
        for row in rows:
            quality_shortfall = max(0.0, quality_floor - float(row["quality"]))
            background_shortfall = max(
                0.0,
                FairProofSimulator.background_floor
                - float(row["background_service_ratio"]),
            )
            scenario_costs.append(
                float(row["p99_latency"]) / max(p99_scale, 1e-9)
                + 3.0 * float(row["deadline_miss_ratio"])
                + float(row["waste"]) / max(waste_scale, 1e-9)
                + 100.0 * quality_shortfall
                + background_shortfall / FairProofSimulator.background_floor
            )
        load_quality = {
            load: statistics.mean(
                float(row["quality"]) for row in rows if row["load"] == load
            )
            for load in sorted({str(row["load"]) for row in rows})
        }
        worst_load_quality = min(load_quality.values())
        feasible_fraction = statistics.mean(
            float(row["quality"]) >= quality_floor for row in rows
        )
        mean_cost = statistics.mean(scenario_costs)
        p90_cost = h.up.percentile(scenario_costs, 0.90)
        robust_cost = (
            mean_cost
            + 0.35 * max(0.0, p90_cost - mean_cost)
            + 50.0 * max(0.0, quality_floor - worst_load_quality)
            + 2.0 * max(0.0, 0.75 - feasible_fraction)
        )
        robust_feasible = (
            metrics["quality"] >= quality_floor
            and worst_load_quality >= quality_floor - 0.01
        )
        adaptive_candidate = distinct_actions >= 2
        robust_scored.append(
            {
                "candidate_id": candidate_id,
                "robust_cost": robust_cost,
                "metrics": metrics,
                "worst_load_quality": worst_load_quality,
                "quality_feasible_fraction": feasible_fraction,
                "mean_scenario_cost": mean_cost,
                "p90_scenario_cost": p90_cost,
                "distinct_actions": distinct_actions,
                "adaptive_candidate": adaptive_candidate,
                "meets_robust_feasibility": robust_feasible,
            }
        )
    selected_infeasible, cost, selected_id, selected_metrics = min(scored)
    robust_selected = choose_robust_candidate(robust_scored)
    robust_id = int(robust_selected["candidate_id"])
    robust_feasible = bool(robust_selected["meets_robust_feasibility"])
    metadata = {
        "candidate_count": len(candidates),
        "random_candidate_count": random_candidate_count,
        "structured_candidate_count": len(structured_candidates),
        "validation_scenarios": len(validation_matrix),
        "quality_floor": quality_floor,
        "p99_scale": p99_scale,
        "waste_scale": waste_scale,
        "selected_candidate_id": selected_id,
        "selected_candidate_family": (
            "structured_bounded_recovery"
            if selected_id >= random_candidate_count
            else "random_or_anchor"
        ),
        "selected_validation_cost": cost,
        "selected_validation_metrics": selected_metrics,
        "distinct_actions": rule_action_count(candidates[selected_id]),
        "meets_quality_feasibility": not selected_infeasible,
        "params": candidates[selected_id],
        "robust": {
            "selected_candidate_id": robust_id,
            "selected_candidate_family": (
                "structured_bounded_recovery"
                if robust_id >= random_candidate_count
                else "random_or_anchor"
            ),
            "selection_status": (
                "adaptive_feasible" if robust_feasible else "adaptive_but_infeasible"
            ),
            "adaptive_candidate": bool(robust_selected["adaptive_candidate"]),
            "meets_robust_feasibility": robust_feasible,
            "selected_validation_cost": robust_selected["robust_cost"],
            "mean_scenario_cost": robust_selected["mean_scenario_cost"],
            "p90_scenario_cost": robust_selected["p90_scenario_cost"],
            "distinct_actions": robust_selected["distinct_actions"],
            "worst_load_quality": robust_selected["worst_load_quality"],
            "quality_feasible_fraction": robust_selected[
                "quality_feasible_fraction"
            ],
            "selected_validation_metrics": robust_selected["metrics"],
            "params": candidates[robust_id],
        },
    }
    return dict(candidates[selected_id]), metadata


def state_action(policy, state: STATE) -> Tuple[str, int, float]:
    if isinstance(policy, ConfidenceHybrid):
        action, _ = policy.action_for_state(state)
        visits = round(
            sum(policy.table.counts[state].values()) / max(1, policy.table.member_count)
        )
        _, _, margin = h.best_action(policy.table, state)
        return action, visits, margin
    if isinstance(policy, h.FixedActionPolicy):
        return policy.action, 30, math.inf
    if isinstance(policy, h.TunedRiskRulePolicy):
        return rule_action_from_state(policy.params, state), 30, math.inf
    action, _, margin = h.best_action(policy, state)
    visits = sum(policy.counts[state].values())
    if isinstance(policy, MedianEnsemble):
        visits = round(visits / max(1, policy.member_count))
    return action, visits, margin


def stability_rows(variants: Mapping[str, Sequence[object]]) -> List[Dict[str, object]]:
    rows = []
    for variant, policies in variants.items():
        for state in h.ALL_STATES:
            actions = []
            visits = []
            margins = []
            for policy in policies:
                action, count, margin = state_action(policy, state)
                if count:
                    actions.append(action)
                    visits.append(count)
                    margins.append(margin)
            modal, modal_count = Counter(actions).most_common(1)[0]
            rows.append(
                {
                    "variant": variant,
                    "state": str(state),
                    "replicates": len(policies),
                    "supported_replicates": len(actions),
                    "min_visits": min(visits),
                    "median_visits": statistics.median(visits),
                    "modal_action": modal,
                    "action_agreement": modal_count / len(actions),
                    "mean_q_margin": statistics.mean(margins),
                    "uncertain": int(modal_count / len(actions) < 0.8),
                }
            )
    return rows


def sanity_summary_rows(variants: Mapping[str, Sequence[object]]) -> List[Dict[str, object]]:
    checks = [
        (("low", "loose", "high_spec"), ("high", "loose", "high_spec"), "nonincreasing"),
        (("high", "loose", "high_spec"), ("high", "tight", "high_spec"), "nonincreasing"),
        (("high", "loose", "low_spec"), ("high", "loose", "high_spec"), "nonincreasing"),
        (("low", "loose", "low_spec"), ("low", "loose", "high_spec"), "at_least_moderate"),
    ]
    rows = []
    for variant, policies in variants.items():
        supported = 0
        violations = 0
        for policy in policies:
            for before, after, expectation in checks:
                before_action, before_visits, _ = state_action(policy, before)
                after_action, after_visits, _ = state_action(policy, after)
                if min(before_visits, after_visits) < 30:
                    continue
                supported += 1
                if expectation == "nonincreasing":
                    violation = h.ACTION_STRENGTH[after_action] > h.ACTION_STRENGTH[before_action]
                else:
                    violation = h.ACTION_STRENGTH[after_action] < h.ACTION_STRENGTH["moderate"]
                violations += int(violation)
        rows.append(
            {
                "variant": variant,
                "supported_checks": supported,
                "violations": violations,
                "violation_rate": violations / max(1, supported),
            }
        )
    return rows


def add_costs(
    rows: List[Dict[str, object]], quality_floor: float, p99_scale: float, waste_scale: float
) -> None:
    for row in rows:
        quality_shortfall = max(0.0, quality_floor - float(row["quality"]))
        background_shortfall = max(
            0.0, FairProofSimulator.background_floor - float(row["background_service_ratio"])
        )
        legacy_cost = (
            float(row["p99_latency"]) / max(p99_scale, 1e-9)
            + 3.0 * float(row["deadline_miss_ratio"])
            + float(row["waste"]) / max(waste_scale, 1e-9)
            + 100.0 * quality_shortfall
        )
        row["legacy_cost"] = legacy_cost
        row["fair_cost"] = legacy_cost + background_shortfall / FairProofSimulator.background_floor
        row["quality_feasible"] = int(float(row["quality"]) >= quality_floor)


def performance_summary_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["variant"])].append(row)
    result = []
    fields = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "background_service_ratio",
        "legacy_cost",
        "fair_cost",
        "fallback_fraction",
    )
    for variant, items in sorted(grouped.items()):
        result.append(
            {
                "variant": variant,
                "evaluation_units": len(items),
                **{
                    f"mean_{field}": statistics.mean(float(row[field]) for row in items)
                    for field in fields
                },
                "quality_feasible_fraction": statistics.mean(
                    int(row["quality_feasible"]) for row in items
                ),
            }
        )
    return result


def performance_by_load_rows(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["variant"]), str(row["load"]))].append(row)
    output = []
    for (variant, load), items in sorted(grouped.items()):
        output.append(
            {
                "variant": variant,
                "load": load,
                "evaluation_units": len(items),
                "mean_p99_latency": statistics.mean(
                    float(row["p99_latency"]) for row in items
                ),
                "mean_deadline_miss_ratio": statistics.mean(
                    float(row["deadline_miss_ratio"]) for row in items
                ),
                "mean_waste": statistics.mean(float(row["waste"]) for row in items),
                "mean_quality": statistics.mean(float(row["quality"]) for row in items),
                "mean_background_service_ratio": statistics.mean(
                    float(row["background_service_ratio"]) for row in items
                ),
                "mean_fair_cost": statistics.mean(
                    float(row["fair_cost"]) for row in items
                ),
                "quality_feasible_fraction": statistics.mean(
                    int(row["quality_feasible"]) for row in items
                ),
            }
        )
    return output


def replicate_cluster_bootstrap_ci(
    values: Sequence[Tuple[int, int, float]],
    seed: int,
    draws: int = 4000,
) -> Tuple[float, float]:
    """Resample training replicates as whole cross-scenario policy clusters."""
    if not values:
        return (math.nan, math.nan)
    by_replicate: Dict[int, Dict[int, float]] = defaultdict(dict)
    for replicate, scenario, value in values:
        by_replicate[replicate][scenario] = value
    replicates = sorted(by_replicate)
    scenarios = sorted({scenario for _, scenario, _ in values})
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sampled = [rng.choice(replicates) for _ in replicates]
        scenario_means = []
        for scenario in scenarios:
            present = [by_replicate[replicate][scenario] for replicate in sampled]
            if present:
                scenario_means.append(statistics.mean(present))
        estimates.append(statistics.mean(scenario_means))
    return (h.up.percentile(estimates, 0.025), h.up.percentile(estimates, 0.975))


def paired_comparison_rows(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    indexed = {
        (str(row["variant"]), int(row["replicate"]), int(row["scenario"])): row
        for row in rows
    }
    variants = sorted({str(row["variant"]) for row in rows if row["variant"] != "baseline"})
    metrics = (
        "p99_latency",
        "deadline_miss_ratio",
        "waste",
        "quality",
        "background_service_ratio",
        "legacy_cost",
        "fair_cost",
    )
    result = []
    for variant in variants:
        keys = sorted(
            (replicate, scenario)
            for name, replicate, scenario in indexed
            if name == variant and ("baseline", replicate, scenario) in indexed
        )
        for metric_index, metric in enumerate(metrics):
            clustered_values = [
                (
                    replicate,
                    scenario,
                    float(indexed[(variant, replicate, scenario)][metric])
                    - float(indexed[("baseline", replicate, scenario)][metric]),
                )
                for replicate, scenario in keys
            ]
            low, high = replicate_cluster_bootstrap_ci(
                clustered_values,
                seed=119267 + 100 * variants.index(variant) + metric_index,
            )
            values = [(scenario, value) for _, scenario, value in clustered_values]
            result.append(
                {
                    "comparison": f"{variant}_minus_baseline",
                    "variant": variant,
                    "metric": metric,
                    "paired_units": len(values),
                    "scenario_strata": len({scenario for scenario, _ in values}),
                    "mean_delta": h.stratified_mean(values),
                    "ci95_low": low,
                    "ci95_high": high,
                    "standardized_effect_dz": h.stratified_effect_dz(values),
                }
            )
    return result


def deployment_gate_rows(
    summaries: Sequence[Mapping[str, object]],
    comparisons: Sequence[Mapping[str, object]],
    by_load: Sequence[Mapping[str, object]],
    fallback_rule: Mapping[str, object],
) -> List[Dict[str, object]]:
    summary_index = {str(row["variant"]): row for row in summaries}
    comparison_index = {
        (str(row["variant"]), str(row["metric"])): row for row in comparisons
    }
    load_quality: Dict[str, List[float]] = defaultdict(list)
    for row in by_load:
        load_quality[str(row["variant"])].append(float(row["mean_quality"]))
    baseline_p99 = float(summary_index["baseline"]["mean_p99_latency"])
    standard_rule_feasible = bool(fallback_rule["meets_quality_feasibility"])
    robust_rule_feasible = bool(
        fallback_rule["robust"]["meets_robust_feasibility"]
    )
    rows = []
    for variant, summary in sorted(summary_index.items()):
        if variant == "baseline":
            continue
        selection_eligible = True
        if variant in {
            "validation_rule",
            "confidence_hybrid_m002",
            "confidence_hybrid_m005",
        }:
            selection_eligible = standard_rule_feasible
        elif variant == "robust_validation_rule":
            selection_eligible = robust_rule_feasible
        gates = {
            "selection_eligible": selection_eligible,
            "fair_cost_improved": float(
                comparison_index[(variant, "fair_cost")]["ci95_high"]
            )
            < 0.0,
            "quality_relative_noninferior": float(
                comparison_index[(variant, "quality")]["ci95_low"]
            )
            >= QUALITY_RELATIVE_MARGIN,
            "mean_quality_feasible": float(summary["mean_quality"]) >= QUALITY_FLOOR,
            "p99_noninferior": float(
                comparison_index[(variant, "p99_latency")]["ci95_high"]
            )
            <= P99_RELATIVE_NONINFERIORITY_MARGIN * baseline_p99,
            "deadline_miss_noninferior": float(
                comparison_index[(variant, "deadline_miss_ratio")]["ci95_high"]
            )
            <= DEADLINE_MISS_NONINFERIORITY_MARGIN,
            "background_floor_met": float(summary["mean_background_service_ratio"])
            >= BACKGROUND_SERVICE_FLOOR,
            "worst_load_quality_met": min(load_quality[variant])
            >= WORST_LOAD_QUALITY_FLOOR,
            "quality_feasible_fraction_met": float(
                summary["quality_feasible_fraction"]
            )
            >= QUALITY_FEASIBLE_FRACTION_FLOOR,
        }
        failed = [name for name, passed in gates.items() if not passed]
        rows.append(
            {
                "variant": variant,
                **{name: int(passed) for name, passed in gates.items()},
                "passed_gate_count": sum(gates.values()),
                "total_gate_count": len(gates),
                "all_deployment_gates_passed": int(not failed),
                "failed_gates": ",".join(failed),
                "worst_load_quality": min(load_quality[variant]),
            }
        )
    return rows


def build_report(
    out: Path,
    mode: str,
    manifest: Mapping[str, object],
    summaries: Sequence[Mapping[str, object]],
    comparisons: Sequence[Mapping[str, object]],
    stability: Sequence[Mapping[str, object]],
    sanity: Sequence[Mapping[str, object]],
    gates: Sequence[Mapping[str, object]],
) -> None:
    by_variant = {str(row["variant"]): row for row in summaries}
    stability_by_variant: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in stability:
        stability_by_variant[str(row["variant"])].append(row)
    comparison_index = {
        (str(row["variant"]), str(row["metric"])): row for row in comparisons
    }
    lines = [
        "# SpecNet-Agent 控制器优化实验报告",
        "",
        f"- 模式：`{mode}`",
        f"- 协议：`{PROTOCOL_VERSION}`",
        f"- 上游 SHA-256：`{manifest['upstream_sha256']}`",
        f"- 训练重复数：{manifest['replicates']}；集成成员数：{manifest['ensemble_size']}",
        f"- 验证场景数：{manifest['validation_scenarios']}；测试场景数：{manifest['evaluation_scenarios']}",
        "",
        "## 一句话结论",
        "",
    ]
    fallback_rule = manifest["fallback_rule"]
    quality_floor = float(fallback_rule["quality_floor"])
    gate_index = {str(row["variant"]): row for row in gates}
    deployable = [
        row for row in summaries
        if str(row["variant"]) != "baseline"
        and int(gate_index[str(row["variant"])]["all_deployment_gates_passed"])
    ]
    deployable.sort(key=lambda row: float(row["mean_fair_cost"]))
    closest = max(
        gates,
        key=lambda row: (
            int(row["passed_gate_count"]),
            -float(by_variant[str(row["variant"])]["mean_fair_cost"]),
        ),
    )
    if deployable:
        best = deployable[0]
        lines.append(
            f"{best['variant']} 通过全部 {gate_index[str(best['variant'])]['total_gate_count']} 项"
            "分层部署门槛；smoke 仅允许其进入 full 复核，不构成部署结论。"
        )
    else:
        lines.append(
            f"没有候选通过全部分层部署门槛。最接近的 {closest['variant']} "
            f"通过 {closest['passed_gate_count']}/{closest['total_gate_count']} 项，"
            f"失败项为 `{closest['failed_gates']}`；保留 baseline，不宣称已完成部署优化。"
        )
    lines += [
        "",
        "## 聚合结果",
        "",
        "| Variant | p99 | Miss | Waste | Quality | Background | Legacy cost | Fair cost | Quality feasible |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['variant']} | {float(row['mean_p99_latency']):.3f} | "
            f"{float(row['mean_deadline_miss_ratio']):.4f} | {float(row['mean_waste']):.3f} | "
            f"{float(row['mean_quality']):.4f} | {float(row['mean_background_service_ratio']):.4f} | "
            f"{float(row['mean_legacy_cost']):.4f} | {float(row['mean_fair_cost']):.4f} | "
            f"{float(row['quality_feasible_fraction']):.3f} |"
        )
    lines += [
        "",
        "## 分层部署门槛",
        "",
        "| Variant | Passed | Eligible | Fair cost | Quality rel. | Mean Q | p99 | Miss | Background | Worst-load Q | Q fraction |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in gates:
        lines.append(
            f"| {row['variant']} | {row['passed_gate_count']}/{row['total_gate_count']} | "
            f"{row['selection_eligible']} | {row['fair_cost_improved']} | "
            f"{row['quality_relative_noninferior']} | {row['mean_quality_feasible']} | "
            f"{row['p99_noninferior']} | {row['deadline_miss_noninferior']} | "
            f"{row['background_floor_met']} | {row['worst_load_quality_met']} | "
            f"{row['quality_feasible_fraction_met']} |"
        )
    lines += ["", "## 相对基线的配对差异", ""]
    for variant in sorted(name for name in by_variant if name != "baseline"):
        lines.append(f"### {variant}")
        lines.append("")
        for metric in (
            "p99_latency",
            "deadline_miss_ratio",
            "waste",
            "quality",
            "background_service_ratio",
            "legacy_cost",
            "fair_cost",
        ):
            row = comparison_index[(variant, metric)]
            lines.append(
                f"- {metric}: delta={float(row['mean_delta']):.4f}, "
                f"95% CI [{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}]"
            )
        lines.append("")
    lines += [
        "## 策略稳定性与单调性",
        "",
        "| Variant | Mean agreement | Uncertain states | Sanity violation rate |",
        "|---|---:|---:|---:|",
    ]
    sanity_index = {str(row["variant"]): row for row in sanity}
    for variant in sorted(stability_by_variant):
        items = stability_by_variant[variant]
        lines.append(
            f"| {variant} | {statistics.mean(float(row['action_agreement']) for row in items):.3f} | "
            f"{sum(int(row['uncertain']) for row in items)}/{len(items)} | "
            f"{float(sanity_index[variant]['violation_rate']):.3f} |"
        )
    lines += [
        "",
        "## 优化含义",
        "",
        "- `scheduled` 只改变训练日程：探索率从 0.18 降到 0.03，学习率从 0.25 降到 0.05。",
        "- `fair_scheduled` 进一步把 background 项从“服务越多惩罚越大”改成“低于 20% 服务率才惩罚”。",
        "- `aligned_scheduled` 将训练奖励对齐到 tail excess、quality>=0.95 和 background floor，检验 reward mismatch。",
        "- `strict_aligned_scheduled` 在新 holdout 打开前冻结：提高 deadline miss、tail excess、quality shortfall 和 background shortfall 惩罚。",
        "- `scheduled_ensemble` 对三个独立 Q 表逐状态、逐动作取中位数，换取训练成本来降低偶然性。",
        "- `confidence_hybrid_m002/m005` 在 N<30 或 Q margin 低于 0.02/0.05 时回退到固定 quality>=0.95 的 validation 冻结规则。",
        "- `robust_validation_rule` 只用均衡 validation 场景选择，并惩罚最差负载质量、背景服务不足和尾部场景 cost。",
        f"- 本次 robust 选择状态为 `{fallback_rule['robust']['selection_status']}`；"
        "如验证约束未通过，该变体只保留为诊断结果，不进入部署候选。",
        "- 自适应规则必须在 27 个状态上使用至少两个动作；退化为单一动作的候选只作为 fixed baseline。",
        "- 部署候选需同时通过 selection eligibility、fair cost、相对/绝对 quality、p99、miss、background、worst-load quality 和 quality-feasible fraction 共 9 项门槛。",
        "- `fixed_moderate`、`fixed_recovery`、`fixed_full` 与两种 validation rule 是不学习的强部署基线。",
        "- Legacy cost 不含 background floor；Fair cost 额外惩罚低于 20% 的 background service。两者必须同时报告。",
        "",
        "## 创新点",
        "",
        "1. **新鲜 holdout 轮换。** v4.3 smoke 主动排除已观察的 v4.2 test 场景，使用第三个因子均衡场景块和新 workload seeds，避免事后调参污染结论。",
        "2. **有质量下界的规则搜索。** 新增只在 recovery/moderate 之间切换的单调 candidate family，避免 heavy state 退化到 conservative/critical-only。",
        "3. **从单一 scalar 转向分层安全门槛。** 不再允许 fair cost 抵消 deadline miss、background starvation 或最差负载质量失败。",
        "4. **训练目标对齐的可证伪对照。** strict aligned 与 aligned 在相同训练/测试协议下比较，直接检验“加重 miss 是否真能改善部署约束”。",
        "5. **可恢复实验工程。** 训练策略按 replicate/reward 类型落盘，full 中断后可校验协议指纹并继续。",
        "",
        "## 展望",
        "",
        "- 首先将 quality 从 retained-branch proxy 替换为任务 outcome，并把 speculative bytes 拆成 useful、late-unused 和 cancelled-inflight；这比继续调 reward 系数更重要。",
        "- 在 v4.3 smoke 通过候选中冻结一个策略，再使用 full OA 场景、5 个训练 replicate 和 cluster bootstrap 复核；如 smoke 无人过门，不启动论文级主张。",
        "- 进一步研究 constrained contextual bandit、低支持状态的层次池化、多瓶颈/多租户 trace 以及端到端 GPU-network 联合调度。",
        "",
        "## 结论边界",
        "",
        "该实验仍是单瓶颈、trace-driven 仿真；quality 是 retained-speculation proxy，不是语义正确率。"
        "Smoke 只用于淘汰候选，只有 full 的配对 CI 才能进入论文主张。",
    ]
    (out / "OPTIMIZATION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def train_checkpointed(
    checkpoint: Path,
    resume: bool,
    label: str,
    seed: int,
    episodes: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    matrix: Sequence[SCENARIO],
    scheduled: bool,
    reward_mode: str,
) -> h.AuditedBandit:
    if resume and checkpoint.is_file():
        print(f"[checkpoint] load {label}: {checkpoint.name}", flush=True)
        return h.load_bandit(checkpoint)
    print(f"[train] {label} seed={seed}", flush=True)
    policy = train_controller(
        seed,
        episodes,
        duration,
        max_workflows,
        max_time,
        matrix,
        scheduled,
        reward_mode,
    )
    h.save_bandit(checkpoint, policy)
    return policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mode = args.mode
    out = (
        Path(args.output_dir)
        if args.output_dir
        else ROOT / "results" / f"optimization_{mode}"
    )
    if out.exists() and any(out.iterdir()) and not args.resume:
        raise FileExistsError(
            f"output directory is not empty; choose a new directory or use --resume: {out}"
        )
    out.mkdir(parents=True, exist_ok=True)
    if mode == "smoke":
        duration, max_workflows, max_time = 700, 28, 2600
        episodes, replicates, ensemble_size = 36, 3, 3
        validation_limit, evaluation_limit = 12, 12
    else:
        duration, max_workflows, max_time = 1800, 90, 6000
        episodes, replicates, ensemble_size = 162, 5, 3
        validation_limit, evaluation_limit = 18, 18
    matrix = h.scenarios(mode)
    validation_matrix, evaluation_matrix, prior_evaluation_matrix = (
        protocol_scenario_split(
            mode, matrix, validation_limit, evaluation_limit
        )
    )

    checkpoint_dir = out / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_signature = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": mode,
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "optimization_harness_sha256": h.sha256(Path(__file__).resolve()),
        "proof_harness_sha256": h.sha256(Path(h.__file__).resolve()),
        "duration": duration,
        "max_workflows": max_workflows,
        "max_time": max_time,
        "training_episodes": episodes,
        "replicates": replicates,
        "ensemble_size": ensemble_size,
        "training_matrix": [list(row) for row in matrix],
    }
    checkpoint_manifest = checkpoint_dir / "checkpoint_manifest.json"
    if args.resume:
        if not checkpoint_manifest.is_file():
            raise FileNotFoundError(
                f"resume requested without checkpoint manifest: {checkpoint_manifest}"
            )
        observed_signature = json.loads(
            checkpoint_manifest.read_text(encoding="utf-8")
        )
        if observed_signature != checkpoint_signature:
            raise ValueError("checkpoint protocol fingerprint does not match current run")
    else:
        h.write_json(checkpoint_manifest, checkpoint_signature)

    baseline_policies = []
    scheduled_policies = []
    fair_policies = []
    ensemble_policies = []
    for replicate in range(replicates):
        member_seeds = [7 + 101 * (replicate * ensemble_size + member) for member in range(ensemble_size)]
        baseline_policies.append(
            train_checkpointed(
                checkpoint_dir / f"baseline_r{replicate}.json",
                args.resume,
                f"replicate={replicate} baseline",
                member_seeds[0],
                episodes,
                duration,
                max_workflows,
                max_time,
                matrix,
                False,
                "base",
            )
        )
        members = []
        for member, seed in enumerate(member_seeds):
            members.append(
                train_checkpointed(
                    checkpoint_dir / f"scheduled_r{replicate}_m{member}.json",
                    args.resume,
                    f"replicate={replicate} scheduled member={member}",
                    seed,
                    episodes,
                    duration,
                    max_workflows,
                    max_time,
                    matrix,
                    True,
                    "base",
                )
            )
        scheduled_policies.append(members[0])
        ensemble_policies.append(MedianEnsemble(members))
        fair_policies.append(
            train_checkpointed(
                checkpoint_dir / f"fair_r{replicate}.json",
                args.resume,
                f"replicate={replicate} fair",
                member_seeds[0],
                episodes,
                duration,
                max_workflows,
                max_time,
                matrix,
                True,
                "fair",
            )
        )
    aligned_policies = []
    strict_aligned_policies = []
    for replicate in range(replicates):
        seed = 7 + 101 * (replicate * ensemble_size)
        aligned_policies.append(
            train_checkpointed(
                checkpoint_dir / f"aligned_r{replicate}.json",
                args.resume,
                f"replicate={replicate} aligned",
                seed,
                episodes,
                duration,
                max_workflows,
                max_time,
                matrix,
                True,
                "aligned",
            )
        )
        strict_aligned_policies.append(
            train_checkpointed(
                checkpoint_dir / f"strict_aligned_r{replicate}.json",
                args.resume,
                f"replicate={replicate} strict_aligned",
                seed,
                episodes,
                duration,
                max_workflows,
                max_time,
                matrix,
                True,
                "strict_aligned",
            )
        )

    fallback_params, fallback_metadata = select_fallback_rule(
        mode,
        validation_matrix,
        baseline_policies,
        duration,
        max_workflows,
        max_time,
    )
    robust_params = dict(fallback_metadata["robust"]["params"])
    hybrid_m002_policies = [
        ConfidenceHybrid(policy, fallback_params, min_margin=0.02)
        for policy in ensemble_policies
    ]
    hybrid_m005_policies = [
        ConfidenceHybrid(policy, fallback_params, min_margin=0.05)
        for policy in ensemble_policies
    ]
    fixed_moderate_policies = [
        h.FixedActionPolicy("moderate", seed=replicate) for replicate in range(replicates)
    ]
    fixed_recovery_policies = [
        h.FixedActionPolicy("recovery", seed=replicate) for replicate in range(replicates)
    ]
    fixed_full_policies = [
        h.FixedActionPolicy("full", seed=replicate) for replicate in range(replicates)
    ]
    validation_rule_policies = [
        h.TunedRiskRulePolicy(fallback_params, seed=replicate, name="validation_rule")
        for replicate in range(replicates)
    ]
    robust_rule_policies = [
        h.TunedRiskRulePolicy(
            robust_params, seed=replicate, name="robust_validation_rule"
        )
        for replicate in range(replicates)
    ]
    variants: Dict[str, Sequence[object]] = {
        "baseline": baseline_policies,
        "scheduled": scheduled_policies,
        "fair_scheduled": fair_policies,
        "aligned_scheduled": aligned_policies,
        "strict_aligned_scheduled": strict_aligned_policies,
        "scheduled_ensemble": ensemble_policies,
        "confidence_hybrid_m002": hybrid_m002_policies,
        "confidence_hybrid_m005": hybrid_m005_policies,
        "fixed_moderate": fixed_moderate_policies,
        "fixed_recovery": fixed_recovery_policies,
        "fixed_full": fixed_full_policies,
        "validation_rule": validation_rule_policies,
        "robust_validation_rule": robust_rule_policies,
    }

    performance: List[Dict[str, object]] = []
    for replicate in range(replicates):
        for scenario_index, scenario in enumerate(evaluation_matrix):
            seed = TEST_SEED_BASE + scenario_index
            for variant, policies in variants.items():
                metrics = run_controller(
                    policies[replicate], scenario, seed, duration, max_workflows, max_time
                )
                performance.append(
                    {
                        "variant": variant,
                        "replicate": replicate,
                        "scenario": scenario_index,
                        "seed": seed,
                        "load": scenario[0],
                        "deadline_scale": scenario[1],
                        "optional_scale": scenario[2],
                        "capacity_scale": scenario[3],
                        **metrics,
                    }
                )
    add_costs(
        performance,
        float(fallback_metadata["quality_floor"]),
        float(fallback_metadata["p99_scale"]),
        float(fallback_metadata["waste_scale"]),
    )
    summaries = performance_summary_rows(performance)
    comparisons = paired_comparison_rows(performance)
    by_load = performance_by_load_rows(performance)
    stability = stability_rows(variants)
    sanity = sanity_summary_rows(variants)
    gates = deployment_gate_rows(
        summaries, comparisons, by_load, fallback_metadata
    )

    h.write_csv(out / "performance.csv", performance)
    h.write_csv(out / "performance_summary.csv", summaries)
    h.write_csv(out / "performance_by_load.csv", by_load)
    h.write_csv(out / "paired_comparisons.csv", comparisons)
    h.write_csv(out / "deployment_gates.csv", gates)
    h.write_csv(out / "policy_stability.csv", stability)
    h.write_csv(out / "sanity_summary.csv", sanity)
    h.write_json(out / "selected_fallback_rule.json", fallback_metadata)
    h.save_bandit(out / "baseline_reference.json", baseline_policies[0])
    h.save_bandit(out / "scheduled_reference.json", scheduled_policies[0])
    h.save_bandit(out / "fair_scheduled_reference.json", fair_policies[0])
    h.save_bandit(out / "aligned_scheduled_reference.json", aligned_policies[0])
    h.save_bandit(
        out / "strict_aligned_scheduled_reference.json", strict_aligned_policies[0]
    )
    h.save_bandit(out / "scheduled_ensemble_reference.json", ensemble_policies[0])
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": mode,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "optimization_harness_sha256": h.sha256(Path(__file__).resolve()),
        "proof_harness_sha256": h.sha256(Path(h.__file__).resolve()),
        "duration": duration,
        "max_workflows": max_workflows,
        "max_time": max_time,
        "training_episodes": episodes,
        "replicates": replicates,
        "ensemble_size": ensemble_size,
        "validation_scenarios": len(validation_matrix),
        "evaluation_scenarios": len(evaluation_matrix),
        "validation_matrix": validation_matrix,
        "evaluation_matrix": evaluation_matrix,
        "prior_evaluation_matrix_excluded": prior_evaluation_matrix,
        "test_seed_rule": (
            f"{TEST_SEED_BASE} + scenario_index; shared by every variant and replicate"
        ),
        "scenario_split": (
            "v4.3 smoke uses the third factor-balanced block after excluding "
            "v4.2 validation and observed evaluation blocks; full uses disjoint OA blocks"
        ),
        "checkpoint_resume_supported": True,
        "deployment_gates": {
            "quality_floor": QUALITY_FLOOR,
            "quality_relative_margin": QUALITY_RELATIVE_MARGIN,
            "p99_relative_noninferiority_margin": P99_RELATIVE_NONINFERIORITY_MARGIN,
            "deadline_miss_absolute_noninferiority_margin": DEADLINE_MISS_NONINFERIORITY_MARGIN,
            "background_service_floor": BACKGROUND_SERVICE_FLOOR,
            "worst_load_quality_floor": WORST_LOAD_QUALITY_FLOOR,
            "quality_feasible_fraction_floor": QUALITY_FEASIBLE_FRACTION_FLOOR,
            "fair_cost_ci95_high_below_zero": True,
        },
        "fallback_rule": fallback_metadata,
    }
    h.write_json(out / "run_manifest.json", manifest)
    build_report(out, mode, manifest, summaries, comparisons, stability, sanity, gates)
    print(f"Optimization study complete: {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
