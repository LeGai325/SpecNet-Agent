
#!/usr/bin/env python3
"""Draw local quality-weight sweep diagnostics around qw=4.0.

The script reads an experiment directory produced with a dense quality-weight
sweep and exports a compact table plus SVG figures. It uses only the Python
standard library.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Sequence


ACTIONS = ["full", "moderate", "recovery", "conservative", "critical_only"]
ACTION_COLOR = {
    "full": "#16a34a",
    "moderate": "#2563eb",
    "recovery": "#0d9488",
    "conservative": "#f59e0b",
    "critical_only": "#dc2626",
}


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


def policy_for_weight(weight: float) -> str:
    return f"specnet_agent_qw_{weight:.2f}".replace(".", "_")


def weight_label(weight: float) -> str:
    return f"{weight:g}"


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


def extract_rows(input_dir: str, load: str) -> List[Dict[str, object]]:
    summary_rows = read_csv(os.path.join(input_dir, "summary_aggregate.csv"))
    action_rows = read_csv(os.path.join(input_dir, "action_counts.csv"))
    actions_by_policy: Dict[str, Counter[str]] = defaultdict(Counter)
    for row in action_rows:
        if row["load"] != load or not row["policy"].startswith("specnet_agent_qw_"):
            continue
        actions_by_policy[row["policy"]][row["action"]] += int(row["count"])

    rows: List[Dict[str, object]] = []
    for row in summary_rows:
        if row["load"] != load or not row["policy"].startswith("specnet_agent_qw_"):
            continue
        if row["quality_weight"] == "":
            continue
        policy = row["policy"]
        action_counts = actions_by_policy[policy]
        out: Dict[str, object] = {
            "load": row["load"],
            "policy": policy,
            "quality_weight": float(row["quality_weight"]),
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
    rows.sort(key=lambda item: float(item["quality_weight"]))
    return rows


def draw_metric_panel(rows: List[Dict[str, object]], metric: str, title: str, x0: float, y0: float, width: float, height: float) -> str:
    pad_l, pad_r, pad_t, pad_b = 58.0, 18.0, 34.0, 42.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    weights = [float(row["quality_weight"]) for row in rows]
    values = [float(row[metric]) for row in rows]
    x_min, x_max = min(weights), max(weights)
    y_min, y_max = min(values), max(values)
    if metric == "avg_quality":
        y_min = max(0.80, y_min - 0.01)
        y_max = min(1.01, y_max + 0.006)
    elif metric == "deadline_miss_ratio":
        y_min = 0.0
        y_max = y_max * 1.20 if y_max > 0 else 0.01
    else:
        y_min = max(0.0, y_min - (y_max - y_min) * 0.12)
        y_max = y_max + (y_max - y_min) * 0.12

    def x_for(value: float) -> float:
        return scale(value, x_min, x_max, plot_x0, plot_x1)

    def y_for(value: float) -> float:
        return scale(value, y_min, y_max, plot_y1, plot_y0)

    parts: List[str] = []
    parts.append(svg_text(x0 + width / 2, y0 + 18, title, 13, "middle", "700"))
    for i in range(4):
        tick = y_min + (y_max - y_min) * i / 3
        yy = y_for(tick)
        label = f"{100 * tick:.1f}%" if metric == "deadline_miss_ratio" else f"{tick:.2f}" if metric == "avg_quality" else f"{tick:.0f}"
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 7, yy + 3, label, 9, "end"))
    for weight in weights:
        xx = x_for(weight)
        parts.append(f'<line x1="{xx:.1f}" y1="{plot_y0:.1f}" x2="{xx:.1f}" y2="{plot_y1:.1f}" stroke="#f1f5f9"/>')
        parts.append(svg_text(xx, plot_y1 + 17, weight_label(weight), 9, "middle"))
    points = " ".join(f'{x_for(float(row["quality_weight"])):.1f},{y_for(float(row[metric])):.1f}' for row in rows)
    parts.append(f'<polyline points="{points}" fill="none" stroke="#059669" stroke-width="2.2"/>')
    for row in rows:
        xx = x_for(float(row["quality_weight"]))
        yy = y_for(float(row[metric]))
        value = float(row[metric])
        label = f"{100 * value:.1f}%" if metric == "deadline_miss_ratio" else f"{value:.3f}" if metric == "avg_quality" else f"{value:.1f}"
        parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4.8" fill="#059669" stroke="#ffffff" stroke-width="1.0"/>')
        parts.append(svg_text(xx + 6, yy - 5, label, 8))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(svg_text((plot_x0 + plot_x1) / 2, y0 + height - 5, "quality weight", 10, "middle", "700"))
    return "\n".join(parts)


def draw_metrics_svg(rows: List[Dict[str, object]], output_path: str, load: str) -> None:
    width, height = 1120, 650
    panel_w, panel_h = 520, 260
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Local quality-weight sweep around 4.0 ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "Dense sampling shows the qw=3.5-4.0 moderate-heavy operating region.", 11, "middle"),
        draw_metric_panel(rows, "p99_latency", "p99 latency", 28, 76, panel_w, panel_h),
        draw_metric_panel(rows, "avg_quality", "average quality", 572, 76, panel_w, panel_h),
        draw_metric_panel(rows, "wasted_speculative_bytes_per_workflow", "wasted speculative bytes / workflow", 28, 362, panel_w, panel_h),
        draw_metric_panel(rows, "deadline_miss_ratio", "deadline miss ratio", 572, 362, panel_w, panel_h),
        "</svg>",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def draw_actions_svg(rows: List[Dict[str, object]], output_path: str, load: str) -> None:
    width, height = 1040, 440
    margin_l, margin_r, margin_t, margin_b = 76.0, 36.0, 72.0, 78.0
    plot_x0, plot_x1 = margin_l, width - margin_r
    plot_y0, plot_y1 = margin_t, height - margin_b
    bar_gap = 20.0
    bar_w = (plot_x1 - plot_x0 - bar_gap * (len(rows) - 1)) / len(rows)

    parts: List[str] = [

        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Action mix around qw=4.0 ({load} load)", 18, "middle", "700"),
        svg_text(width / 2, 50, "qw=3.5 and qw=4.0 shift strongly toward moderate; neighboring weights prefer full or recovery.", 11, "middle"),
    ]
    for i in range(5):
        pct = i / 4
        yy = scale(pct, 0, 1, plot_y1, plot_y0)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0"/>')
        parts.append(svg_text(plot_x0 - 8, yy + 3, f"{int(100 * pct)}%", 10, "end"))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')

    for idx, row in enumerate(rows):
        xx = plot_x0 + idx * (bar_w + bar_gap)
        y_cursor = plot_y1
        for action in ACTIONS:
            pct = float(row[f"{action}_pct"])
            height_px = (plot_y1 - plot_y0) * pct
            yy = y_cursor - height_px
            parts.append(f'<rect x="{xx:.1f}" y="{yy:.1f}" width="{bar_w:.1f}" height="{height_px:.1f}" fill="{ACTION_COLOR[action]}"/>')
            if pct > 0.10:
                parts.append(svg_text(xx + bar_w / 2, yy + height_px / 2 + 3, f"{int(round(100 * pct))}%", 9, "middle", "700"))
            y_cursor = yy
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 18, weight_label(float(row["quality_weight"])), 10, "middle", "700"))
        parts.append(svg_text(xx + bar_w / 2, plot_y1 + 34, f'n={int(row["total_actions"])}', 8, "middle"))

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
    parser = argparse.ArgumentParser(description="Plot dense quality-weight diagnostics around qw=4.0.")
    parser.add_argument("--input-dir", default="outputs/qw_around_4_eval10")
    parser.add_argument("--output-dir", default="outputs/qw_around_4_eval10/local_analysis")
    parser.add_argument("--load", default="heavy", choices=["light", "medium", "heavy"])
    args = parser.parse_args()

    rows = extract_rows(args.input_dir, args.load)
    fieldnames = [
        "load",
        "policy",
        "quality_weight",
        "p99_latency",
        "avg_quality",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "total_actions",
    ]
    for action in ACTIONS:
        fieldnames.append(f"{action}_count")
        fieldnames.append(f"{action}_pct")
    write_csv(os.path.join(args.output_dir, f"qw_local_points_{args.load}.csv"), rows, fieldnames)
    draw_metrics_svg(rows, os.path.join(args.output_dir, f"fig_qw_local_metrics_{args.load}.svg"), args.load)
    draw_actions_svg(rows, os.path.join(args.output_dir, f"fig_qw_local_action_mix_{args.load}.svg"), args.load)
    print("Wrote local quality-weight analysis to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
