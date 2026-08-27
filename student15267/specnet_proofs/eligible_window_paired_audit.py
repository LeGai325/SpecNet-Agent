#!/usr/bin/env python3
"""Post-hoc paired audit of eligible-window and original-semantics cells."""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

try:
    from . import proof_harness as h
except ImportError:  # pragma: no cover
    import proof_harness as h


PROTOCOL_VERSION = "2026-08-02.eligible-window-paired-audit-v1"
METRICS = (
    "p99_latency",
    "deadline_miss_ratio",
    "waste",
    "quality",
    "normalized_latency",
    "background_service_ratio",
    "link_utilization",
)


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def paired_rows(
    eligible: Sequence[Mapping[str, object]],
    reference: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    indexed = {
        (int(row["run"]), int(row["scenario"])): row for row in reference
    }
    output = []
    for metric_index, metric in enumerate(METRICS):
        values = []
        for row in eligible:
            key = (int(row["run"]), int(row["scenario"]))
            if key not in indexed:
                continue
            values.append(
                (
                    key[1],
                    float(row[metric]) - float(indexed[key][metric]),
                )
            )
        low, high = h.stratified_bootstrap_ci(
            values, seed=292_000 + metric_index
        )
        output.append(
            {
                "metric": metric,
                "direction": "eligible_minus_original",
                "paired_cells": len(values),
                "scenario_strata": len({scenario for scenario, _ in values}),
                "mean_delta": h.stratified_mean(values),
                "ci95_low": low,
                "ci95_high": high,
            }
        )
    return output


def write_report(
    out: Path,
    rows: Sequence[Mapping[str, object]],
    drain_mean: float,
) -> None:
    lines = [
        "# Eligible-window 与原语义配对审计",
        "",
        "该审计在不重跑、不调参的前提下，对确认集同一 run/scenario 的 cells 做场景分层 bootstrap。正值表示 eligible-window 高于原语义。",
        "",
        f"- 协议：`{PROTOCOL_VERSION}`",
        f"- 平均 post-foreground drain：{drain_mean:.3f} epochs。",
        "",
        "| Metric | Mean delta | 95% CI |",
        "|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['metric']} | {float(row['mean_delta']):+.6f} | "
            f"[{float(row['ci95_low']):+.6f}, {float(row['ci95_high']):+.6f}] |"
        )
    lines += [
        "",
        "## 解释",
        "",
        "- p99、miss 与 normalized latency 的差异用于核查前台非劣性；background 的正差异是目标效果。",
        "- waste、quality 或 utilization 的任何变化均保留，不因 background 门通过而省略。",
        "- 这是确认完成后的诊断，不改变预先冻结的通过判定。",
    ]
    (out / "ELIGIBLE_WINDOW_PAIRED_AUDIT.md").write_text(
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
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = read_rows(source / "eligible_window_cells.csv")
    reference = read_rows(source / "original_semantics_reference_cells.csv")
    rows = paired_rows(eligible, reference)
    drain_mean = statistics.mean(
        float(row["post_foreground_drain_time"]) for row in eligible
    )
    h.write_csv(out / "paired_semantic_comparisons.csv", rows)
    h.write_json(
        out / "run_manifest.json",
        {
            "protocol_version": PROTOCOL_VERSION,
            "source_confirmation_dir": str(source.resolve()),
            "source_manifest_sha256": h.sha256(source / "run_manifest.json"),
            "paired_cells": len(eligible),
            "scenario_strata": len({int(row["scenario"]) for row in eligible}),
            "script_sha256": h.sha256(Path(__file__).resolve()),
        },
    )
    write_report(out, rows, drain_mean)
    print(f"[done] results written to {out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
