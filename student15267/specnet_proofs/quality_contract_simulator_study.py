#!/usr/bin/env python3
"""Validation-only event-simulator study for negotiated quality contracts.

The study keeps V5's capacity-consistent service pool, staged completion rule,
and explicit pre-judge optional barrier.  It changes only optional admission:
one comparator holds a robust 0.95 contract and falls back to all optional
branches when infeasible; the candidate negotiates from 0.95 to 0.94 and
rejects optional admission if neither tier is feasible.
"""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:
    from . import trace_deployment_v4_study as v4
    from . import trace_deployment_v5_resource_study as v5
    from .quality_contract_broker import (
        QualityContract,
        QualityTier,
        VirtualByteDebtLedger,
        select_minimum_byte_portfolio,
        select_quality_tier,
    )
    from .quality_contract_trace_audit import TRACE_UP
except ImportError:  # pragma: no cover - direct script invocation
    import trace_deployment_v4_study as v4
    import trace_deployment_v5_resource_study as v5
    from quality_contract_broker import (
        QualityContract,
        QualityTier,
        VirtualByteDebtLedger,
        select_minimum_byte_portfolio,
        select_quality_tier,
    )
    from quality_contract_trace_audit import TRACE_UP


PROTOCOL_VERSION = "2026-08-18.quality-contract-event-validation-v2"
PRIMARY_QUALITY_TARGET = 0.95
FALLBACK_QUALITY_TARGET = 0.94
UNCERTAINTY_PENALTY = 1.0
DEFAULT_SEED_BASE = 2_680_000


@dataclass(frozen=True)
class AdmissionDecision:
    selected_branch_ids: Tuple[int, ...]
    requested_target: float
    granted_target: float | None
    tier: str
    primary_feasible: bool
    degraded: bool
    rejected: bool
    budget_fallback: bool
    admitted_bytes: float
    full_optional_bytes: float
    point_minimum_bytes: float
    achieved_lower_utility: float
    predicted_point_quality: float
    predicted_lower_quality: float


def quality_required_utility(
    point_potential: float,
    quality_target: float,
) -> float:
    fraction = min(
        1.0,
        max(
            0.0,
            (quality_target - TRACE_UP.BASE_REQUIRED_QUALITY)
            / max(1e-12, 1.0 - TRACE_UP.BASE_REQUIRED_QUALITY),
        ),
    )
    return fraction * point_potential


def utility_quality(utility: float, point_potential: float) -> float:
    if point_potential <= 1e-12:
        return 1.0
    retained_fraction = min(1.0, max(0.0, utility / point_potential))
    return TRACE_UP.BASE_REQUIRED_QUALITY + (
        1.0 - TRACE_UP.BASE_REQUIRED_QUALITY
    ) * retained_fraction


def contract_admission_decision(
    workflow_spec: object,
    uncertainty_penalty: float,
    fallback_quality_target: float | None,
    primary_quality_target: float = PRIMARY_QUALITY_TARGET,
    admission_byte_budget_ratio: float | None = None,
) -> AdmissionDecision:
    if admission_byte_budget_ratio is not None and admission_byte_budget_ratio < 1.0:
        raise ValueError("admission byte budget ratio must be at least 1")
    optional = [branch for branch in workflow_spec.branches if not branch.required]
    contracts = [
        QualityContract(
            contract_id=int(branch.branch_index),
            byte_cost=float(branch.size),
            expected_utility=float(branch.expected_utility),
            selection_probability=float(branch.selection_probability),
        )
        for branch in optional
    ]
    retain_limit = TRACE_UP.JUDGE_RETAIN_LIMIT.get(
        workflow_spec.template,
        len(optional),
    )
    point_potential = sum(
        sorted(
            (contract.expected_utility for contract in contracts),
            reverse=True,
        )[:retain_limit]
    )
    primary_required = quality_required_utility(
        point_potential,
        primary_quality_target,
    )
    point_portfolio = select_minimum_byte_portfolio(
        contracts,
        primary_required,
        retain_limit,
        uncertainty_penalty=0.0,
    )
    primary_portfolio = select_minimum_byte_portfolio(
        contracts,
        primary_required,
        retain_limit,
        uncertainty_penalty=uncertainty_penalty,
    )
    budget_fallback = False

    if fallback_quality_target is None:
        portfolio = primary_portfolio
        granted_target = primary_quality_target
        tier = "primary" if portfolio.feasible else "infeasible_full_fallback"
        degraded = False
        rejected = False
    else:
        if not TRACE_UP.BASE_REQUIRED_QUALITY <= fallback_quality_target <= primary_quality_target:
            raise ValueError(
                "fallback target must lie between base and primary quality"
            )
        fallback_required = quality_required_utility(
            point_potential,
            fallback_quality_target,
        )
        negotiated = select_quality_tier(
            contracts,
            [
                QualityTier("primary", primary_required),
                QualityTier("degraded", fallback_required),
            ],
            retain_limit,
            uncertainty_penalty=uncertainty_penalty,
        )
        if negotiated.feasible:
            portfolio = negotiated.portfolio
            tier = str(negotiated.granted_tier)
            granted_target = (
                primary_quality_target
                if tier == "primary"
                else fallback_quality_target
            )
            degraded = negotiated.degraded
            rejected = False
        else:
            portfolio = negotiated.portfolio
            tier = "rejected"
            granted_target = None
            degraded = False
            rejected = True

    if (
        admission_byte_budget_ratio is not None
        and portfolio.total_bytes
        > point_portfolio.total_bytes * admission_byte_budget_ratio + 1e-12
    ):
        portfolio = point_portfolio
        tier = "budget_fallback"
        granted_target = primary_quality_target
        degraded = False
        rejected = False
        budget_fallback = True

    selected_ids = (
        ()
        if rejected
        else tuple(sorted(contract.contract_id for contract in portfolio.contracts))
    )
    selected_contracts = [
        contract for contract in contracts if contract.contract_id in selected_ids
    ]
    point_selected_utility = sum(
        sorted(
            (contract.expected_utility for contract in selected_contracts),
            reverse=True,
        )[:retain_limit]
    )
    lower_selected_utility = sum(
        sorted(
            (
                contract.lower_utility(uncertainty_penalty)
                for contract in selected_contracts
            ),
            reverse=True,
        )[:retain_limit]
    )
    return AdmissionDecision(
        selected_branch_ids=selected_ids,
        requested_target=primary_quality_target,
        granted_target=granted_target,
        tier=tier,
        primary_feasible=primary_portfolio.feasible,
        degraded=degraded,
        rejected=rejected,
        budget_fallback=budget_fallback,
        admitted_bytes=sum(contract.byte_cost for contract in selected_contracts),
        full_optional_bytes=sum(contract.byte_cost for contract in contracts),
        point_minimum_bytes=point_portfolio.total_bytes,
        achieved_lower_utility=lower_selected_utility,
        predicted_point_quality=utility_quality(
            point_selected_utility,
            point_potential,
        ),
        predicted_lower_quality=utility_quality(
            lower_selected_utility,
            point_potential,
        ),
    )


class ContractMinimumQualitySimulator(v5.CapacityConsistentMinimumQualitySimulator):
    """V5 event simulator with contract admission and byte-debt accounting."""

    def __init__(
        self,
        *args,
        uncertainty_penalty: float,
        fallback_quality_target: float | None,
        admission_byte_budget_ratio: float | None,
        **kwargs,
    ) -> None:
        self.uncertainty_penalty = float(uncertainty_penalty)
        self.fallback_quality_target = fallback_quality_target
        self.admission_byte_budget_ratio = admission_byte_budget_ratio
        self.contract_decisions: Dict[int, AdmissionDecision] = {}
        self.byte_debt_ledger = VirtualByteDebtLedger()
        super().__init__(*args, **kwargs)

    def branches_for_action(self, workflow, action: str) -> List[object]:
        required = [branch for branch in workflow.spec.branches if branch.required]
        decision = contract_admission_decision(
            workflow.spec,
            self.uncertainty_penalty,
            self.fallback_quality_target,
            admission_byte_budget_ratio=self.admission_byte_budget_ratio,
        )
        self.contract_decisions[workflow.spec.workflow_id] = decision
        selected_ids = set(decision.selected_branch_ids)
        selected = [
            branch
            for branch in workflow.spec.branches
            if not branch.required and int(branch.branch_index) in selected_ids
        ]
        self.byte_debt_ledger.allocate(
            workflow.spec.workflow_id,
            decision.admitted_bytes,
        )
        return required + selected

    def serve_active_flows(self) -> None:
        before = {
            flow_id: float(flow.served) for flow_id, flow in self.flows.items()
        }
        super().serve_active_flows()
        for flow_id, flow in self.flows.items():
            delta = float(flow.served) - before.get(flow_id, 0.0)
            if delta <= 1e-12:
                continue
            if flow.background:
                self.byte_debt_ledger.charge(
                    flow.workflow_id,
                    delta,
                    "background",
                )
            elif flow.speculative:
                self.byte_debt_ledger.charge(
                    flow.workflow_id,
                    delta,
                    "optional",
                )

    def summary(self) -> Dict[str, object]:
        result = super().summary()
        self.byte_debt_ledger.assert_conservation()
        tier_counts = {
            "primary": 0,
            "degraded": 0,
            "rejected": 0,
            "infeasible_full_fallback": 0,
            "budget_fallback": 0,
        }
        contract_met = []
        admitted = []
        point_minimum = []
        predicted_lower = []
        for record in result["workflow_records"]:
            workflow_id = int(record["workflow_id"])
            decision = self.contract_decisions[workflow_id]
            account = self.byte_debt_ledger.account(workflow_id)
            tier_counts[decision.tier] += 1
            met = int(
                decision.granted_target is not None
                and float(record["quality"]) + 1e-12 >= decision.granted_target
            )
            contract_met.append(met)
            admitted.append(decision.admitted_bytes)
            point_minimum.append(decision.point_minimum_bytes)
            predicted_lower.append(decision.predicted_lower_quality)
            record.update(
                {
                    "contract_tier": decision.tier,
                    "contract_requested_target": decision.requested_target,
                    "contract_granted_target": (
                        decision.granted_target
                        if decision.granted_target is not None
                        else ""
                    ),
                    "contract_target_met": met,
                    "contract_primary_feasible": int(decision.primary_feasible),
                    "contract_degraded": int(decision.degraded),
                    "contract_rejected": int(decision.rejected),
                    "contract_budget_fallback": int(decision.budget_fallback),
                    "contract_admitted_bytes": decision.admitted_bytes,
                    "contract_point_minimum_bytes": decision.point_minimum_bytes,
                    "contract_predicted_point_quality": decision.predicted_point_quality,
                    "contract_predicted_lower_quality": decision.predicted_lower_quality,
                    "debt_allocated_budget": account.allocated_budget,
                    "debt_optional_charged": account.optional_charged,
                    "debt_background_charged": account.background_charged,
                    "debt_outstanding": account.outstanding,
                    "debt_unused_budget": account.unused_budget,
                }
            )
        accounts = list(self.byte_debt_ledger.accounts.values())
        workflow_count = max(1, len(contract_met))
        result.update(
            {
                "contract_target_met_ratio": sum(contract_met) / workflow_count,
                "contract_primary_ratio": tier_counts["primary"] / workflow_count,
                "contract_degraded_ratio": tier_counts["degraded"] / workflow_count,
                "contract_rejected_ratio": tier_counts["rejected"] / workflow_count,
                "contract_infeasible_full_fallback_ratio": (
                    tier_counts["infeasible_full_fallback"] / workflow_count
                ),
                "contract_budget_fallback_ratio": (
                    tier_counts["budget_fallback"] / workflow_count
                ),
                "contract_admitted_bytes_per_workflow": (
                    statistics.mean(admitted) if admitted else 0.0
                ),
                "contract_point_minimum_bytes_per_workflow": (
                    statistics.mean(point_minimum) if point_minimum else 0.0
                ),
                "contract_predicted_lower_quality_mean": (
                    statistics.mean(predicted_lower) if predicted_lower else 1.0
                ),
                "debt_allocated_budget": sum(
                    account.allocated_budget for account in accounts
                ),
                "debt_optional_charged": sum(
                    account.optional_charged for account in accounts
                ),
                "debt_background_charged": sum(
                    account.background_charged for account in accounts
                ),
                "debt_outstanding": self.byte_debt_ledger.global_outstanding,
                "debt_unused_budget": sum(
                    account.unused_budget for account in accounts
                ),
                "debt_conservation_pass": 1,
            }
        )
        return result


def common_simulator_kwargs(capacity_scale: float) -> Dict[str, object]:
    return {
        "capacity_scale": capacity_scale,
        "pressure_definition": v4.PRESSURE_DEFINITION,
        "quality_target": PRIMARY_QUALITY_TARGET,
        "quality_hard_floor": PRIMARY_QUALITY_TARGET,
        "safety_guard": True,
        "optional_quality_target": PRIMARY_QUALITY_TARGET,
        "completion_barrier": True,
    }


def run_variant(
    variant: str,
    specs: Sequence[object],
    policy: object,
    load: str,
    seed: int,
    capacity_scale: float,
    uncertainty_penalty: float,
    fallback_quality_target: float | None,
    admission_byte_budget_ratio: float | None = None,
) -> Dict[str, object]:
    kwargs = common_simulator_kwargs(capacity_scale)
    if variant == "v4_minq_96":
        kwargs["completion_barrier"] = False
        simulator = v5.CapacityConsistentMinimumQualitySimulator(
            copy.deepcopy(specs),
            policy,
            load,
            seed,
            v5.SCREEN_DURATION,
            v5.SCREEN_MAX_TIME,
            **kwargs,
        )
    elif variant == "v5_point_staged":
        simulator = v5.CapacityConsistentMinimumQualitySimulator(
            copy.deepcopy(specs),
            policy,
            load,
            seed,
            v5.SCREEN_DURATION,
            v5.SCREEN_MAX_TIME,
            **kwargs,
        )
    else:
        simulator = ContractMinimumQualitySimulator(
            copy.deepcopy(specs),
            policy,
            load,
            seed,
            v5.SCREEN_DURATION,
            v5.SCREEN_MAX_TIME,
            uncertainty_penalty=uncertainty_penalty,
            fallback_quality_target=fallback_quality_target,
            admission_byte_budget_ratio=admission_byte_budget_ratio,
            **kwargs,
        )
    summary = simulator.run()
    summary.update(
        {
            "variant": variant,
            "total_served_bytes": simulator.total_served,
            "total_capacity_bytes": simulator.total_capacity,
        }
    )
    return summary


def event_metric_row(summary: Mapping[str, object]) -> Dict[str, float]:
    row = dict(v5.metric_row(summary))
    row.update(
        {
            "contract_target_met_ratio": float(
                summary.get(
                    "contract_target_met_ratio",
                    summary["quality_target_met_ratio"],
                )
            ),
            "contract_primary_ratio": float(
                summary.get("contract_primary_ratio", 1.0)
            ),
            "contract_degraded_ratio": float(
                summary.get("contract_degraded_ratio", 0.0)
            ),
            "contract_rejected_ratio": float(
                summary.get("contract_rejected_ratio", 0.0)
            ),
            "contract_infeasible_full_fallback_ratio": float(
                summary.get("contract_infeasible_full_fallback_ratio", 0.0)
            ),
            "contract_budget_fallback_ratio": float(
                summary.get("contract_budget_fallback_ratio", 0.0)
            ),
            "contract_admitted_bytes_per_workflow": float(
                summary.get(
                    "contract_admitted_bytes_per_workflow",
                    row["admitted_optional_bytes_per_workflow"],
                )
            ),
            "contract_point_minimum_bytes_per_workflow": float(
                summary.get(
                    "contract_point_minimum_bytes_per_workflow",
                    row["admitted_optional_bytes_per_workflow"],
                )
            ),
            "contract_predicted_lower_quality_mean": float(
                summary.get("contract_predicted_lower_quality_mean", 1.0)
            ),
            "debt_allocated_budget": float(
                summary.get("debt_allocated_budget", 0.0)
            ),
            "debt_optional_charged": float(
                summary.get("debt_optional_charged", 0.0)
            ),
            "debt_background_charged": float(
                summary.get("debt_background_charged", 0.0)
            ),
            "debt_outstanding": float(summary.get("debt_outstanding", 0.0)),
            "debt_unused_budget": float(summary.get("debt_unused_budget", 0.0)),
            "debt_conservation_pass": float(
                summary.get("debt_conservation_pass", 1.0)
            ),
        }
    )
    return row


def mean_rows(rows: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    return v5.mean_rows(rows)


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    fields: List[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def scenario_comparisons(
    candidate_rows: Sequence[Mapping[str, object]],
    reference_rows: Sequence[Mapping[str, object]],
    reference_name: str,
) -> List[Dict[str, object]]:
    by_scenario: Dict[int, List[Tuple[Mapping[str, object], Mapping[str, object]]]] = {}
    for candidate, reference in zip(candidate_rows, reference_rows):
        scenario = int(candidate["scenario"])
        by_scenario.setdefault(scenario, []).append((candidate, reference))
    output: List[Dict[str, object]] = []
    for scenario, pairs in sorted(by_scenario.items()):
        row: Dict[str, object] = {
            "scenario": scenario,
            "paired_runs": len(pairs),
            "reference": reference_name,
            "min_contract_target_met_ratio": min(
                float(candidate["contract_target_met_ratio"])
                for candidate, _ in pairs
            ),
            "min_original_095_target_ratio": min(
                float(candidate["quality_target_met_ratio"])
                for candidate, _ in pairs
            ),
            "max_rejected_ratio": max(
                float(candidate["contract_rejected_ratio"])
                for candidate, _ in pairs
            ),
            "max_budget_fallback_ratio": max(
                float(candidate["contract_budget_fallback_ratio"])
                for candidate, _ in pairs
            ),
        }
        for metric in (
            "avg_quality",
            "p99_latency",
            "deadline_miss_ratio",
            "total_served_bytes",
            "link_utilization",
            "path_queue_pressure_p99",
        ):
            row[f"delta_{metric}"] = statistics.fmean(
                float(candidate[metric]) - float(reference[metric])
                for candidate, reference in pairs
            )
        row["resource_tail_gate_pass"] = int(
            float(row["delta_p99_latency"]) <= 1e-9
            and float(row["delta_deadline_miss_ratio"]) <= 0.02 + 1e-9
            and float(row["delta_total_served_bytes"]) <= 1e-9
            and float(row["delta_link_utilization"]) <= 1e-9
        )
        row["contract_gate_pass"] = int(
            float(row["min_contract_target_met_ratio"]) >= 1.0
            and float(row["max_rejected_ratio"]) <= 1e-12
            and int(row["resource_tail_gate_pass"]) == 1
        )
        row["original_095_gate_pass"] = int(
            float(row["min_original_095_target_ratio"]) >= 1.0
            and int(row["resource_tail_gate_pass"]) == 1
        )
        output.append(row)
    return output


def write_report(
    output_dir: Path,
    summaries: Sequence[Mapping[str, object]],
    tiered_vs_v5: Sequence[Mapping[str, object]],
    tiered_vs_fixed: Sequence[Mapping[str, object]],
    budgeted_vs_v5: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> None:
    lines = [
        "# 分层质量契约事件模拟实验",
        "",
        "本实验只使用 validation split。五个方案共享相同 workload、容量一致服务池和随机 seed；QCB 方案沿用 V5 调度，仅改变 optional 准入集合。",
        "",
        "## 总体结果",
        "",
        "| 方案 | 实际质量 | 原 0.95 达标率 | 授予契约达标率 | 降级/拒绝/预算回退/全量回退 | P99 | served bytes | optional bytes/workflow | Δbytes vs V5 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['variant']} | {float(row['avg_quality']):.4f} | "
            f"{float(row['quality_target_met_ratio']):.2%} | "
            f"{float(row['contract_target_met_ratio']):.2%} | "
            f"{float(row['contract_degraded_ratio']):.2%}/"
            f"{float(row['contract_rejected_ratio']):.2%}/"
            f"{float(row['contract_budget_fallback_ratio']):.2%}/"
            f"{float(row['contract_infeasible_full_fallback_ratio']):.2%} | "
            f"{float(row['p99_latency']):.3f} | "
            f"{float(row['total_served_bytes']):.2f} | "
            f"{float(row['contract_admitted_bytes_per_workflow']):.2f} | "
            f"{float(row['delta_total_served_bytes_vs_v5']):+.2f} |"
        )
    v5_pass = sum(int(row["contract_gate_pass"]) for row in tiered_vs_v5)
    fixed_pass = sum(int(row["contract_gate_pass"]) for row in tiered_vs_fixed)
    original_pass = sum(int(row["original_095_gate_pass"]) for row in tiered_vs_v5)
    budgeted_pass = sum(int(row["contract_gate_pass"]) for row in budgeted_vs_v5)
    budgeted_fallback = max(
        float(row["max_budget_fallback_ratio"]) for row in budgeted_vs_v5
    )
    lines += [
        "",
        "## 场景硬门",
        "",
        f"- 分层契约相对 V5：{v5_pass}/{len(tiered_vs_v5)} 个场景通过授予契约、P99、miss、bytes、utilization 联合门。",
        f"- 分层契约相对固定稳健 0.95：{fixed_pass}/{len(tiered_vs_fixed)} 个场景通过联合门。",
        f"- 若仍坚持所有 workflow 必须达到原 0.95，分层方案相对 V5 有 {original_pass}/{len(tiered_vs_v5)} 个场景通过完整门。",
        f"- 预算约束分层契约相对 V5：{budgeted_pass}/{len(budgeted_vs_v5)} 个场景通过联合门；最坏场景的 V5 预算回退比例为 {budgeted_fallback:.2%}。",
        "",
        "## 账务与边界",
        "",
        "- optional 实际服务写入 workflow byte budget；background 服务作为显式 outstanding debt，不再藏在总体 served bytes 中。",
        "- 每个运行都执行账户守恒断言；守恒通过不代表资源预算已经通过。",
        "- 预算回退保证不会以超过 V5 点估计组合的可选准入字节换取表面质量；回退不等于鲁棒契约得到验证，必须单列报告。",
        "- 不确定性折扣仍是敏感性压力，不是从真实质量标签校准出的置信下界。",
        "- 0.94 是 validation 候选；本轮结果不能用于修改 test 参数。",
        f"- 协议：`{manifest['protocol_version']}`；profile：`{manifest['profile']}`；seed：`{manifest['seed_rule']}`。",
    ]
    (output_dir / "QUALITY_CONTRACT_EVENT_REPORT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("trace_driven_v1", "trace_driven_v2"), required=True)
    parser.add_argument("--data-root", type=Path, default=v4.DEFAULT_DATA_ROOT)
    parser.add_argument("--scenario-count", type=int, default=9)
    parser.add_argument("--evaluation-runs", type=int, default=3)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument("--uncertainty-penalty", type=float, default=UNCERTAINTY_PENALTY)
    parser.add_argument("--fallback-quality-target", type=float, default=FALLBACK_QUALITY_TARGET)
    parser.add_argument("--admission-byte-budget-ratio", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scenario_count < 1 or args.evaluation_runs < 1:
        raise ValueError("scenario count and evaluation runs must be positive")
    if not 0.0 <= args.uncertainty_penalty <= 1.0:
        raise ValueError("uncertainty penalty must lie in [0, 1]")
    if args.admission_byte_budget_ratio < 1.0:
        raise ValueError("admission byte budget ratio must be at least 1")
    trace_profile = v4.profile_path(args.profile, args.data_root)
    matrix = v4.balanced_evaluation_matrix(
        list(
            itertools.product(
                TRACE_UP.LOAD_CONFIG.keys(),
                (0.30, 0.65, 1.20),
                (0.45, 1.65),
                (0.72, 1.25),
            )
        ),
        args.scenario_count,
        seed=268_081,
    )
    variants: Dict[str, List[Dict[str, object]]] = {
        "v4_minq_96": [],
        "v5_point_staged": [],
        "qcb_fixed_095": [],
        "qcb_tiered_095_094": [],
        "qcb_budgeted_tiered_095_094": [],
    }
    print(
        f"[qcb event validation] {args.profile}: "
        f"{len(matrix)} scenarios x {args.evaluation_runs} runs",
        flush=True,
    )
    for evaluation_run in range(args.evaluation_runs):
        for scenario_index, scenario in enumerate(matrix):
            load, deadline_scale, optional_scale, capacity_scale = scenario
            seed = args.seed_base + evaluation_run * 10_000 + scenario_index
            specs = v4.scaled_trace_workload(
                seed,
                load,
                v5.SCREEN_DURATION,
                v5.SCREEN_MAX_WORKFLOWS,
                deadline_scale,
                optional_scale,
                args.profile,
                trace_profile,
                "validation",
                upstream=TRACE_UP,
            )
            policies = {
                "v4_minq_96": v4.DeadlineReservedMinimumQualityRule(
                    seed,
                    v5.V4_BASE_BOOST,
                    v5.V4_URGENCY_GAIN,
                    reserve_margin=v5.V4_RESERVE_MARGIN,
                ),
                "v5_point_staged": v5.StagedMinimumQualityRule(
                    seed,
                    1.0,
                    0.75,
                ),
                "qcb_fixed_095": v5.StagedMinimumQualityRule(
                    seed,
                    1.0,
                    0.75,
                ),
                "qcb_tiered_095_094": v5.StagedMinimumQualityRule(
                    seed,
                    1.0,
                    0.75,
                ),
                "qcb_budgeted_tiered_095_094": v5.StagedMinimumQualityRule(
                    seed,
                    1.0,
                    0.75,
                ),
            }
            for variant, policy in policies.items():
                fallback = (
                    args.fallback_quality_target
                    if variant in (
                        "qcb_tiered_095_094",
                        "qcb_budgeted_tiered_095_094",
                    )
                    else None
                )
                budget_ratio = (
                    args.admission_byte_budget_ratio
                    if variant == "qcb_budgeted_tiered_095_094"
                    else None
                )
                summary = run_variant(
                    variant,
                    specs,
                    policy,
                    load,
                    seed,
                    capacity_scale,
                    args.uncertainty_penalty,
                    fallback,
                    budget_ratio,
                )
                variants[variant].append(
                    {
                        "evaluation_run": evaluation_run,
                        "scenario": scenario_index,
                        "load": load,
                        "deadline_scale": deadline_scale,
                        "optional_scale": optional_scale,
                        "capacity_scale": capacity_scale,
                        **event_metric_row(summary),
                    }
                )

    aggregate = {variant: mean_rows(rows) for variant, rows in variants.items()}
    v5_reference = aggregate["v5_point_staged"]
    summaries: List[Dict[str, object]] = []
    for variant, metrics in aggregate.items():
        row: Dict[str, object] = {"variant": variant, **metrics}
        for metric in (
            "avg_quality",
            "p99_latency",
            "deadline_miss_ratio",
            "total_served_bytes",
            "link_utilization",
            "path_queue_pressure_p99",
        ):
            row[f"delta_{metric}_vs_v5"] = (
                float(metrics[metric]) - float(v5_reference[metric])
            )
        summaries.append(row)

    tiered_vs_v5 = scenario_comparisons(
        variants["qcb_tiered_095_094"],
        variants["v5_point_staged"],
        "v5_point_staged",
    )
    tiered_vs_fixed = scenario_comparisons(
        variants["qcb_tiered_095_094"],
        variants["qcb_fixed_095"],
        "qcb_fixed_095",
    )
    budgeted_vs_v5 = scenario_comparisons(
        variants["qcb_budgeted_tiered_095_094"],
        variants["v5_point_staged"],
        "v5_point_staged",
    )
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "profile": args.profile,
        "split": "validation",
        "scenario_count": len(matrix),
        "evaluation_runs": args.evaluation_runs,
        "evaluation_matrix": matrix,
        "seed_rule": f"{args.seed_base} + evaluation_run*10000 + scenario_index",
        "primary_quality_target": PRIMARY_QUALITY_TARGET,
        "fallback_quality_target": args.fallback_quality_target,
        "uncertainty_penalty": args.uncertainty_penalty,
        "admission_byte_budget_ratio": args.admission_byte_budget_ratio,
        "comparison": "paired V4/V5/fixed-robust/tiered-contract event simulation",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for variant, rows in variants.items():
        write_csv(args.output_dir / f"{variant}_cells.csv", rows)
    write_csv(args.output_dir / "summary.csv", summaries)
    write_csv(args.output_dir / "tiered_vs_v5_scenarios.csv", tiered_vs_v5)
    write_csv(args.output_dir / "tiered_vs_fixed_scenarios.csv", tiered_vs_fixed)
    write_csv(args.output_dir / "budgeted_vs_v5_scenarios.csv", budgeted_vs_v5)
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.output_dir,
        summaries,
        tiered_vs_v5,
        tiered_vs_fixed,
        budgeted_vs_v5,
        manifest,
    )
    print(f"[done] QCB event results written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
