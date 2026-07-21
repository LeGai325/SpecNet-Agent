
#!/usr/bin/env python3
"""Analyze controller-state ablations and deterministic proxy baselines.

The script consumes an experiment directory produced by
``specnet_agent_experiment.py``. Learned policies are summarized across
training seeds after averaging evaluation runs. Proxy baselines are summarized
across evaluation runs because they have no training seed.
"""

from __future__ import annotations

import argparse
import ast
import csv
import html
import json
import os
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Sequence, Tuple


ACTIONS = ["full", "moderate", "recovery", "conservative", "critical_only"]
ACTION_COLOR = {
    "full": "#16a34a",
    "moderate": "#2563eb",
    "recovery": "#0d9488",
    "conservative": "#f59e0b",
    "critical_only": "#dc2626",
}
VARIANTS = [
    ("full", "Full", "learned_state_controller"),
    ("congestion_only", "Congestion only", "learned_state_ablation"),
    ("no_slack", "No slack", "learned_state_ablation"),
    ("no_spec_pressure", "No spec pressure", "learned_state_ablation"),
    ("no_source_control", "No source control", "proxy_baseline"),
    ("no_learning", "No learning", "proxy_baseline"),
]
VARIANT_LABEL = {variant: label for variant, label, _ in VARIANTS}
VARIANT_TYPE = {variant: kind for variant, _, kind in VARIANTS}
VARIANT_ORDER = {variant: index for index, (variant, _, _) in enumerate(VARIANTS)}
VARIANT_COLOR = {
    "full": "#059669",
    "congestion_only": "#2563eb",
    "no_slack": "#7c3aed",
    "no_spec_pressure": "#0891b2",
    "no_source_control": "#ea580c",
    "no_learning": "#dc2626",
}
LEARNED_VARIANTS = {"full", "congestion_only", "no_slack", "no_spec_pressure"}
PROXY_POLICY_VARIANT = {
    "critical_path_only": "no_source_control",
    "rule_balanced": "no_learning",
}
METRICS = [
    ("p99_latency", "p99 latency"),
    ("avg_quality", "average quality"),
    ("wasted_speculative_bytes_per_workflow", "wasted speculative bytes / workflow"),
    ("deadline_miss_ratio", "deadline miss ratio"),
]


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: str, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def scale(value: float, src_min: float, src_max: float, dst_min: float, dst_max: float) -> float:
    if abs(src_max - src_min) < 1e-12:
        return (dst_min + dst_max) / 2
    return dst_min + (value - src_min) * (dst_max - dst_min) / (src_max - src_min)


def svg_text(
    x: float,
    y: float,
    text: str,
    size: int = 12,
    anchor: str = "start",
    weight: str = "400",
    fill: str = "#0f172a",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="{fill}">'
        f"{html.escape(text)}</text>"
    )


def metric_label(metric: str, value: float) -> str:
    if metric == "deadline_miss_ratio":
        return f"{100 * value:.1f}%"
    if metric == "avg_quality":
        return f"{value:.3f}"
    return f"{value:.1f}"


def delta_label(metric: str, value: float) -> str:
    if metric == "deadline_miss_ratio":
        return f"{100 * value:+.1f} pp"
    if metric == "avg_quality":
        return f"{value:+.3f}"
    return f"{value:+.1f}"


def int_text(value: str) -> str:
    return str(int(float(value))) if value != "" else ""


def variant_for_row(row: Dict[str, str]) -> str:
    controller_variant = row.get("controller_variant", "")
    if controller_variant in LEARNED_VARIANTS:
        return controller_variant
    return PROXY_POLICY_VARIANT.get(row.get("policy", ""), "")


def point_group_key(row: Dict[str, str]) -> Tuple[str, str, str]:
    variant = variant_for_row(row)
    if variant in LEARNED_VARIANTS:
        unit_id = f"train_seed:{int_text(row.get('train_seed', ''))}"
    else:
        unit_id = f"eval_run:{int_text(row.get('run', ''))}"
    return variant, row["policy"], unit_id


def extract_point_rows(
    summary_rows: Sequence[Dict[str, str]],
    workflow_rows: Sequence[Dict[str, str]],
    load: str,
) -> List[Dict[str, object]]:
    grouped_summaries: Dict[Tuple[str, str, str], List[Dict[str, str]]] = defaultdict(list)
    grouped_actions: Dict[Tuple[str, str, str], Counter[str]] = defaultdict(Counter)

    for row in summary_rows:
        variant = variant_for_row(row)
        if row["load"] == load and variant:
            grouped_summaries[point_group_key(row)].append(row)
    for row in workflow_rows:
        variant = variant_for_row(row)
        if row["load"] == load and variant:
            grouped_actions[point_group_key(row)][row["action"]] += 1

    points: List[Dict[str, object]] = []
    for (variant, policy, unit_id), items in grouped_summaries.items():
        first = items[0]
        learned = variant in LEARNED_VARIANTS
        action_counts = grouped_actions[(variant, policy, unit_id)]
        total_actions = sum(action_counts.values())
        point: Dict[str, object] = {
            "load": load,
            "variant": variant,
            "variant_label": VARIANT_LABEL[variant],
            "comparison_type": VARIANT_TYPE[variant],
            "source_policy": policy,
            "state_features": first.get("state_features", ""),
            "quality_weight": first.get("quality_weight", ""),
            "train_seed": int_text(first.get("train_seed", "")),
            "eval_seed": int_text(first.get("eval_seed", "")),
            "unit_id": unit_id,
            "variation_source": "train_seed" if learned else "eval_run",
            "runs": len(items),
        }
        for metric, _ in METRICS:
            point[metric] = mean([float(item[metric]) for item in items])

        point["total_actions"] = total_actions
        for action in ACTIONS:
            count = action_counts[action]
            point[f"{action}_count"] = count
            point[f"{action}_pct"] = count / total_actions if total_actions else 0.0
        points.append(point)

    points.sort(key=lambda row: (VARIANT_ORDER[str(row["variant"])], str(row["unit_id"])))
    return points


def aggregate_points(point_rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in point_rows:
        grouped[(str(row["load"]), str(row["variant"]))].append(row)

    rows: List[Dict[str, object]] = []
    for (load, variant), items in grouped.items():
        seeds = sorted({int(row["train_seed"]) for row in items if row["train_seed"] != ""})
        out: Dict[str, object] = {
            "load": load,
            "variant": variant,
            "variant_label": VARIANT_LABEL[variant],
            "comparison_type": VARIANT_TYPE[variant],
            "state_features": items[0]["state_features"],
            "quality_weight": items[0]["quality_weight"],
            "sample_count": len(items),
            "variation_source": items[0]["variation_source"],
            "train_seeds": ",".join(str(seed) for seed in seeds),
            "eval_seed": items[0]["eval_seed"],
        }
        for metric, _ in METRICS:
            values = [float(item[metric]) for item in items]
            out[f"{metric}_mean"] = mean(values)
            out[f"{metric}_std"] = stdev(values)
            out[f"{metric}_min"] = min(values)
            out[f"{metric}_max"] = max(values)
        for action in ACTIONS:
            values = [float(item[f"{action}_pct"]) for item in items]
            out[f"{action}_pct_mean"] = mean(values)
            out[f"{action}_pct_std"] = stdev(values)
        rows.append(out)
    rows.sort(key=lambda row: (str(row["load"]), VARIANT_ORDER[str(row["variant"])]))
    return rows


def paired_delta_rows(
    summary_rows: Sequence[Dict[str, str]],
    loads: Sequence[str],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for load in loads:
        selected = [row for row in summary_rows if row["load"] == load and variant_for_row(row)]
        full_by_seed_run: Dict[Tuple[int, int], Dict[str, str]] = {}
        comparison_by_variant_seed_run: Dict[Tuple[str, int, int], Dict[str, str]] = {}
        proxy_by_variant_run: Dict[Tuple[str, int], Dict[str, str]] = {}
        for row in selected:
            variant = variant_for_row(row)
            run = int(float(row["run"]))
            if variant == "full":
                full_by_seed_run[(int(float(row["train_seed"])), run)] = row
            elif variant in LEARNED_VARIANTS:
                comparison_by_variant_seed_run[(variant, int(float(row["train_seed"])), run)] = row
            else:
                proxy_by_variant_run[(variant, run)] = row

        train_seeds = sorted({seed for seed, _ in full_by_seed_run})
        for variant, _, _ in VARIANTS[1:]:
            for train_seed in train_seeds:
                pairs = []
                for (seed, run), full_row in full_by_seed_run.items():
                    if seed != train_seed:
                        continue
                    if variant in LEARNED_VARIANTS:
                        comparison = comparison_by_variant_seed_run.get((variant, seed, run))
                    else:
                        comparison = proxy_by_variant_run.get((variant, run))
                    if comparison is not None:
                        pairs.append((full_row, comparison))
                if not pairs:
                    continue
                out: Dict[str, object] = {
                    "load": load,
                    "variant": variant,
                    "variant_label": VARIANT_LABEL[variant],
                    "comparison_type": VARIANT_TYPE[variant],
                    "train_seed": train_seed,
                    "eval_seed": int_text(pairs[0][0].get("eval_seed", "")),
                    "runs": len(pairs),
                }
                for metric, _ in METRICS:
                    full_values = [float(full_row[metric]) for full_row, _ in pairs]
                    variant_values = [float(comparison[metric]) for _, comparison in pairs]
                    out[f"{metric}_full"] = mean(full_values)
                    out[f"{metric}_variant"] = mean(variant_values)
                    out[f"{metric}_delta"] = mean(
                        [variant_value - full_value for full_value, variant_value in zip(full_values, variant_values)]
                    )
                rows.append(out)
    rows.sort(
        key=lambda row: (
            str(row["load"]),
            VARIANT_ORDER[str(row["variant"])],
            int(row["train_seed"]),
        )
    )
    return rows


def aggregate_deltas(delta_rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in delta_rows:
        grouped[(str(row["load"]), str(row["variant"]))].append(row)
    rows: List[Dict[str, object]] = []
    for (load, variant), items in grouped.items():
        out: Dict[str, object] = {
            "load": load,
            "variant": variant,
            "variant_label": VARIANT_LABEL[variant],
            "comparison_type": VARIANT_TYPE[variant],
            "seed_count": len(items),
            "train_seeds": ",".join(str(item["train_seed"]) for item in items),
            "eval_seed": items[0]["eval_seed"],
        }
        for metric, _ in METRICS:
            values = [float(item[f"{metric}_delta"]) for item in items]
            out[f"{metric}_delta_mean"] = mean(values)
            out[f"{metric}_delta_std"] = stdev(values)
            out[f"{metric}_delta_min"] = min(values)
            out[f"{metric}_delta_max"] = max(values)
        rows.append(out)
    rows.sort(key=lambda row: (str(row["load"]), VARIANT_ORDER[str(row["variant"])]))
    return rows


def extract_state_coverage(model_path: str) -> List[Dict[str, object]]:
    with open(model_path, "r", encoding="utf-8") as f:
        bundle = json.load(f)
    rows: List[Dict[str, object]] = []
    for policy, entry in bundle["policies"].items():
        model = entry["model"]
        features = list(model["state_features"])
        visited_states = []
        updates = 0
        for state_text, action_counts in model["counts"].items():
            state_updates = sum(int(count) for count in action_counts.values())
            if state_updates <= 0:
                continue
            visited_states.append(ast.literal_eval(state_text))
            updates += state_updates
        for index, feature in enumerate(features):
            buckets = sorted({str(state[index]) for state in visited_states})
            rows.append(
                {
                    "policy": policy,
                    "controller_variant": entry["controller_variant"],
                    "state_features": ",".join(features),
                    "quality_weight": entry["quality_weight"],
                    "train_seed": entry["train_seed"],
                    "feature": feature,
                    "bucket_count": len(buckets),
                    "buckets": ",".join(buckets),
                    "visited_states": len(visited_states),
                    "training_updates": updates,
                    "coverage_ok": len(buckets) >= 2,
                }
            )
    rows.sort(
        key=lambda row: (
            int(row["train_seed"]),
            VARIANT_ORDER[str(row["controller_variant"])],
            str(row["feature"]),
        )
    )
    return rows


def metric_range(values: Sequence[float], metric: str, include_zero: bool = False) -> Tuple[float, float]:
    y_min, y_max = min(values), max(values)
    if include_zero:

        y_min = min(0.0, y_min)
        y_max = max(0.0, y_max)
    elif metric == "deadline_miss_ratio":
        y_min = 0.0
    elif metric == "avg_quality":
        y_min = max(0.75, y_min - 0.01)
        y_max = min(1.01, y_max + 0.006)
    margin = (y_max - y_min) * 0.15
    if margin < 1e-9:
        margin = max(0.01 if metric in {"avg_quality", "deadline_miss_ratio"} else 1.0, abs(y_max) * 0.05)
    if include_zero:
        return y_min - margin, y_max + margin
    return max(0.0, y_min - margin), y_max + margin


def variant_label_parts(variant: str) -> Tuple[str, str]:
    labels = {
        "full": ("Full", ""),
        "congestion_only": ("Congestion", "only"),
        "no_slack": ("No", "slack"),
        "no_spec_pressure": ("No spec", "pressure"),
        "no_source_control": ("No source", "control"),
        "no_learning": ("No", "learning"),
    }
    return labels[variant]


def draw_metric_panel(
    point_rows: Sequence[Dict[str, object]],
    aggregate_rows: Sequence[Dict[str, object]],
    metric: str,
    title: str,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    pad_l, pad_r, pad_t, pad_b = 68.0, 20.0, 34.0, 60.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    variants = [variant for variant, _, _ in VARIANTS]
    x_by_variant = {
        variant: scale(index, 0, len(variants) - 1, plot_x0 + 14, plot_x1 - 14)
        for index, variant in enumerate(variants)
    }
    values = [float(row[metric]) for row in point_rows]
    y_min, y_max = metric_range(values, metric)

    def y_for(value: float) -> float:
        return scale(value, y_min, y_max, plot_y1, plot_y0)

    parts = [svg_text(x0 + width / 2, y0 + 18, title, 13, "middle", "700")]
    for index in range(4):
        tick = y_min + (y_max - y_min) * index / 3
        yy = y_for(tick)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 7, yy + 3, metric_label(metric, tick), 9, "end"))
    grouped_points: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in point_rows:
        grouped_points[str(row["variant"])].append(row)
    for variant in variants:
        xx = x_by_variant[variant]
        label_a, label_b = variant_label_parts(variant)
        parts.append(svg_text(xx, plot_y1 + 17, label_a, 9, "middle", "700"))
        if label_b:
            parts.append(svg_text(xx, plot_y1 + 30, label_b, 9, "middle", "700"))
        points = grouped_points[variant]
        for index, row in enumerate(points):
            jitter = (index - (len(points) - 1) / 2) * min(5.0, 24.0 / max(1, len(points)))
            parts.append(
                f'<circle cx="{xx + jitter:.1f}" cy="{y_for(float(row[metric])):.1f}" r="3.0" '
                f'fill="{VARIANT_COLOR[variant]}" fill-opacity="0.45" stroke="{VARIANT_COLOR[variant]}" stroke-width="0.7"/>'
            )
    for row in aggregate_rows:
        variant = str(row["variant"])
        xx = x_by_variant[variant]
        y_low = y_for(float(row[f"{metric}_min"]))
        y_high = y_for(float(row[f"{metric}_max"]))
        y_mean = y_for(float(row[f"{metric}_mean"]))
        color = VARIANT_COLOR[variant]
        parts.append(f'<line x1="{xx:.1f}" y1="{y_high:.1f}" x2="{xx:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="1.5"/>')
        parts.append(f'<circle cx="{xx:.1f}" cy="{y_mean:.1f}" r="5.0" fill="{color}" stroke="#ffffff" stroke-width="1.2"/>')
        parts.append(svg_text(xx + 7, y_mean - 5, metric_label(metric, float(row[f"{metric}_mean"])), 8))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    return "\n".join(parts)


def draw_metrics_svg(
    point_rows: Sequence[Dict[str, object]],
    aggregate_rows: Sequence[Dict[str, object]],
    output_path: str,
    load: str,
) -> None:
    width, height = 1200, 690
    panel_w, panel_h = 560, 275
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>Controller ablation metrics for {html.escape(load)} load</title>",
        "<desc>Four metric panels compare four learned controller states with no-source-control and no-learning proxy baselines.</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Controller ablation metrics ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "Learned variants vary across train seeds; proxy baselines vary across evaluation runs.", 11, "middle"),
        draw_metric_panel(point_rows, aggregate_rows, "p99_latency", "p99 latency", 28, 76, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "avg_quality", "average quality", 612, 76, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "wasted_speculative_bytes_per_workflow", "wasted speculative bytes / workflow", 28, 376, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "deadline_miss_ratio", "deadline miss ratio", 612, 376, panel_w, panel_h),
        "</svg>",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def draw_delta_panel(
    delta_rows: Sequence[Dict[str, object]],
    aggregate_rows: Sequence[Dict[str, object]],
    metric: str,
    title: str,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    pad_l, pad_r, pad_t, pad_b = 72.0, 20.0, 34.0, 60.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    variants = [variant for variant, _, _ in VARIANTS if variant != "full"]
    x_by_variant = {
        variant: scale(index, 0, len(variants) - 1, plot_x0 + 14, plot_x1 - 14)
        for index, variant in enumerate(variants)
    }
    values = [float(row[f"{metric}_delta"]) for row in delta_rows]
    y_min, y_max = metric_range(values, metric, include_zero=True)

    def y_for(value: float) -> float:
        return scale(value, y_min, y_max, plot_y1, plot_y0)

    parts = [svg_text(x0 + width / 2, y0 + 18, title, 13, "middle", "700")]
    for index in range(4):
        tick = y_min + (y_max - y_min) * index / 3
        yy = y_for(tick)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 7, yy + 3, delta_label(metric, tick), 9, "end"))
    zero_y = y_for(0.0)
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{zero_y:.1f}" x2="{plot_x1:.1f}" y2="{zero_y:.1f}" stroke="#64748b" stroke-width="1.2" stroke-dasharray="4 3"/>')
    grouped_points: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in delta_rows:
        grouped_points[str(row["variant"])].append(row)
    for variant in variants:
        xx = x_by_variant[variant]
        label_a, label_b = variant_label_parts(variant)
        parts.append(svg_text(xx, plot_y1 + 17, label_a, 9, "middle", "700"))
        if label_b:
            parts.append(svg_text(xx, plot_y1 + 30, label_b, 9, "middle", "700"))
        points = grouped_points[variant]
        for index, row in enumerate(points):
            jitter = (index - (len(points) - 1) / 2) * 4.0
            parts.append(
                f'<circle cx="{xx + jitter:.1f}" cy="{y_for(float(row[f"{metric}_delta"])):.1f}" r="3.2" '
                f'fill="{VARIANT_COLOR[variant]}" fill-opacity="0.45" stroke="{VARIANT_COLOR[variant]}" stroke-width="0.7"/>'
            )
    for row in aggregate_rows:
        variant = str(row["variant"])
        xx = x_by_variant[variant]
        y_low = y_for(float(row[f"{metric}_delta_min"]))
        y_high = y_for(float(row[f"{metric}_delta_max"]))
        y_mean = y_for(float(row[f"{metric}_delta_mean"]))
        color = VARIANT_COLOR[variant]
        parts.append(f'<line x1="{xx:.1f}" y1="{y_high:.1f}" x2="{xx:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="1.5"/>')
        parts.append(f'<circle cx="{xx:.1f}" cy="{y_mean:.1f}" r="5.0" fill="{color}" stroke="#ffffff" stroke-width="1.2"/>')
        parts.append(svg_text(xx + 7, y_mean - 5, delta_label(metric, float(row[f"{metric}_delta_mean"])), 8))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    return "\n".join(parts)


def draw_deltas_svg(
    delta_rows: Sequence[Dict[str, object]],
    aggregate_rows: Sequence[Dict[str, object]],

    output_path: str,
    load: str,
) -> None:
    width, height = 1200, 690
    panel_w, panel_h = 560, 275
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>Controller ablation deltas relative to full for {html.escape(load)} load</title>",
        "<desc>Four panels show each ablation minus the full controller, paired by training seed and evaluation workload.</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Paired deltas relative to full ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "Values are variant minus full; zero means no observed effect.", 11, "middle"),
        draw_delta_panel(delta_rows, aggregate_rows, "p99_latency", "p99 latency delta", 28, 76, panel_w, panel_h),
        draw_delta_panel(delta_rows, aggregate_rows, "avg_quality", "average quality delta", 612, 76, panel_w, panel_h),
        draw_delta_panel(delta_rows, aggregate_rows, "wasted_speculative_bytes_per_workflow", "waste delta / workflow", 28, 376, panel_w, panel_h),
        draw_delta_panel(delta_rows, aggregate_rows, "deadline_miss_ratio", "deadline miss delta", 612, 376, panel_w, panel_h),
        "</svg>",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def draw_action_mix_svg(
    aggregate_rows: Sequence[Dict[str, object]],
    output_path: str,
    load: str,
) -> None:
    width, height = 1160, 460
    margin_l, margin_r, margin_t, margin_b = 76.0, 36.0, 78.0, 90.0
    plot_x0, plot_x1 = margin_l, width - margin_r
    plot_y0, plot_y1 = margin_t, height - margin_b
    bar_gap = 26.0
    bar_w = (plot_x1 - plot_x0 - bar_gap * (len(aggregate_rows) - 1)) / len(aggregate_rows)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f"<title>Controller ablation action mix for {html.escape(load)} load</title>",
        "<desc>Stacked bars compare the five controller actions across learned variants and proxy baselines.</desc>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Controller ablation action mix ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "Learned bars average train-seed percentages; proxy bars average evaluation-run percentages.", 11, "middle"),
    ]
    for index in range(5):
        pct = index / 4
        yy = scale(pct, 0, 1, plot_y1, plot_y0)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 8, yy + 3, f"{int(100 * pct)}%", 10, "end"))
    for index, row in enumerate(aggregate_rows):
        variant = str(row["variant"])
        xx = plot_x0 + index * (bar_w + bar_gap)
        y_cursor = plot_y1
        for action in ACTIONS:
            pct = float(row[f"{action}_pct_mean"])
            bar_height = (plot_y1 - plot_y0) * pct
            yy = y_cursor - bar_height
            parts.append(f'<rect x="{xx:.1f}" y="{yy:.1f}" width="{bar_w:.1f}" height="{bar_height:.1f}" fill="{ACTION_COLOR[action]}"/>')
            y_cursor = yy
        label_a, label_b = variant_label_parts(variant)
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 18, label_a, 10, "middle", "700"))
        if label_b:
            parts.append(svg_text(xx + bar_w / 2, plot_y1 + 32, label_b, 10, "middle", "700"))
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 47, f'n={int(row["sample_count"])}', 8, "middle"))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    legend_x, legend_y = plot_x0, height - 18
    for action in ACTIONS:
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{ACTION_COLOR[action]}"/>')
        parts.append(svg_text(legend_x + 19, legend_y, action, 10))
        legend_x += 144
    parts.append("</svg>")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze SpecNet controller ablations.")
    parser.add_argument("--input-dir", default="outputs/controller_ablation_qw1_60_eval10")
    parser.add_argument("--output-dir", default="outputs/controller_ablation_qw1_60_eval10/analysis")
    parser.add_argument("--loads", default="light,medium,heavy")
    args = parser.parse_args()

    loads = [load.strip() for load in args.loads.split(",") if load.strip()]
    invalid_loads = [load for load in loads if load not in {"light", "medium", "heavy"}]
    if not loads or invalid_loads:
        raise SystemExit(f"Invalid loads: {invalid_loads or loads}")

    summary_rows = read_csv(os.path.join(args.input_dir, "summary_by_run.csv"))
    workflow_rows = read_csv(os.path.join(args.input_dir, "workflow_results.csv"))
    all_points: List[Dict[str, object]] = []
    all_summaries: List[Dict[str, object]] = []
    for load in loads:
        points = extract_point_rows(summary_rows, workflow_rows, load)
        summaries = aggregate_points(points)
        expected_variants = {variant for variant, _, _ in VARIANTS}
        found_variants = {str(row["variant"]) for row in summaries}
        if found_variants != expected_variants:
            raise SystemExit(f"Missing controller variants for {load}: {sorted(expected_variants - found_variants)}")
        all_points.extend(points)
        all_summaries.extend(summaries)
        draw_metrics_svg(
            points,
            summaries,
            os.path.join(args.output_dir, f"fig_controller_ablation_metrics_{load}.svg"),
            load,
        )
        draw_action_mix_svg(
            summaries,
            os.path.join(args.output_dir, f"fig_controller_ablation_action_mix_{load}.svg"),
            load,
        )

    deltas = paired_delta_rows(summary_rows, loads)
    delta_summaries = aggregate_deltas(deltas)
    for load in loads:
        draw_deltas_svg(
            [row for row in deltas if row["load"] == load],
            [row for row in delta_summaries if row["load"] == load],
            os.path.join(args.output_dir, f"fig_controller_ablation_deltas_{load}.svg"),
            load,
        )

    coverage_rows = extract_state_coverage(os.path.join(args.input_dir, "specnet_agent_model.json"))
    point_fields = [
        "load", "variant", "variant_label", "comparison_type", "source_policy", "state_features",
        "quality_weight", "train_seed", "eval_seed", "unit_id", "variation_source", "runs",
    ] + [metric for metric, _ in METRICS] + ["total_actions"]
    for action in ACTIONS:
        point_fields.extend([f"{action}_count", f"{action}_pct"])
    summary_fields = [
        "load", "variant", "variant_label", "comparison_type", "state_features", "quality_weight",
        "sample_count", "variation_source", "train_seeds", "eval_seed",
    ]
    for metric, _ in METRICS:
        summary_fields.extend([f"{metric}_mean", f"{metric}_std", f"{metric}_min", f"{metric}_max"])
    for action in ACTIONS:
        summary_fields.extend([f"{action}_pct_mean", f"{action}_pct_std"])
    delta_fields = [
        "load", "variant", "variant_label", "comparison_type", "train_seed", "eval_seed", "runs",
    ]
    for metric, _ in METRICS:
        delta_fields.extend([f"{metric}_full", f"{metric}_variant", f"{metric}_delta"])
    delta_summary_fields = [
        "load", "variant", "variant_label", "comparison_type", "seed_count", "train_seeds", "eval_seed",
    ]
    for metric, _ in METRICS:
        delta_summary_fields.extend(
            [f"{metric}_delta_mean", f"{metric}_delta_std", f"{metric}_delta_min", f"{metric}_delta_max"]
        )
    coverage_fields = [
        "policy", "controller_variant", "state_features", "quality_weight", "train_seed", "feature",
        "bucket_count", "buckets", "visited_states", "training_updates", "coverage_ok",
    ]

    write_csv(os.path.join(args.output_dir, "controller_ablation_points.csv"), all_points, point_fields)
    write_csv(os.path.join(args.output_dir, "controller_ablation_summary.csv"), all_summaries, summary_fields)
    write_csv(os.path.join(args.output_dir, "controller_ablation_paired_deltas.csv"), deltas, delta_fields)
    write_csv(os.path.join(args.output_dir, "controller_ablation_delta_summary.csv"), delta_summaries, delta_summary_fields)
    write_csv(os.path.join(args.output_dir, "controller_state_coverage.csv"), coverage_rows, coverage_fields)

    coverage_failures = [row for row in coverage_rows if not row["coverage_ok"]]
    print("Wrote controller ablation analysis to:", os.path.abspath(args.output_dir))
    if coverage_failures:
        failed_features = sorted({str(row["feature"]) for row in coverage_failures})
        print("WARNING: degenerate controller-state features:", ",".join(failed_features))


if __name__ == "__main__":
    main()
