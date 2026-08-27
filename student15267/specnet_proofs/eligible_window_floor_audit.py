#!/usr/bin/env python3
"""Recompute per-workflow eligible-window floor statistics on frozen seeds."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

try:
    from . import proof_harness as h
    from .factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
        IdleEligibleFactorizedRule,
        meets_background_floor,
        run_eligible_policy,
    )
except ImportError:  # pragma: no cover
    import proof_harness as h
    from factorized_background_eligible_window_study import (
        BACKGROUND_FLOOR_TOLERANCE,
        IdleEligibleFactorizedRule,
        meets_background_floor,
        run_eligible_policy,
    )


PROTOCOL_VERSION = "2026-08-05.eligible-window-floor-audit-v1"
BACKGROUND_FLOOR = 0.20


def seed_base_from_rule(seed_rule: str) -> int:
    match = re.match(r"\s*(\d+)\s*\+\s*run", seed_rule)
    if match is None:
        raise ValueError(f"unsupported seed rule: {seed_rule}")
    return int(match.group(1))


def audit_rows(
    params: Mapping[str, float],
    matrix: Sequence[Sequence[object]],
    runs: int,
    seed_base: int,
) -> tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    cells: List[Dict[str, object]] = []
    workflows: List[Dict[str, object]] = []
    for run in range(runs):
        for scenario_index, raw_scenario in enumerate(matrix):
            scenario = (
                str(raw_scenario[0]),
                float(raw_scenario[1]),
                float(raw_scenario[2]),
                float(raw_scenario[3]),
            )
            seed = seed_base + run * 10_000 + scenario_index
            summary, metrics = run_eligible_policy(
                IdleEligibleFactorizedRule(params), scenario, seed
            )
            specs = h.scaled_workload(
                seed, scenario[0], 1800, 90, scenario[1], scenario[2]
            )
            totals = {
                spec.workflow_id: max(1.0, sum(spec.background_sizes))
                for spec in specs
            }
            ratios = []
            for record in summary["workflow_records"]:
                workflow_id = int(record["workflow_id"])
                ratio = float(record["background_bytes_served"]) / totals[workflow_id]
                ratios.append(ratio)
                workflows.append(
                    {
                        "run": run,
                        "scenario": scenario_index,
                        "seed": seed,
                        "workflow_id": workflow_id,
                        "action": record["action"],
                        "background_ratio": ratio,
                        "strict_floor_pass": int(ratio >= BACKGROUND_FLOOR),
                        "tolerant_floor_pass": int(meets_background_floor(ratio)),
                    }
                )
            cells.append(
                {
                    "run": run,
                    "scenario": scenario_index,
                    "seed": seed,
                    "load": scenario[0],
                    "deadline_scale": scenario[1],
                    "optional_scale": scenario[2],
                    "capacity_scale": scenario[3],
                    "workflow_count": len(ratios),
                    "strict_floor_fraction": statistics.mean(
                        ratio >= BACKGROUND_FLOOR for ratio in ratios
                    ),
                    "tolerant_floor_fraction": statistics.mean(
                        meets_background_floor(ratio) for ratio in ratios
                    ),
                    "minimum_background_ratio": min(ratios),
                    "maximum_floor_shortfall": max(
                        0.0, max(BACKGROUND_FLOOR - ratio for ratio in ratios)
                    ),
                    "reported_background_ratio": metrics["background_service_ratio"],
                }
            )
        print(f"[floor-audit] run {run + 1}/{runs}", flush=True)
    return cells, workflows


def write_report(
    output_dir: Path,
    cells: Sequence[Mapping[str, object]],
    workflows: Sequence[Mapping[str, object]],
) -> None:
    strict_fraction = statistics.mean(float(row["strict_floor_pass"]) for row in workflows)
    tolerant_fraction = statistics.mean(
        float(row["tolerant_floor_pass"]) for row in workflows
    )
    min_ratio = min(float(row["background_ratio"]) for row in workflows)
    max_shortfall = max(float(row["maximum_floor_shortfall"]) for row in cells)
    lines = [
        "# Eligible-window workflow floor 数值审计",
        "",
        "该审计使用已冻结确认集的相同 scenarios、seeds 与 full policy 重放，不运行消融、不选择参数，也不改变三项假设判定。它的唯一目的是区分真实的 20% floor 违例与二进制浮点舍入。",
        "",
        f"- 协议：`{PROTOCOL_VERSION}`",
        f"- Cell 数：{len(cells)}；workflow 数：{len(workflows)}。",
        f"- 原始 `ratio >= 0.20`：{strict_fraction:.6f}。",
        f"- 使用 `{BACKGROUND_FLOOR_TOLERANCE:.0e}` 容差后的 floor fraction：{tolerant_fraction:.6f}。",
        f"- 最小 ratio：{min_ratio:.17g}；最大绝对 shortfall：{max_shortfall:.3g}。",
        "",
        "## 结论",
        "",
        "- 若 tolerant fraction 为 1，严格比较中的未达标均为机器精度舍入，不能解释为公平性失败。",
        "- 此审计不把场景均值门升级为业务层面的长期逐请求 SLO；实际系统仍需为超时、TTL 与跨生命周期价值定义独立约束。",
    ]
    (output_dir / "ELIGIBLE_WINDOW_FLOOR_AUDIT.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.confirmation_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((source / "run_manifest.json").read_text(encoding="utf-8"))
    params = {
        key: float(value) for key, value in manifest["selected_params"].items()
    }
    seed_base = seed_base_from_rule(str(manifest["seed_rule"]))
    cells, workflows = audit_rows(
        params,
        manifest["evaluation_matrix"],
        int(manifest["runs"]),
        seed_base,
    )
    h.write_csv(output_dir / "floor_audit_cells.csv", cells)
    h.write_csv(output_dir / "floor_audit_workflows.csv", workflows)
    h.write_json(
        output_dir / "run_manifest.json",
        {
            "protocol_version": PROTOCOL_VERSION,
            "source_confirmation_dir": str(source.resolve()),
            "source_manifest_sha256": h.sha256(source / "run_manifest.json"),
            "seed_rule": manifest["seed_rule"],
            "cells": len(cells),
            "workflows": len(workflows),
            "floor_tolerance": BACKGROUND_FLOOR_TOLERANCE,
            "script_sha256": h.sha256(Path(__file__).resolve()),
        },
    )
    write_report(output_dir, cells, workflows)
    print(f"[done] results written to {output_dir.resolve()}", flush=True)


if __name__ == "__main__":
    main()
