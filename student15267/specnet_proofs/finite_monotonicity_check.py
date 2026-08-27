#!/usr/bin/env python3
"""Finite sanity check for the weighted-service monotonicity lemma."""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


@dataclass
class ToyFlow:
    remaining: float
    weight: float
    critical: bool


def serve_until_critical_done(
    critical: Sequence[Tuple[float, float]],
    optional: Sequence[Tuple[float, float]],
    capacity: float,
    max_epochs: int = 100,
) -> int:
    flows = [ToyFlow(size, weight, True) for size, weight in critical]
    flows.extend(ToyFlow(size, weight, False) for size, weight in optional)
    for epoch in range(1, max_epochs + 1):
        active = [flow for flow in flows if flow.remaining > 1e-12]
        if not active:
            return epoch - 1
        remaining_capacity = capacity
        candidates = active
        while candidates and remaining_capacity > 1e-12:
            total_weight = sum(flow.weight for flow in candidates)
            if total_weight <= 1e-12:
                break
            served_this_round = 0.0
            progressed = []
            for flow in candidates:
                share = remaining_capacity * flow.weight / total_weight
                served = min(flow.remaining, share)
                if served <= 1e-12:
                    continue
                flow.remaining -= served
                served_this_round += served
                progressed.append(flow)
            remaining_capacity -= served_this_round
            if served_this_round <= 1e-12 or not progressed:
                break
            candidates = [flow for flow in candidates if flow.remaining > 1e-12]
        if all(flow.remaining <= 1e-12 for flow in flows if flow.critical):
            return epoch
    raise RuntimeError("toy system did not finish within max_epochs")


def enumerate_cases() -> Iterable[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], float]]:
    sizes = (1.0, 2.0, 4.0)
    weights = (1.0, 2.0, 3.0)
    for capacity in (1.0, 2.0, 3.0):
        for critical_count in (1, 2, 3):
            for optional_count in (0, 1, 2):
                critical_options = itertools.product(itertools.product(sizes, weights), repeat=critical_count)
                for critical in critical_options:
                    optional_options = itertools.product(
                        itertools.product(sizes, weights), repeat=optional_count
                    )
                    for optional in optional_options:
                        yield list(critical), list(optional), capacity


def check() -> dict:
    deltas = []
    violations = []
    cases = 0
    for critical, optional, capacity in enumerate_cases():
        cases += 1
        with_optional = serve_until_critical_done(critical, optional, capacity)
        without_optional = serve_until_critical_done(critical, [], capacity)
        delta = without_optional - with_optional
        deltas.append(delta)
        if delta > 0:
            violations.append(
                {
                    "critical": critical,
                    "optional": optional,
                    "capacity": capacity,
                    "with_optional": with_optional,
                    "without_optional": without_optional,
                    "delta": delta,
                }
            )
    return {
        "cases": cases,
        "violations": len(violations),
        "max_delta_removed_minus_original": max(deltas),
        "min_delta_removed_minus_original": min(deltas),
        "mean_delta_removed_minus_original": statistics.mean(deltas),
        "violating_examples": violations[:10],
        "assumption": "single bottleneck, work-conserving weighted max-min, unchanged critical flows",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    result = check()
    out = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent / "results" / "finite_monotonicity_20260723"
    out.mkdir(parents=True, exist_ok=True)
    (out / "finite_monotonicity.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    report = [
        "# Finite monotonicity check",
        "",
        "在单瓶颈、work-conserving weighted max-min、关键流不变的假设下，枚举小规模关键/可选流，复现模拟器的逐轮服务。",
        "",
        f"- cases: {result['cases']}",
        f"- violations: {result['violations']}",
        f"- max delta (remove optional - original): {result['max_delta_removed_minus_original']}",
        f"- min delta: {result['min_delta_removed_minus_original']}",
        f"- mean delta: {result['mean_delta_removed_minus_original']:.6f}",
        "",
        "`violations=0` 只说明该有限枚举没有找到反例，不等于覆盖所有网络调度器、多瓶颈或动态到达过程。",
    ]
    (out / "FINITE_MONOTONICITY_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
