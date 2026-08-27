#!/usr/bin/env python3
"""Trace-driven confirmation for the frozen factorized three-signal mechanism.

This study deliberately keeps the synthetic proof and the trace-driven evidence
separate.  The control parameters were selected before this protocol from the
synthetic development study; no trace result is used to tune them.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple


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

try:
    from . import proof_harness as h
    from .factorized_signal_study import FactorizedSignalRule, verdict_rows
    from .pressure_definition_study import PressureSimulator
    from .three_signal_confirmation_study import (
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        paired_slice_units,
    )
    from .three_signal_rule_study import (
        analysis_rows,
        balanced_evaluation_matrix,
        paired_nonjoint_units,
    )
except ImportError:  # pragma: no cover - direct execution from this directory
    import proof_harness as h
    from factorized_signal_study import FactorizedSignalRule, verdict_rows
    from pressure_definition_study import PressureSimulator
    from three_signal_confirmation_study import (
        PRIMARY_SPECS,
        QUALITY_FLOOR,
        paired_slice_units,
    )
    from three_signal_rule_study import (
        analysis_rows,
        balanced_evaluation_matrix,
        paired_nonjoint_units,
    )


PROTOCOL_VERSION = "2026-08-06.trace-factorized-three-signal-v3"
PRESSURE_DEFINITION = "active_speculative_backlog"
FROZEN_PARAMS = {
    "congestion_critical_boost": 1.50,
    "congestion_optional_scale": 0.75,
    "slack_critical_boost": 2.00,
}
FROZEN_PARAMETER_SOURCE = "factorized_signal_confirm_v1_20260730"
TRACE_QUALITY_TARGET = 0.95
TRACE_QUALITY_HARD_FLOOR = 0.95
DEFAULT_OPTIONAL_COMPLETION_BOOST = 1.0
DEFAULT_TIGHT_OPTIONAL_COMPLETION_BOOST = 1.0
MECHANISM_VARIANT = "quality_safe_factorized_successor_v1"
PROFILE_NAMES = (
    "trace_driven_v1",
    "trace_driven_v2",
    "trace_driven_v3_candidate",
)
MODE_DEFAULTS = {
    "smoke": {
        "duration": 700,
        "max_workflows": 28,
        "max_time": 2600,
        "evaluation_runs": 2,
        "seed_base": 2_260_000,
        "scenario_count": 18,
    },
    "confirm": {
        "duration": 1800,
        "max_workflows": 90,
        "max_time": 6000,
        "evaluation_runs": 3,
        "seed_base": 2_360_000,
        "scenario_count": None,
    },
}


class TraceQualitySafeFactorizedRule(FactorizedSignalRule):
    """Add a state-independent completion guard for selected optional flows.

    The base guard is deliberately applied after the three experimental signal
    paths and observes only flow type.  A separate tight-workflow multiplier
    compensates the slack path's critical-flow boost so that it does not starve
    selected quality-bearing branches from the same workflow.
    """

    def __init__(
        self,
        params: Mapping[str, float],
        ablation: str = "full",
        seed: int = 0,
        optional_completion_boost: float = DEFAULT_OPTIONAL_COMPLETION_BOOST,
        tight_optional_completion_boost: float = (
            DEFAULT_TIGHT_OPTIONAL_COMPLETION_BOOST
        ),
    ) -> None:
        if optional_completion_boost < 1.0:
            raise ValueError("optional completion boost must be at least 1.0")
        if tight_optional_completion_boost < 1.0:
            raise ValueError("tight optional completion boost must be at least 1.0")
        super().__init__(params, ablation, seed)
        self.optional_completion_boost = float(optional_completion_boost)
        self.tight_optional_completion_boost = float(tight_optional_completion_boost)

    def flow_weight(self, flow, sim) -> float:
        weight = super().flow_weight(flow, sim)
        if flow.speculative and not flow.background:
            weight *= self.optional_completion_boost
            owner = sim.workflows.get(flow.workflow_id)
            state = getattr(owner, "observable_state", None) if owner is not None else None
            if (
                self.ablation != "no_slack"
                and state is not None
                and state[1] == "tight"
            ):
                weight *= self.tight_optional_completion_boost
        return weight


def profile_path(profile: str, data_root: Path) -> Path:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"unsupported trace profile: {profile}")
    path = data_root / "processed" / profile / "profile.json"
    if not path.is_file():
        raise FileNotFoundError(f"trace profile not found: {path}")
    return path


def scaled_trace_workload(
    workload_seed: int,
    load: str,
    duration: int,
    max_workflows: int,
    deadline_scale: float,
    optional_scale: float,
    profile: str,
    trace_profile: Path,
    split: str,
    upstream: object | None = None,
) -> List[object]:
    """Load a profile deterministically, then apply the frozen stress factors."""
    source_upstream = upstream or h.up
    specs = copy.deepcopy(
        source_upstream.generate_workload(
            workload_seed,
            load,
            duration,
            max_workflows,
            workload_profile=profile,
            phase=split,
            trace_profile_path=str(trace_profile),
        )
    )
    for workflow in specs:
        workflow.deadline *= deadline_scale
        for branch in workflow.branches:
            if not branch.required:
                branch.size *= optional_scale
        workflow.background_sizes = [
            size * optional_scale for size in workflow.background_sizes
        ]
    return specs


def run_once(
    policy: TraceQualitySafeFactorizedRule,
    scenario: Tuple[str, float, float, float],
    workload_seed: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    profile: str,
    trace_profile: Path,
    split: str,
) -> Dict[str, object]:
    load, deadline_scale, optional_scale, capacity_scale = scenario
    specs = scaled_trace_workload(
        workload_seed,
        load,
        duration,
        max_workflows,
        deadline_scale,
        optional_scale,
        profile,
        trace_profile,
        split,
    )
    simulator = PressureSimulator(
        specs,
        policy,
        load,
        workload_seed,
        duration,
        max_time,
        capacity_scale=capacity_scale,
        pressure_definition=PRESSURE_DEFINITION,
        quality_target=TRACE_QUALITY_TARGET,
        quality_hard_floor=TRACE_QUALITY_HARD_FLOOR,
        safety_guard=True,
    )
    summary = simulator.run()
    summary.update(
        {
            "deadline_scale": deadline_scale,
            "optional_scale": optional_scale,
            "capacity_scale": capacity_scale,
            "workload_profile": profile,
            "total_served_bytes": simulator.total_served,
            "total_capacity_bytes": simulator.total_capacity,
        }
    )
    return summary


def evaluate(
    matrix: Sequence[Tuple[str, float, float, float]],
    evaluation_runs: int,
    seed_base: int,
    duration: int,
    max_workflows: int,
    max_time: int,
    profile: str,
    trace_profile: Path,
    split: str,
    optional_completion_boost: float,
    tight_optional_completion_boost: float,
    scenario_offset: int = 0,
    evaluation_run_offset: int = 0,
) -> Tuple[List[Dict[str, object]], List[Dict[str, object]], Counter[str]]:
    units: List[Dict[str, object]] = []
    nonjoint_units: List[Dict[str, object]] = []
    action_counts: Counter[str] = Counter()
    for evaluation_run in range(
        evaluation_run_offset, evaluation_run_offset + evaluation_runs
    ):
        for scenario_index, scenario in enumerate(matrix, start=scenario_offset):
            workload_seed = seed_base + evaluation_run * 10_000 + scenario_index
            policies = {
                name: TraceQualitySafeFactorizedRule(
                    FROZEN_PARAMS,
                    name,
                    seed=workload_seed,
                    optional_completion_boost=optional_completion_boost,
                    tight_optional_completion_boost=tight_optional_completion_boost,
                )
                for name in ("full", "no_congestion", "no_slack", "no_pressure")
            }
            summaries = {
                name: run_once(
                    policy,
                    scenario,
                    workload_seed,
                    duration,
                    max_workflows,
                    max_time,
                    profile,
                    trace_profile,
                    split,
                )
                for name, policy in policies.items()
            }
            units.extend(
                paired_slice_units(
                    summaries,
                    replicate=0,
                    eval_run=evaluation_run,
                    scenario_index=scenario_index,
                    workload_seed=workload_seed,
                )
            )
            nonjoint_units.extend(
                paired_nonjoint_units(
                    summaries,
                    evaluation_run,
                    scenario_index,
                    workload_seed,
                )
            )
            action_counts.update(
                {
                    str(action): int(count)
                    for action, count in summaries["full"].get(
                        "action_counts", {}
                    ).items()
                }
            )
    return units, nonjoint_units, action_counts


def write_report(
    output_dir: Path,
    manifest: Mapping[str, object],
    analysis: Sequence[Mapping[str, object]],
    nonjoint_analysis: Sequence[Mapping[str, object]],
    verdicts: Sequence[Mapping[str, object]],
) -> None:
    broad = {
        str(row["hypothesis"]): row
        for row in analysis
        if int(row["primary_metric"])
    }
    nonjoint = {
        str(row["hypothesis"]): row
        for row in nonjoint_analysis
        if int(row["primary_metric"])
    }
    verdict = {str(row["claim"]): str(row["status"]) for row in verdicts}
    lines = [
        "# Trace-driven 因子化三信号确认",
        "",
        "本实验使用冻结的 trace profile，且不在测试 split 上重新搜索控制参数。",
        "它与既有 synthetic 三信号证据独立，不能合并计算显著性。",
        "",
        f"- 协议：`{manifest['protocol_version']}`",
        f"- Profile：`{manifest['workload_profile']}`",
        f"- Profile SHA-256：`{manifest['profile_sha256']}`",
        f"- 数据源 split：`{manifest['source_split']}`",
        f"- 冻结参数来源：`{manifest['frozen_parameter_source']}`",
        f"- 参数：`{manifest['frozen_params']}`",
        f"- 静态 optional-completion 护栏：`{manifest['optional_completion_boost']:.2f}`",
        f"- 紧急 workflow optional 补偿：`{manifest['tight_optional_completion_boost']:.2f}`",
        f"- 预测质量 guard：target/floor=`{manifest['quality_target']:.2f}`，开启",
        f"- 评估：{manifest['evaluation_scenarios']} 场景 × {manifest['evaluation_runs']} runs",
        f"- Seed：`{manifest['test_seed_rule']}`",
        "- 差值定义：消融策略 − 完整策略；主指标正值表示移除变量后变差。",
        "",
        "## 主结果",
        "",
        "| 假设 | 主指标 | Broad delta | 95% CI | Holm p | Nonjoint delta | 质量（Full/Ablation） | 判定 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for hypothesis, (_, _, _, metric) in PRIMARY_SPECS.items():
        row = broad[hypothesis]
        secondary = nonjoint[hypothesis]
        lines.append(
            f"| {hypothesis} | {metric} | "
            f"{float(row['mean_delta_ablation_minus_full']):+.4f} | "
            f"[{float(row['ci95_low']):+.4f}, {float(row['ci95_high']):+.4f}] | "
            f"{float(row['holm_adjusted_p']):.4g} | "
            f"{float(secondary['mean_delta_ablation_minus_full']):+.4f} | "
            f"{float(row['mean_full_quality']):.4f}/{float(row['mean_ablation_quality']):.4f} | "
            f"{verdict[hypothesis]} |"
        )
    lines += [
        "",
        "## 解释边界",
        "",
        "- `congestion` 控制全局调度；`slack` 控制 tight workflow 的关键流和选定质量分支；`active speculative backlog` 控制 source admission。",
        "- 这是真实数据校准的模板 workload；deadline、网络 telemetry、队列与反事实 action 仍是 simulator 变量。",
        "- 护栏参数只在 V3 validation 上选择；V3/V1/V2 test 不参与选择。`100×` 静态优先级仍需由资源尾部与能耗实验审计。",
        "- 若任一假设不支持，结果应作为 trace transfer 的负结果保留，不能通过 trace 数据再调参后回写本 confirmation。",
        "- 后续 background/TTL 实验必须在此 profile 与冻结参数上单独预注册资源尾部、bytes 和能耗代理 gate。",
    ]
    (output_dir / "TRACE_FACTORIZED_SIGNAL_REPORT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def merge_batches(input_dirs: Sequence[Path], output_dir: Path) -> None:
    """Combine non-overlapping scenario batches without re-running the simulator."""
    if len(input_dirs) < 2:
        raise ValueError("batch merge requires at least two input directories")
    manifests = []
    for input_dir in input_dirs:
        manifest_path = input_dir / "run_manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"batch manifest not found: {manifest_path}")
        manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
    invariant_keys = (
        "protocol_version",
        "mode",
        "workload_profile",
        "profile_sha256",
        "source_split",
        "upstream_sha256",
        "pressure_definition",
        "frozen_params",
        "optional_completion_boost",
        "tight_optional_completion_boost",
        "mechanism_variant",
        "test_seed_rule",
    )
    reference = manifests[0]
    for manifest in manifests[1:]:
        for key in invariant_keys:
            if manifest.get(key) != reference.get(key):
                raise ValueError(f"batch manifests disagree on {key}")

    units: List[Dict[str, str]] = []
    nonjoint_units: List[Dict[str, str]] = []
    seen_cells = set()
    for input_dir in input_dirs:
        batch_units = read_csv_rows(input_dir / "trace_confirmation_units.csv")
        batch_cells = {(row["eval_run"], row["scenario"]) for row in batch_units}
        overlap = seen_cells.intersection(batch_cells)
        if overlap:
            raise ValueError(f"batch scenario overlap detected: {sorted(overlap)[:3]}")
        seen_cells.update(batch_cells)
        units.extend(batch_units)
        nonjoint_units.extend(
            read_csv_rows(input_dir / "trace_nonjoint_confirmation_units.csv")
        )
    if not units or not nonjoint_units:
        raise ValueError("merged batches must contain broad and nonjoint units")

    analysis = analysis_rows(units)
    nonjoint_analysis = analysis_rows(nonjoint_units)
    verdicts = verdict_rows(analysis, nonjoint_analysis, str(reference["mode"]))
    action_counts: Counter[str] = Counter()
    for manifest in manifests:
        action_counts.update(
            {str(key): int(value) for key, value in manifest["full_action_counts"].items()}
        )
    indexed_scenarios = {}
    for manifest in manifests:
        indices = manifest["evaluated_scenario_indices"]
        for index, scenario in zip(indices, manifest["evaluation_matrix"]):
            if index in indexed_scenarios and indexed_scenarios[index] != scenario:
                raise ValueError(f"batch scenario definition mismatch at index {index}")
            indexed_scenarios[index] = scenario
    evaluation_runs = len({row["eval_run"] for row in units})
    manifest = dict(reference)
    manifest.update(
        {
            "harness_sha256": h.sha256(Path(__file__).resolve()),
            "evaluation_matrix": [
                indexed_scenarios[index] for index in sorted(indexed_scenarios)
            ],
            "evaluated_scenario_indices": sorted(indexed_scenarios),
            "evaluation_scenarios": len(indexed_scenarios),
            "evaluation_runs": evaluation_runs,
            "scenario_offset": "merged_nonoverlapping_batches",
            "full_action_counts": dict(action_counts),
            "merged_from": [str(path.resolve()) for path in input_dirs],
            "supported_claims": [
                row["claim"] for row in verdicts if row["status"] == "supported"
            ],
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    h.write_csv(output_dir / "trace_confirmation_units.csv", units)
    h.write_csv(output_dir / "trace_nonjoint_confirmation_units.csv", nonjoint_units)
    h.write_csv(output_dir / "trace_confirmation_analysis.csv", analysis)
    h.write_csv(output_dir / "trace_nonjoint_confirmation_analysis.csv", nonjoint_analysis)
    h.write_csv(output_dir / "claim_verdicts.csv", verdicts)
    h.write_json(output_dir / "run_manifest.json", manifest)
    write_report(output_dir, manifest, analysis, nonjoint_analysis, verdicts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=tuple(MODE_DEFAULTS), default="smoke")
    parser.add_argument("--profile", choices=PROFILE_NAMES, default="trace_driven_v3_candidate")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument(
        "--merge-input-dirs",
        nargs="+",
        type=Path,
        default=None,
        help="Merge non-overlapping batches instead of running new scenarios.",
    )
    parser.add_argument("--evaluation-runs", type=int, default=None)
    parser.add_argument(
        "--scenario-offset",
        type=int,
        default=0,
        help="Start index within the frozen balanced matrix; supports resumable batches.",
    )
    parser.add_argument(
        "--scenario-count",
        type=int,
        default=None,
        help="Optional batch size within the frozen balanced matrix.",
    )
    parser.add_argument(
        "--evaluation-run-offset",
        type=int,
        default=0,
        help="Start evaluation-run index; supports non-overlapping seed batches.",
    )
    parser.add_argument("--split", choices=("validation", "test"), default="test")
    parser.add_argument(
        "--optional-completion-boost",
        type=float,
        default=DEFAULT_OPTIONAL_COMPLETION_BOOST,
        help="State-independent priority multiplier for selected optional branches.",
    )
    parser.add_argument(
        "--tight-optional-completion-boost",
        type=float,
        default=DEFAULT_TIGHT_OPTIONAL_COMPLETION_BOOST,
        help="Slack-path compensation for selected optional branches of tight workflows.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.merge_input_dirs is not None:
        if args.output_dir is None:
            raise ValueError("--output-dir is required when merging batches")
        merge_batches(args.merge_input_dirs, args.output_dir)
        print(f"[done] merged results written to {args.output_dir.resolve()}", flush=True)
        return
    if not TRACE_UPSTREAM.is_file():
        raise FileNotFoundError(f"trace upstream snapshot not found: {TRACE_UPSTREAM}")
    settings = MODE_DEFAULTS[args.mode]
    trace_profile = profile_path(args.profile, args.data_root)
    all_scenarios = h.scenarios("smoke" if args.mode == "smoke" else "full")
    if settings["scenario_count"] is None:
        matrix = all_scenarios
    else:
        matrix = balanced_evaluation_matrix(
            all_scenarios,
            int(settings["scenario_count"]),
            seed=206_081,
        )
    frozen_matrix = list(matrix)
    if args.scenario_offset < 0 or args.scenario_offset >= len(frozen_matrix):
        raise ValueError("scenario offset must index the frozen evaluation matrix")
    if args.scenario_count is not None:
        if args.scenario_count < 1:
            raise ValueError("scenario count must be positive")
        matrix = frozen_matrix[
            args.scenario_offset : args.scenario_offset + args.scenario_count
        ]
    else:
        matrix = frozen_matrix[args.scenario_offset :]
    if not matrix:
        raise ValueError("scenario batch is empty")
    evaluation_runs = args.evaluation_runs or int(settings["evaluation_runs"])
    if evaluation_runs < 1:
        raise ValueError("evaluation runs must be positive")
    if args.evaluation_run_offset < 0:
        raise ValueError("evaluation-run offset must be non-negative")
    if args.optional_completion_boost < 1.0:
        raise ValueError("optional completion boost must be at least 1.0")
    if args.tight_optional_completion_boost < 1.0:
        raise ValueError("tight optional completion boost must be at least 1.0")
    output_dir = args.output_dir or (
        ROOT
        / "results"
        / (
            f"trace_factorized_signal_{args.profile}_{args.split}_"
            f"{args.mode}_boost{args.optional_completion_boost:g}_"
            f"tight{args.tight_optional_completion_boost:g}_20260806"
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[{args.split}] {args.profile}: {len(matrix)} scenarios x {evaluation_runs} runs "
        f"(optional boost={args.optional_completion_boost:g}, "
        f"tight compensation={args.tight_optional_completion_boost:g})",
        flush=True,
    )
    units, nonjoint_units, action_counts = evaluate(
        matrix,
        evaluation_runs,
        int(settings["seed_base"]),
        int(settings["duration"]),
        int(settings["max_workflows"]),
        int(settings["max_time"]),
        args.profile,
        trace_profile,
        args.split,
        args.optional_completion_boost,
        args.tight_optional_completion_boost,
        args.scenario_offset,
        args.evaluation_run_offset,
    )
    analysis = analysis_rows(units)
    nonjoint_analysis = analysis_rows(nonjoint_units)
    verdicts = verdict_rows(analysis, nonjoint_analysis, args.mode)

    source_splits = sorted(
        {
            str(getattr(spec, "source_split", "unknown"))
            for spec in scaled_trace_workload(
                int(settings["seed_base"]),
                "medium",
                int(settings["duration"]),
                int(settings["max_workflows"]),
                1.0,
                1.0,
                args.profile,
                trace_profile,
                args.split,
            )
        }
    )
    manifest = {
        "protocol_version": PROTOCOL_VERSION,
        "mode": args.mode,
        "workload_profile": args.profile,
        "trace_profile_path": str(trace_profile.resolve()),
        "profile_sha256": h.sha256(trace_profile),
        "source_split": args.split,
        "observed_source_splits": source_splits,
        "upstream_path": str(h.UPSTREAM_PATH),
        "upstream_sha256": h.sha256(h.UPSTREAM_PATH),
        "harness_sha256": h.sha256(Path(__file__).resolve()),
        "pressure_definition": PRESSURE_DEFINITION,
        "mechanism_variant": MECHANISM_VARIANT,
        "frozen_params": FROZEN_PARAMS,
        "frozen_parameter_source": FROZEN_PARAMETER_SOURCE,
        "optional_completion_boost": args.optional_completion_boost,
        "tight_optional_completion_boost": args.tight_optional_completion_boost,
        "optional_completion_boost_selection": (
            "validation_calibrated"
            if args.split == "test" and args.optional_completion_boost != 1.0
            else "validation_search"
            if args.optional_completion_boost != 1.0
            else "none"
        ),
        "evaluation_matrix": matrix,
        "frozen_evaluation_matrix": frozen_matrix,
        "evaluated_scenario_indices": list(
            range(args.scenario_offset, args.scenario_offset + len(matrix))
        ),
        "scenario_offset": args.scenario_offset,
        "evaluation_run_offset": args.evaluation_run_offset,
        "evaluation_scenarios": len(matrix),
        "evaluation_runs": evaluation_runs,
        "test_seed_rule": f"{settings['seed_base']} + eval_run*10000 + scenario_index",
        "quality_floor": QUALITY_FLOOR,
        "quality_target": TRACE_QUALITY_TARGET,
        "quality_hard_floor": TRACE_QUALITY_HARD_FLOOR,
        "safety_guard": True,
        "full_action_counts": dict(action_counts),
        "supported_claims": [
            row["claim"] for row in verdicts if row["status"] == "supported"
        ],
    }
    h.write_csv(output_dir / "trace_confirmation_units.csv", units)
    h.write_csv(output_dir / "trace_nonjoint_confirmation_units.csv", nonjoint_units)
    h.write_csv(output_dir / "trace_confirmation_analysis.csv", analysis)
    h.write_csv(output_dir / "trace_nonjoint_confirmation_analysis.csv", nonjoint_analysis)
    h.write_csv(output_dir / "claim_verdicts.csv", verdicts)
    h.write_json(output_dir / "run_manifest.json", manifest)
    write_report(output_dir, manifest, analysis, nonjoint_analysis, verdicts)
    print(f"[done] results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
