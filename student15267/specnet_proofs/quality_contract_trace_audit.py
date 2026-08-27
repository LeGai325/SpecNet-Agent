#!/usr/bin/env python3
"""Validation-only audit for quality-contract optional-branch admission.

This experiment deliberately stops before event-simulator integration.  It
checks that the point-estimate broker is exactly equivalent to V4, then
measures the extra admission demand and infeasibility exposed by deterministic
uncertainty haircuts on V1/V2 validation workloads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import itertools
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parent
TRACE_UPSTREAM = (
    ROOT
    / "trace_upstream_snapshot"
    / "specnet_agent_experiments"
    / "specnet_agent_experiment.py"
)
DEFAULT_DATA_ROOT = ROOT.parent / "external_agent_data"
os.environ.setdefault("SPECNET_UPSTREAM", str(TRACE_UPSTREAM))
os.environ.setdefault("SPECNET_DATA_ROOT", str(DEFAULT_DATA_ROOT))


def load_trace_upstream(path: Path):
    module_name = "specnet_quality_contract_trace_upstream"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load trace upstream: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


TRACE_UP = load_trace_upstream(TRACE_UPSTREAM)

try:
    from .quality_contract_broker import (
        QualityContract,
        QualityTier,
        select_minimum_byte_portfolio,
        select_quality_tier,
    )
    from .trace_deployment_v4_study import (
        SCREEN_DURATION,
        SCREEN_MAX_WORKFLOWS,
        clamp01,
        minimum_quality_optional_subset,
    )
    from .trace_factorized_signal_study import (
        TRACE_QUALITY_TARGET,
        profile_path,
        scaled_trace_workload,
    )
    from .three_signal_rule_study import balanced_evaluation_matrix
except ImportError:  # pragma: no cover - direct execution from this directory
    from quality_contract_broker import (
        QualityContract,
        QualityTier,
        select_minimum_byte_portfolio,
        select_quality_tier,
    )
    from trace_deployment_v4_study import (
        SCREEN_DURATION,
        SCREEN_MAX_WORKFLOWS,
        clamp01,
        minimum_quality_optional_subset,
    )
    from trace_factorized_signal_study import (
        TRACE_QUALITY_TARGET,
        profile_path,
        scaled_trace_workload,
    )
    from three_signal_rule_study import balanced_evaluation_matrix


PROTOCOL_VERSION = "2026-08-16.quality-contract-validation-audit-v1"
DEFAULT_PROFILES = ("trace_driven_v1", "trace_driven_v2")
DEFAULT_PENALTIES = (0.0, 0.25, 0.5, 1.0)
DEFAULT_SEED_BASE = 2_670_000
DEFAULT_SCENARIO_COUNT = 9
DEFAULT_EVALUATION_RUNS = 2
DEFAULT_FALLBACK_QUALITY_TARGET = 0.94


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def branch_ids(branches: Iterable[object]) -> Tuple[int, ...]:
    return tuple(sorted(int(branch.branch_index) for branch in branches))


def optional_contracts(optional_branches: Sequence[object]) -> List[QualityContract]:
    return [
        QualityContract(
            contract_id=int(branch.branch_index),
            byte_cost=float(branch.size),
            expected_utility=float(branch.expected_utility),
            selection_probability=float(branch.selection_probability),
        )
        for branch in optional_branches
    ]


def required_optional_utility(
    optional_branches: Sequence[object],
    template: str,
    quality_target: float,
) -> float:
    potential = point_optional_potential(optional_branches, template)
    if potential <= 1e-12:
        return 0.0
    required_fraction = clamp01(
        (quality_target - TRACE_UP.BASE_REQUIRED_QUALITY)
        / max(1e-12, 1.0 - TRACE_UP.BASE_REQUIRED_QUALITY)
    )
    return required_fraction * potential


def point_optional_potential(
    optional_branches: Sequence[object],
    template: str,
) -> float:
    retain_limit = TRACE_UP.JUDGE_RETAIN_LIMIT.get(template, len(optional_branches))
    return sum(
        sorted(
            (float(branch.expected_utility) for branch in optional_branches),
            reverse=True,
        )[:retain_limit]
    )


def maximum_supported_quality(
    contracts: Sequence[QualityContract],
    point_potential: float,
    retain_limit: int,
    uncertainty_penalty: float,
) -> Tuple[float, float]:
    maximum_lower_utility = sum(
        sorted(
            (
                contract.lower_utility(uncertainty_penalty)
                for contract in contracts
            ),
            reverse=True,
        )[:retain_limit]
    )
    if point_potential <= 1e-12:
        return maximum_lower_utility, 1.0
    retained_fraction = clamp01(maximum_lower_utility / point_potential)
    supported_quality = TRACE_UP.BASE_REQUIRED_QUALITY + (
        1.0 - TRACE_UP.BASE_REQUIRED_QUALITY
    ) * retained_fraction
    return maximum_lower_utility, supported_quality


def audit_workflow(
    workflow: object,
    uncertainty_penalties: Sequence[float],
    quality_target: float = TRACE_QUALITY_TARGET,
    fallback_quality_target: float = DEFAULT_FALLBACK_QUALITY_TARGET,
) -> List[Dict[str, object]]:
    if not TRACE_UP.BASE_REQUIRED_QUALITY <= fallback_quality_target <= quality_target:
        raise ValueError(
            "fallback quality target must lie between base quality and primary target"
        )
    optional = [branch for branch in workflow.branches if not branch.required]
    contracts = optional_contracts(optional)
    retain_limit = TRACE_UP.JUDGE_RETAIN_LIMIT.get(workflow.template, len(optional))
    required_utility = required_optional_utility(
        optional,
        workflow.template,
        quality_target,
    )
    fallback_required_utility = required_optional_utility(
        optional,
        workflow.template,
        fallback_quality_target,
    )
    point_potential = point_optional_potential(optional, workflow.template)
    v4_selected = minimum_quality_optional_subset(
        optional,
        workflow.template,
        quality_target,
        upstream=TRACE_UP,
    )
    v4_ids = branch_ids(v4_selected)
    v4_bytes = sum(float(branch.size) for branch in v4_selected)
    full_bytes = sum(float(branch.size) for branch in optional)

    rows: List[Dict[str, object]] = []
    for penalty in uncertainty_penalties:
        maximum_lower_utility, supported_quality = maximum_supported_quality(
            contracts,
            point_potential,
            retain_limit,
            float(penalty),
        )
        portfolio = select_minimum_byte_portfolio(
            contracts,
            required_utility,
            retain_limit,
            uncertainty_penalty=float(penalty),
        )
        negotiated = select_quality_tier(
            contracts,
            [
                QualityTier("primary", required_utility),
                QualityTier("degraded", fallback_required_utility),
            ],
            retain_limit,
            uncertainty_penalty=float(penalty),
        )
        negotiated_target = (
            quality_target
            if negotiated.granted_tier == "primary"
            else fallback_quality_target
            if negotiated.granted_tier == "degraded"
            else ""
        )
        selected_ids = tuple(sorted(item.contract_id for item in portfolio.contracts))
        exact_point_agreement = selected_ids == v4_ids and abs(
            portfolio.total_bytes - v4_bytes
        ) <= 1e-9
        if abs(float(penalty)) <= 1e-12 and not exact_point_agreement:
            raise AssertionError(
                f"point broker disagrees with V4 for workflow {workflow.workflow_id}: "
                f"{selected_ids} != {v4_ids}"
            )
        if portfolio.total_bytes + 1e-9 < v4_bytes:
            raise AssertionError(
                "a utility haircut produced fewer bytes than the point optimum"
            )
        rows.append(
            {
                "workflow_id": int(workflow.workflow_id),
                "template": str(workflow.template),
                "uncertainty_penalty": float(penalty),
                "optional_count": len(optional),
                "retain_limit": int(retain_limit),
                "required_utility": required_utility,
                "fallback_required_utility": fallback_required_utility,
                "point_potential_utility": point_potential,
                "maximum_lower_utility": maximum_lower_utility,
                "maximum_supported_quality": supported_quality,
                "quality_contract_shortfall": max(
                    0.0,
                    quality_target - supported_quality,
                ),
                "full_optional_bytes": full_bytes,
                "v4_point_count": len(v4_selected),
                "v4_point_bytes": v4_bytes,
                "v4_point_ids": ";".join(str(value) for value in v4_ids),
                "broker_selected_count": len(portfolio.contracts),
                "broker_admission_bytes": portfolio.total_bytes,
                "broker_selected_ids": ";".join(
                    str(value) for value in selected_ids
                ),
                "achieved_lower_utility": portfolio.achieved_lower_utility,
                "feasible": int(portfolio.feasible),
                "fallback_all_optional": int(not portfolio.feasible),
                "byte_premium_vs_point": portfolio.total_bytes - v4_bytes,
                "negotiated_tier": negotiated.granted_tier or "rejected",
                "negotiated_quality_target": negotiated_target,
                "negotiated_admission_bytes": negotiated.portfolio.total_bytes,
                "negotiated_primary": int(negotiated.granted_tier == "primary"),
                "negotiated_degraded": int(negotiated.degraded),
                "negotiated_rejected": int(not negotiated.feasible),
                "negotiated_savings_vs_fixed": (
                    portfolio.total_bytes - negotiated.portfolio.total_bytes
                ),
                "negotiated_premium_vs_point": (
                    negotiated.portfolio.total_bytes - v4_bytes
                ),
                "point_exact_agreement": (
                    int(exact_point_agreement)
                    if abs(float(penalty)) <= 1e-12
                    else ""
                ),
            }
        )
    return rows


def summarize_group(
    items: Sequence[Mapping[str, object]],
    dimensions: Mapping[str, object],
) -> Dict[str, object]:
    full_bytes = sum(float(row["full_optional_bytes"]) for row in items)
    point_bytes = sum(float(row["v4_point_bytes"]) for row in items)
    broker_bytes = sum(float(row["broker_admission_bytes"]) for row in items)
    feasible_count = sum(int(row["feasible"]) for row in items)
    agreement_values = [
        int(row["point_exact_agreement"])
        for row in items
        if row["point_exact_agreement"] != ""
    ]
    supported_qualities = sorted(
        float(row["maximum_supported_quality"]) for row in items
    )
    negotiated_bytes = sum(
        float(row["negotiated_admission_bytes"]) for row in items
    )
    return {
        **dimensions,
        "workflows": len(items),
        "full_optional_bytes": full_bytes,
        "v4_point_bytes": point_bytes,
        "broker_admission_bytes": broker_bytes,
        "savings_vs_full_bytes": full_bytes - broker_bytes,
        "savings_vs_full_ratio": (
            (full_bytes - broker_bytes) / full_bytes
            if full_bytes > 1e-12
            else 0.0
        ),
        "premium_vs_point_bytes": broker_bytes - point_bytes,
        "premium_vs_point_ratio": (
            (broker_bytes - point_bytes) / point_bytes
            if point_bytes > 1e-12
            else 0.0
        ),
        "negotiated_admission_bytes": negotiated_bytes,
        "negotiated_savings_vs_fixed_bytes": broker_bytes - negotiated_bytes,
        "negotiated_savings_vs_fixed_ratio": (
            (broker_bytes - negotiated_bytes) / broker_bytes
            if broker_bytes > 1e-12
            else 0.0
        ),
        "negotiated_premium_vs_point_bytes": negotiated_bytes - point_bytes,
        "negotiated_premium_vs_point_ratio": (
            (negotiated_bytes - point_bytes) / point_bytes
            if point_bytes > 1e-12
            else 0.0
        ),
        "negotiated_primary_fraction": statistics.mean(
            int(row["negotiated_primary"]) for row in items
        ),
        "negotiated_degraded_fraction": statistics.mean(
            int(row["negotiated_degraded"]) for row in items
        ),
        "negotiated_rejected_fraction": statistics.mean(
            int(row["negotiated_rejected"]) for row in items
        ),
        "mean_broker_bytes_per_workflow": statistics.mean(
            float(row["broker_admission_bytes"]) for row in items
        ),
        "mean_selected_contracts": statistics.mean(
            int(row["broker_selected_count"]) for row in items
        ),
        "feasible_fraction": feasible_count / len(items),
        "infeasible_fraction": 1.0 - feasible_count / len(items),
        "mean_maximum_supported_quality": statistics.mean(supported_qualities),
        "p05_maximum_supported_quality": interpolated_percentile(
            supported_qualities,
            0.05,
        ),
        "minimum_supported_quality": supported_qualities[0],
        "mean_quality_contract_shortfall": statistics.mean(
            float(row["quality_contract_shortfall"]) for row in items
        ),
        "point_exact_agreement_fraction": (
            statistics.mean(agreement_values) if agreement_values else ""
        ),
    }


def interpolated_percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("percentile probability must lie in [0, 1]")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * probability
    low = int(rank)
    high = min(len(ordered) - 1, low + 1)
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def summarize(rows: Sequence[Mapping[str, object]]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, float], List[Mapping[str, object]]] = {}
    for row in rows:
        key = (str(row["profile"]), float(row["uncertainty_penalty"]))
        groups.setdefault(key, []).append(row)
    return [
        summarize_group(
            items,
            {"profile": profile, "uncertainty_penalty": penalty},
        )
        for (profile, penalty), items in sorted(groups.items())
    ]


def summarize_by_template(
    rows: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    groups: Dict[Tuple[str, str, float], List[Mapping[str, object]]] = {}
    for row in rows:
        key = (
            str(row["profile"]),
            str(row["template"]),
            float(row["uncertainty_penalty"]),
        )
        groups.setdefault(key, []).append(row)
    return [
        summarize_group(
            items,
            {
                "profile": profile,
                "template": template,
                "uncertainty_penalty": penalty,
            },
        )
        for (profile, template, penalty), items in sorted(groups.items())
    ]


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    output_dir: Path,
    summaries: Sequence[Mapping[str, object]],
    template_summaries: Sequence[Mapping[str, object]],
    manifest: Mapping[str, object],
) -> None:
    lines = [
        "# 质量契约经纪人：V1/V2 验证集准入审计",
        "",
        "本实验只审计 optional 分支的准入组合，不运行网络事件调度器。表中的 bytes 是“若准入将产生的 optional 需求”，不是实际 served bytes、能耗或生产成本。",
        "",
        "## 结果",
        "",
        "| Profile | 不确定性折扣 | 工作流 | 准入 bytes | 相对 V4 溢价 | 相对全开节省 | 不可行率 | V4 精确一致率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        agreement = row["point_exact_agreement_fraction"]
        agreement_text = (
            f"{float(agreement):.2%}" if agreement != "" else "-"
        )
        lines.append(
            f"| {row['profile']} | {float(row['uncertainty_penalty']):.2f} | "
            f"{int(row['workflows'])} | {float(row['broker_admission_bytes']):.2f} | "
            f"{float(row['premium_vs_point_ratio']):+.2%} | "
            f"{float(row['savings_vs_full_ratio']):.2%} | "
            f"{float(row['infeasible_fraction']):.2%} | {agreement_text} |"
        )
    strongest_penalty = max(
        float(row["uncertainty_penalty"]) for row in summaries
    )
    lines += [
        "",
        f"## 最强折扣（{strongest_penalty:.2f}）的模板诊断",
        "",
        "| Profile | Template | 工作流 | 相对 V4 溢价 | 不可行率 | 95% 工作流可支持目标 | 最低可支持目标 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in template_summaries:
        if abs(float(row["uncertainty_penalty"]) - strongest_penalty) > 1e-12:
            continue
        lines.append(
            f"| {row['profile']} | {row['template']} | {int(row['workflows'])} | "
            f"{float(row['premium_vs_point_ratio']):+.2%} | "
            f"{float(row['infeasible_fraction']):.2%} | "
            f"{float(row['p05_maximum_supported_quality']):.4f} | "
            f"{float(row['minimum_supported_quality']):.4f} |"
        )
    lines += [
        "",
        f"## 分层契约候选（主目标 {float(manifest['quality_target']):.2f} / 降级目标 {float(manifest['fallback_quality_target']):.2f}）",
        "",
        "| Profile | 固定目标 bytes | 分层契约 bytes | 收回 bytes | 相对 V4 溢价 | 主契约 | 降级契约 | 拒绝 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        if abs(float(row["uncertainty_penalty"]) - strongest_penalty) > 1e-12:
            continue
        lines.append(
            f"| {row['profile']} | {float(row['broker_admission_bytes']):.2f} | "
            f"{float(row['negotiated_admission_bytes']):.2f} | "
            f"{float(row['negotiated_savings_vs_fixed_bytes']):.2f} "
            f"({float(row['negotiated_savings_vs_fixed_ratio']):.2%}) | "
            f"{float(row['negotiated_premium_vs_point_ratio']):+.2%} | "
            f"{float(row['negotiated_primary_fraction']):.2%} | "
            f"{float(row['negotiated_degraded_fraction']):.2%} | "
            f"{float(row['negotiated_rejected_fraction']):.2%} |"
        )
    lines += [
        "",
        "## 如何解释",
        "",
        "- 折扣为 `0` 时，新经纪人必须逐工作流、逐分支地与 V4 完全一致；否则实现不能进入模拟器。",
        "- 折扣增大后，准入 bytes 只会不变或上升。这是为不确定性留出的保险，不应伪装成资源优化。",
        "- `不可行` 表示即使回退为全部 optional 分支，折扣后的效用仍无法兑现原 0.95 契约；这时正确动作是显式降级、拒绝或重谈契约，而不是继续堆优先级。",
        "- `95% 工作流可支持目标` 是该组最大可支持质量上界的第 5 百分位，可作为分组契约协商的保守起点；它仍不是生产 SLO。",
        "- 分层契约只在主目标数学上不可行时降级；本轮 0.94 来自 validation 可行前沿，必须冻结后再到独立 split 确认，不能把它写成已验证 SLO。",
        "- Profile 总体差异必须结合模板组成解释；若某类模板集中不可行，先检查 retain limit 和效用标定，不能直接归因于链路资源。",
        "- 当前折扣是确定性敏感性压力测试，不是从样本估计出的置信区间。下一阶段必须用 calibration split 学习分服务类型的误差半径，再做 conformal/分布鲁棒校准。",
        "",
        "## 可复查性",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- Split：`{manifest['split']}`（脚本不提供 test 选项）",
        f"- 场景：{manifest['scenario_count']} 个均衡 smoke 场景 x {manifest['evaluation_runs']} runs/profile",
        f"- Seed：`{manifest['seed_rule']}`",
        f"- 不确定性折扣：`{manifest['uncertainty_penalties']}`",
        "- V1/V2 分开汇总，不把相关工作流当成独立统计显著性样本。",
    ]
    (output_dir / "QUALITY_CONTRACT_TRACE_AUDIT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_penalties(value: str) -> Tuple[float, ...]:
    penalties = tuple(
        float(item.strip()) for item in value.split(",") if item.strip()
    )
    if not penalties or any(value < 0.0 or value > 1.0 for value in penalties):
        raise argparse.ArgumentTypeError("penalties must be comma-separated values in [0, 1]")
    if not any(abs(value) <= 1e-12 for value in penalties):
        raise argparse.ArgumentTypeError("penalties must include 0 for the V4 invariant")
    return penalties


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=DEFAULT_PROFILES,
        default=list(DEFAULT_PROFILES),
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--penalties", type=parse_penalties, default=DEFAULT_PENALTIES)
    parser.add_argument("--scenario-count", type=int, default=DEFAULT_SCENARIO_COUNT)
    parser.add_argument("--evaluation-runs", type=int, default=DEFAULT_EVALUATION_RUNS)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    parser.add_argument(
        "--fallback-quality-target",
        type=float,
        default=DEFAULT_FALLBACK_QUALITY_TARGET,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "results" / "quality_contract_validation_audit_20260816",
    )
    return parser.parse_args()


def trace_scenarios() -> List[Tuple[str, float, float, float]]:
    return list(
        itertools.product(
            TRACE_UP.LOAD_CONFIG.keys(),
            (0.30, 0.65, 1.20),
            (0.45, 1.65),
            (0.72, 1.25),
        )
    )


def main() -> None:
    args = parse_args()
    if args.scenario_count < 1 or args.evaluation_runs < 1:
        raise ValueError("scenario count and evaluation runs must be positive")
    if not TRACE_UP.BASE_REQUIRED_QUALITY <= args.fallback_quality_target <= TRACE_QUALITY_TARGET:
        raise ValueError(
            "fallback quality target must lie between base quality and primary target"
        )
    matrix = balanced_evaluation_matrix(
        trace_scenarios(),
        args.scenario_count,
        seed=246_081,
    )
    workflow_rows: List[Dict[str, object]] = []
    profile_hashes: Dict[str, str] = {}
    for profile_index, profile in enumerate(args.profiles):
        trace_profile = profile_path(profile, args.data_root)
        profile_hashes[profile] = sha256(trace_profile)
        for evaluation_run in range(args.evaluation_runs):
            for scenario_index, scenario in enumerate(matrix):
                load, deadline_scale, optional_scale, capacity_scale = scenario
                seed = (
                    args.seed_base
                    + profile_index * 100_000
                    + evaluation_run * 10_000
                    + scenario_index
                )
                workflows = scaled_trace_workload(
                    seed,
                    load,
                    SCREEN_DURATION,
                    SCREEN_MAX_WORKFLOWS,
                    deadline_scale,
                    optional_scale,
                    profile,
                    trace_profile,
                    "validation",
                    upstream=TRACE_UP,
                )
                for workflow in workflows:
                    metadata = {
                        "profile": profile,
                        "split": "validation",
                        "evaluation_run": evaluation_run,
                        "scenario": scenario_index,
                        "seed": seed,
                        "load": load,
                        "deadline_scale": deadline_scale,
                        "optional_scale": optional_scale,
                        "capacity_scale": capacity_scale,
                        "source_record_id": workflow.source_record_id,
                    }
                    workflow_rows.extend(
                        {
                            **metadata,
                            **row,
                        }
                        for row in audit_workflow(
                            workflow,
                            args.penalties,
                            fallback_quality_target=args.fallback_quality_target,
                        )
                    )

    summaries = summarize(workflow_rows)
    template_summaries = summarize_by_template(workflow_rows)
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "split": "validation",
        "profiles": list(args.profiles),
        "profile_sha256": profile_hashes,
        "upstream_sha256": sha256(TRACE_UPSTREAM),
        "broker_sha256": sha256(ROOT / "quality_contract_broker.py"),
        "audit_sha256": sha256(Path(__file__).resolve()),
        "quality_target": TRACE_QUALITY_TARGET,
        "fallback_quality_target": args.fallback_quality_target,
        "uncertainty_penalties": list(args.penalties),
        "scenario_count": len(matrix),
        "evaluation_runs": args.evaluation_runs,
        "evaluation_matrix": matrix,
        "seed_rule": (
            f"{args.seed_base} + profile_index*100000 + "
            "evaluation_run*10000 + scenario_index"
        ),
        "workflow_penalty_rows": len(workflow_rows),
        "interpretation": (
            "deterministic uncertainty sensitivity; not a calibrated confidence bound"
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "workflow_contract_audit.csv", workflow_rows)
    write_csv(args.output_dir / "contract_summary.csv", summaries)
    write_csv(
        args.output_dir / "contract_template_summary.csv",
        template_summaries,
    )
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(args.output_dir, summaries, template_summaries, manifest)
    print(f"[done] quality-contract audit written to {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
