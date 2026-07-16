#!/usr/bin/env python3
"""Draw training-seed stability diagnostics for SpecNet-Agent sweeps.

The script reads an experiment directory produced with multiple training seeds
and a fixed evaluation seed. It exports point/aggregate CSVs and compact SVG
figures using only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import html
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
        for row in rows:
            writer.writerow(row)


def mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def scale(value: float, src_min: float, src_max: float, dst_min: float, dst_max: float) -> float:
    if abs(src_max - src_min) < 1e-12:
        return (dst_min + dst_max) / 2
    return dst_min + (value - src_min) * (dst_max - dst_min) / (src_max - src_min)


def svg_text(x: float, y: float, text: str, size: int = 12, anchor: str = "start", weight: str = "400") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="#0f172a">'
        f"{html.escape(text)}</text>"
    )


def weight_label(weight: float) -> str:
    return f"{weight:g}"


def metric_label(metric: str, value: float) -> str:
    if metric == "deadline_miss_ratio":
        return f"{100 * value:.1f}%"
    if metric == "avg_quality":
        return f"{value:.3f}"
    return f"{value:.1f}"


def extract_point_rows(input_dir: str, load: str) -> List[Dict[str, object]]:
    summary_rows = read_csv(os.path.join(input_dir, "summary_aggregate.csv"))
    action_rows = read_csv(os.path.join(input_dir, "action_counts.csv"))
    actions_by_policy: Dict[str, Counter[str]] = defaultdict(Counter)
    for row in action_rows:
        if row["load"] != load or not row["policy"].startswith("specnet_agent"):
            continue
        if row.get("train_seed", "") == "":
            continue
        actions_by_policy[row["policy"]][row["action"]] += int(row["count"])

    rows: List[Dict[str, object]] = []
    for row in summary_rows:
        if row["load"] != load or not row["policy"].startswith("specnet_agent"):
            continue
        if row.get("quality_weight", "") == "" or row.get("train_seed", "") == "":
            continue
        policy = row["policy"]
        action_counts = actions_by_policy[policy]
        out: Dict[str, object] = {
            "load": row["load"],
            "policy": policy,
            "quality_weight": float(row["quality_weight"]),
            "train_seed": int(float(row["train_seed"])),
            "eval_seed": int(float(row.get("eval_seed", 0) or 0)),
            "runs": int(row["runs"]),
            "p99_latency": float(row["p99_latency"]),
            "avg_quality": float(row["avg_quality"]),
            "deadline_miss_ratio": float(row["deadline_miss_ratio"]),
            "wasted_speculative_bytes_per_workflow": float(row["wasted_speculative_bytes_per_workflow"]),
        }
        total_actions = sum(action_counts.values())
        out["total_actions"] = total_actions
        for action in ACTIONS:
            count = action_counts[action]
            out[f"{action}_count"] = count
            out[f"{action}_pct"] = count / total_actions if total_actions else 0.0
        rows.append(out)
    rows.sort(key=lambda item: (float(item["quality_weight"]), int(item["train_seed"])))
    return rows


def aggregate_by_weight(point_rows: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    grouped: Dict[float, List[Dict[str, object]]] = defaultdict(list)
    for row in point_rows:
        grouped[float(row["quality_weight"])].append(row)

    rows: List[Dict[str, object]] = []
    for quality_weight, items in sorted(grouped.items()):
        out: Dict[str, object] = {
            "load": items[0]["load"],
            "quality_weight": quality_weight,
            "seed_count": len(items),
            "train_seeds": ",".join(str(int(item["train_seed"])) for item in items),
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
    return rows


def metric_range(values: Sequence[float], metric: str) -> Tuple[float, float]:
    y_min, y_max = min(values), max(values)
    if metric == "deadline_miss_ratio":
        return 0.0, y_max * 1.25 if y_max > 0 else 0.01
    if metric == "avg_quality":
        return max(0.80, y_min - 0.012), min(1.01, y_max + 0.008)
    margin = (y_max - y_min) * 0.15
    if margin < 1e-9:
        margin = max(1.0, abs(y_max) * 0.05)
    return max(0.0, y_min - margin), y_max + margin


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
    pad_l, pad_r, pad_t, pad_b = 64.0, 24.0, 34.0, 46.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    weights = [float(row["quality_weight"]) for row in aggregate_rows]
    x_by_weight = {
        weight: scale(i, 0, max(1, len(weights) - 1), plot_x0 + 14, plot_x1 - 14)
        for i, weight in enumerate(weights)
    }
    values = [float(row[metric]) for row in point_rows]
    y_min, y_max = metric_range(values, metric)

    def y_for(value: float) -> float:
        return scale(value, y_min, y_max, plot_y1, plot_y0)

    parts: List[str] = [svg_text(x0 + width / 2, y0 + 18, title, 13, "middle", "700")]
    for i in range(4):
        tick = y_min + (y_max - y_min) * i / 3
        yy = y_for(tick)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 7, yy + 3, metric_label(metric, tick), 9, "end"))
    for weight in weights:
        xx = x_by_weight[weight]
        parts.append(f'<line x1="{xx:.1f}" y1="{plot_y0:.1f}" x2="{xx:.1f}" y2="{plot_y1:.1f}" stroke="#f1f5f9"/>')
        parts.append(svg_text(xx, plot_y1 + 17, weight_label(weight), 9, "middle"))

    mean_points = []
    for agg in aggregate_rows:
        weight = float(agg["quality_weight"])
        xx = x_by_weight[weight]
        y_lo = y_for(float(agg[f"{metric}_min"]))
        y_hi = y_for(float(agg[f"{metric}_max"]))
        y_mean = y_for(float(agg[f"{metric}_mean"]))
        mean_points.append(f"{xx:.1f},{y_mean:.1f}")
        parts.append(f'<line x1="{xx:.1f}" y1="{y_hi:.1f}" x2="{xx:.1f}" y2="{y_lo:.1f}" stroke="#475569" stroke-width="1.4"/>')
        parts.append(f'<circle cx="{xx:.1f}" cy="{y_hi:.1f}" r="2.0" fill="#475569"/>')
        parts.append(f'<circle cx="{xx:.1f}" cy="{y_lo:.1f}" r="2.0" fill="#475569"/>')

    parts.append(f'<polyline points="{" ".join(mean_points)}" fill="none" stroke="#059669" stroke-width="2.2"/>')

    grouped_points: Dict[float, List[Dict[str, object]]] = defaultdict(list)
    for row in point_rows:
        grouped_points[float(row["quality_weight"])].append(row)
    for weight, items in grouped_points.items():
        items = sorted(items, key=lambda row: int(row["train_seed"]))
        for idx, row in enumerate(items):
            jitter = (idx - (len(items) - 1) / 2) * 5.5
            xx = x_by_weight[weight] + jitter
            yy = y_for(float(row[metric]))
            parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="3.5" fill="#34d399" stroke="#064e3b" stroke-width="0.8"/>')

    for agg in aggregate_rows:
        weight = float(agg["quality_weight"])
        xx = x_by_weight[weight]
        yy = y_for(float(agg[f"{metric}_mean"]))
        parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="5.0" fill="#059669" stroke="#ffffff" stroke-width="1.0"/>')
        parts.append(svg_text(xx + 7, yy - 5, metric_label(metric, float(agg[f"{metric}_mean"])), 8))

    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(svg_text((plot_x0 + plot_x1) / 2, y0 + height - 6, "quality weight", 10, "middle", "700"))
    return "\n".join(parts)


def draw_metrics_svg(point_rows: Sequence[Dict[str, object]], aggregate_rows: Sequence[Dict[str, object]], output_path: str, load: str) -> None:
    width, height = 1120, 650
    panel_w, panel_h = 520, 260
    seed_text = ",".join(str(seed) for seed in sorted({int(row["train_seed"]) for row in point_rows}))
    eval_seed = str(point_rows[0]["eval_seed"]) if point_rows else ""
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Training-seed stability ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, f"Small dots are train seeds ({seed_text}); large dots are means. eval_seed={eval_seed}.", 11, "middle"),
        draw_metric_panel(point_rows, aggregate_rows, "p99_latency", "p99 latency", 28, 76, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "avg_quality", "average quality", 572, 76, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "wasted_speculative_bytes_per_workflow", "wasted speculative bytes / workflow", 28, 362, panel_w, panel_h),
        draw_metric_panel(point_rows, aggregate_rows, "deadline_miss_ratio", "deadline miss ratio", 572, 362, panel_w, panel_h),
        "</svg>",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def draw_action_mix_svg(aggregate_rows: Sequence[Dict[str, object]], output_path: str, load: str) -> None:
    width, height = 900, 430
    margin_l, margin_r, margin_t, margin_b = 72.0, 36.0, 72.0, 78.0
    plot_x0, plot_x1 = margin_l, width - margin_r
    plot_y0, plot_y1 = margin_t, height - margin_b
    bar_gap = 34.0
    bar_w = (plot_x1 - plot_x0 - bar_gap * (len(aggregate_rows) - 1)) / len(aggregate_rows)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Mean action mix across training seeds ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "Bars show mean action percentages across independently trained agents.", 11, "middle"),
    ]
    for i in range(5):
        pct = i / 4
        yy = scale(pct, 0, 1, plot_y1, plot_y0)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 8, yy + 3, f"{int(100 * pct)}%", 10, "end"))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')

    for idx, row in enumerate(aggregate_rows):
        xx = plot_x0 + idx * (bar_w + bar_gap)
        y_cursor = plot_y1
        for action in ACTIONS:
            pct = float(row[f"{action}_pct_mean"])
            height_px = (plot_y1 - plot_y0) * pct
            yy = y_cursor - height_px
            parts.append(f'<rect x="{xx:.1f}" y="{yy:.1f}" width="{bar_w:.1f}" height="{height_px:.1f}" fill="{ACTION_COLOR[action]}"/>')
            if pct > 0.10:
                parts.append(svg_text(xx + bar_w / 2, yy + height_px / 2 + 3, f"{int(round(100 * pct))}%", 9, "middle", "700"))
            y_cursor = yy
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 18, weight_label(float(row["quality_weight"])), 10, "middle", "700"))
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 34, f'n={int(row["seed_count"])} seeds', 8, "middle"))

    legend_x, legend_y = plot_x0, height - 20
    for action in ACTIONS:
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{ACTION_COLOR[action]}"/>')
        parts.append(svg_text(legend_x + 19, legend_y, action, 10))
        legend_x += 132
    parts.append("</svg>")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot SpecNet-Agent training-seed stability diagnostics.")
    parser.add_argument("--input-dir", default="outputs/qw_training_seed_stability_eval10")
    parser.add_argument("--output-dir", default="outputs/qw_training_seed_stability_eval10/seed_stability")
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    point_rows = extract_point_rows(args.input_dir, args.load)
    aggregate_rows = aggregate_by_weight(point_rows)

    point_fields = [
        "load",
        "policy",
        "quality_weight",
        "train_seed",
        "eval_seed",
        "runs",
        "p99_latency",
        "avg_quality",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "total_actions",
    ]
    for action in ACTIONS:
        point_fields.append(f"{action}_count")
        point_fields.append(f"{action}_pct")
    aggregate_fields = [
        "load",
        "quality_weight",
        "seed_count",
        "train_seeds",
        "eval_seed",
    ]
    for metric, _ in METRICS:
        aggregate_fields.extend([f"{metric}_mean", f"{metric}_std", f"{metric}_min", f"{metric}_max"])
    for action in ACTIONS:
        aggregate_fields.extend([f"{action}_pct_mean", f"{action}_pct_std"])

    write_csv(os.path.join(args.output_dir, f"seed_stability_points_{args.load}.csv"), point_rows, point_fields)
    write_csv(os.path.join(args.output_dir, f"seed_stability_by_weight_{args.load}.csv"), aggregate_rows, aggregate_fields)
    draw_metrics_svg(point_rows, aggregate_rows, os.path.join(args.output_dir, f"fig_seed_stability_metrics_{args.load}.svg"), args.load)
    draw_action_mix_svg(aggregate_rows, os.path.join(args.output_dir, f"fig_seed_stability_action_mix_{args.load}.svg"), args.load)
    print("Wrote training-seed stability analysis to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
