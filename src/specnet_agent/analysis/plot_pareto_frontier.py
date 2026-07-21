
#!/usr/bin/env python3
"""Export Pareto points and draw latency-quality frontier figures.

This script intentionally uses only the Python standard library so the
experiment can be plotted on a clean machine without matplotlib.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
from typing import Dict, Iterable, List, Sequence, Tuple


TARGET_POLICIES = [
    "rule_aggressive",
    "rule_balanced",
    "rule_quality_preserving",
    "specnet_agent_qw_0_50",
    "specnet_agent_qw_1_00",
    "specnet_agent_qw_1_60",
    "specnet_agent_qw_2_50",
    "specnet_agent_qw_4_00",
    "specnet_agent_qw_6_00",
]

CONTEXT_POLICIES = [
    "fifo",
    "static_priority",
    "critical_path_only",
]

METRIC_POLICIES = CONTEXT_POLICIES + TARGET_POLICIES

RULE_POLICIES = {
    "rule_aggressive",
    "rule_balanced",
    "rule_quality_preserving",
}

SPECNET_POLICIES = [policy for policy in TARGET_POLICIES if policy.startswith("specnet_agent_qw_")]

POLICY_LABEL = {
    "fifo": "FIFO",
    "static_priority": "Static priority",
    "critical_path_only": "Critical-path only",
    "rule_aggressive": "Rule aggressive",
    "rule_balanced": "Rule balanced",
    "rule_quality_preserving": "Rule quality preserving",
    "specnet_agent_qw_0_50": "SpecNet qw=0.5",
    "specnet_agent_qw_1_00": "SpecNet qw=1.0",
    "specnet_agent_qw_1_60": "SpecNet qw=1.6",
    "specnet_agent_qw_2_50": "SpecNet qw=2.5",
    "specnet_agent_qw_4_00": "SpecNet qw=4.0",
    "specnet_agent_qw_6_00": "SpecNet qw=6.0",
}

POLICY_SHORT = {
    "fifo": "FIFO",
    "static_priority": "Static",
    "critical_path_only": "CritPath",
    "rule_aggressive": "Rule-A",
    "rule_balanced": "Rule-B",
    "rule_quality_preserving": "Rule-Q",
    "specnet_agent_qw_0_50": "S-0.5",
    "specnet_agent_qw_1_00": "S-1.0",
    "specnet_agent_qw_1_60": "S-1.6",
    "specnet_agent_qw_2_50": "S-2.5",
    "specnet_agent_qw_4_00": "S-4.0",
    "specnet_agent_qw_6_00": "S-6.0",
}

RULE_COLOR = "#f59e0b"
SPECNET_COLOR = "#059669"
BASELINE_COLOR = "#64748b"
FRONTIER_COLOR = "#0f172a"
MISS_COLOR = "#dc2626"


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


def selected_rows(rows: Iterable[Dict[str, str]], policies: Sequence[str]) -> List[Dict[str, object]]:
    selected = set(policies)
    out: List[Dict[str, object]] = []
    for row in rows:
        policy = row["policy"]
        if policy not in selected:
            continue
        if policy in RULE_POLICIES:
            controller = "rule"
        elif policy in CONTEXT_POLICIES:
            controller = "baseline"
        else:
            controller = "specnet"
        out.append(
            {
                "load": row["load"],
                "policy": policy,
                "policy_label": POLICY_LABEL[policy],
                "controller": controller,
                "quality_weight": row["quality_weight"],
                "p99_latency": float(row["p99_latency"]),
                "avg_quality": float(row["avg_quality"]),
                "deadline_miss_ratio": float(row["deadline_miss_ratio"]),
                "wasted_speculative_bytes_per_workflow": float(row["wasted_speculative_bytes_per_workflow"]),
            }
        )
    return out


def mark_pareto(rows: List[Dict[str, object]]) -> None:
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            better_or_equal = (
                float(other["p99_latency"]) <= float(row["p99_latency"])
                and float(other["avg_quality"]) >= float(row["avg_quality"])
            )
            strictly_better = (
                float(other["p99_latency"]) < float(row["p99_latency"])
                or float(other["avg_quality"]) > float(row["avg_quality"])
            )
            if better_or_equal and strictly_better:
                dominated = True
                break
        row["pareto_frontier"] = "no" if dominated else "yes"


def scale(value: float, src_min: float, src_max: float, dst_min: float, dst_max: float) -> float:
    if abs(src_max - src_min) < 1e-12:
        return (dst_min + dst_max) / 2
    return dst_min + (value - src_min) * (dst_max - dst_min) / (src_max - src_min)


def specnet_sort_key(row: Dict[str, object]) -> float:
    value = row["quality_weight"]
    return float(value) if value != "" else 0.0


def color_for(row: Dict[str, object]) -> str:
    if row["controller"] == "specnet":
        return SPECNET_COLOR
    if row["controller"] == "rule":
        return RULE_COLOR
    return BASELINE_COLOR


def svg_text(x: float, y: float, text: str, size: int = 12, anchor: str = "start", weight: str = "400") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="DejaVu Sans, Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" fill="#0f172a">'
        f"{html.escape(text)}</text>"
    )


def draw_panel(rows: List[Dict[str, object]], load: str, x0: float, y0: float, width: float, height: float) -> str:
    pad_l, pad_r, pad_t, pad_b = 58.0, 18.0, 34.0, 46.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    xs = [float(row["p99_latency"]) for row in rows]
    ys = [float(row["avg_quality"]) for row in rows]
    wastes = [float(row["wasted_speculative_bytes_per_workflow"]) for row in rows]
    misses = [float(row["deadline_miss_ratio"]) for row in rows]
    x_min, x_max = min(xs), max(xs)

    y_min, y_max = min(ys), max(ys)
    waste_min, waste_max = min(wastes), max(wastes)
    miss_max = max(misses)
    x_pad = max(2.0, (x_max - x_min) * 0.10)
    y_pad = max(0.005, (y_max - y_min) * 0.14)
    x_min -= x_pad
    x_max += x_pad
    y_min = max(0.70, y_min - y_pad)
    y_max = min(1.01, y_max + y_pad)

    def x_for(value: float) -> float:
        return scale(value, x_min, x_max, plot_x0, plot_x1)

    def y_for(value: float) -> float:
        return scale(value, y_min, y_max, plot_y1, plot_y0)

    def r_for(waste: float) -> float:
        return scale(waste, waste_min, waste_max, 4.4, 9.2)

    parts: List[str] = []
    parts.append(f'<g id="panel-{html.escape(load)}">')
    parts.append(svg_text(x0 + width / 2, y0 + 18, f"{load.capitalize()} load", 14, "middle", "700"))
    parts.append(f'<rect x="{plot_x0:.1f}" y="{plot_y0:.1f}" width="{plot_x1 - plot_x0:.1f}" height="{plot_y1 - plot_y0:.1f}" fill="#ffffff"/>')

    for i in range(5):
        tx = x_min + (x_max - x_min) * i / 4
        xx = x_for(tx)
        parts.append(f'<line x1="{xx:.1f}" y1="{plot_y0:.1f}" x2="{xx:.1f}" y2="{plot_y1:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(svg_text(xx, plot_y1 + 18, f"{tx:.0f}", 10, "middle"))
    for i in range(5):
        ty = y_min + (y_max - y_min) * i / 4
        yy = y_for(ty)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(svg_text(plot_x0 - 8, yy + 3, f"{ty:.2f}", 10, "end"))

    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.2"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.2"/>')

    specnet = [row for row in rows if row["controller"] == "specnet"]
    specnet_sorted = sorted(specnet, key=specnet_sort_key)
    if len(specnet_sorted) > 1:
        pts = " ".join(f'{x_for(float(row["p99_latency"])):.1f},{y_for(float(row["avg_quality"])):.1f}' for row in specnet_sorted)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{SPECNET_COLOR}" stroke-width="2.5" opacity="0.75"/>')

    frontier = sorted([row for row in rows if row["pareto_frontier"] == "yes"], key=lambda row: float(row["p99_latency"]))
    if len(frontier) > 1:
        pts = " ".join(f'{x_for(float(row["p99_latency"])):.1f},{y_for(float(row["avg_quality"])):.1f}' for row in frontier)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{FRONTIER_COLOR}" stroke-width="2" stroke-dasharray="5 4" opacity="0.80"/>')

    for row in rows:
        x = x_for(float(row["p99_latency"]))
        y = y_for(float(row["avg_quality"]))
        is_specnet = row["controller"] == "specnet"
        color = color_for(row)
        waste = float(row["wasted_speculative_bytes_per_workflow"])
        miss = 100.0 * float(row["deadline_miss_ratio"])
        radius = r_for(waste)
        stroke = MISS_COLOR if miss > 0 else "#ffffff"
        stroke_width = 1.8 if miss > 0 else 1.1
        label = str(row["quality_weight"]) if is_specnet else str(row["policy"]).replace("rule_", "rule\\n")
        tooltip = (
            f"{POLICY_LABEL[str(row['policy'])]} | p99={float(row['p99_latency']):.1f}, "
            f"quality={float(row['avg_quality']):.3f}, miss={miss:.1f}%, waste={waste:.1f}"
        )
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" stroke="{stroke}" stroke-width="{stroke_width:.1f}">')
        parts.append(f"<title>{html.escape(tooltip)}</title></circle>")
        label_text = label.replace("\\n", " ")
        dx = 8 if is_specnet else -8
        anchor = "start" if is_specnet else "end"
        parts.append(svg_text(x + dx, y - 7, label_text, 9, anchor))
        if miss > 0:
            parts.append(svg_text(x + dx, y + 10, f"miss {miss:.1f}%", 8, anchor))

    if miss_max > 0:
        parts.append(svg_text(plot_x1 - 3, plot_y0 + 12, "red outline = deadline miss > 0", 8, "end"))
    parts.append(svg_text(plot_x1 - 3, plot_y0 + 25, "larger point = more wasted bytes", 8, "end"))

    parts.append(svg_text((plot_x0 + plot_x1) / 2, y0 + height - 12, "p99 latency", 11, "middle", "700"))
    parts.append(
        f'<text x="{x0 + 14:.1f}" y="{(plot_y0 + plot_y1) / 2:.1f}" transform="rotate(-90 {x0 + 14:.1f} {(plot_y0 + plot_y1) / 2:.1f})" '
        'font-family="DejaVu Sans, Arial, sans-serif" font-size="11" font-weight="700" text-anchor="middle" fill="#0f172a">'
        "avg quality</text>"
    )
    parts.append("</g>")
    return "\n".join(parts)


def draw_svg(rows: List[Dict[str, object]], output_path: str, loads: Sequence[str], title: str) -> None:
    panel_w, panel_h = 390, 320
    margin = 24
    width = margin * 2 + panel_w * len(loads)
    height = 415
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 26, title, 18, "middle", "700"),
        svg_text(width / 2, 48, "x=p99 latency, y=avg quality, point size=wasted bytes, red outline=deadline miss.", 11, "middle"),
    ]
    for idx, load in enumerate(loads):
        load_rows = [row for row in rows if row["load"] == load]
        parts.append(draw_panel(load_rows, load, margin + idx * panel_w, 64, panel_w, panel_h))
    legend_y = height - 20
    legend_x = margin + 8
    parts.append(f'<circle cx="{legend_x:.1f}" cy="{legend_y:.1f}" r="5.5" fill="{SPECNET_COLOR}"/>')
    parts.append(svg_text(legend_x + 12, legend_y + 4, "SpecNet weight sweep", 11))
    parts.append(f'<circle cx="{legend_x + 180:.1f}" cy="{legend_y:.1f}" r="5.5" fill="{RULE_COLOR}"/>')
    parts.append(svg_text(legend_x + 192, legend_y + 4, "Rule feedback variants", 11))
    parts.append(f'<line x1="{legend_x + 372:.1f}" y1="{legend_y:.1f}" x2="{legend_x + 412:.1f}" y2="{legend_y:.1f}" stroke="{FRONTIER_COLOR}" stroke-width="2" stroke-dasharray="5 4"/>')
    parts.append(svg_text(legend_x + 420, legend_y + 4, "non-dominated frontier", 11))
    parts.append("</svg>")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def metric_value(row: Dict[str, object], metric: str) -> float:
    if metric == "deadline_miss_pct":
        return 100.0 * float(row["deadline_miss_ratio"])
    return float(row[metric])


def metric_label(value: float, metric: str) -> str:
    if metric == "avg_quality":
        return f"{value:.3f}"
    if metric == "deadline_miss_pct":
        return f"{value:.1f}%"
    return f"{value:.1f}"


def draw_metric_panel(
    rows: List[Dict[str, object]],
    policies: Sequence[str],
    metric: str,
    title: str,
    subtitle: str,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    pad_l, pad_r, pad_t, pad_b = 52.0, 14.0, 34.0, 66.0
    plot_x0, plot_x1 = x0 + pad_l, x0 + width - pad_r
    plot_y0, plot_y1 = y0 + pad_t, y0 + height - pad_b
    ordered = [row for policy in policies for row in rows if row["policy"] == policy]
    values = [metric_value(row, metric) for row in ordered]
    v_min = 0.0 if metric != "avg_quality" else max(0.70, min(values) - 0.03)
    v_max = max(values)
    if metric == "avg_quality":
        v_max = min(1.01, v_max + 0.015)
    else:
        v_max = v_max * 1.15 if v_max > 0 else 1.0

    def y_for(value: float) -> float:
        return scale(value, v_min, v_max, plot_y1, plot_y0)

    parts: List[str] = []
    parts.append(f'<g id="metric-{html.escape(metric)}">')
    parts.append(svg_text(x0 + width / 2, y0 + 18, title, 14, "middle", "700"))
    parts.append(svg_text(x0 + width / 2, y0 + 34, subtitle, 9, "middle"))
    parts.append(f'<rect x="{plot_x0:.1f}" y="{plot_y0:.1f}" width="{plot_x1 - plot_x0:.1f}" height="{plot_y1 - plot_y0:.1f}" fill="#ffffff"/>')
    for i in range(4):
        tick = v_min + (v_max - v_min) * i / 3
        yy = y_for(tick)
        parts.append(f'<line x1="{plot_x0:.1f}" y1="{yy:.1f}" x2="{plot_x1:.1f}" y2="{yy:.1f}" stroke="#e2e8f0" stroke-width="1"/>')
        parts.append(svg_text(plot_x0 - 7, yy + 3, metric_label(tick, metric), 9, "end"))
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y1:.1f}" x2="{plot_x1:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')
    parts.append(f'<line x1="{plot_x0:.1f}" y1="{plot_y0:.1f}" x2="{plot_x0:.1f}" y2="{plot_y1:.1f}" stroke="#334155" stroke-width="1.1"/>')

    gap = 5.0
    bar_w = (plot_x1 - plot_x0 - gap * (len(ordered) - 1)) / len(ordered)
    for idx, row in enumerate(ordered):
        value = metric_value(row, metric)
        xx = plot_x0 + idx * (bar_w + gap)
        yy = y_for(value)
        color = color_for(row)
        parts.append(f'<rect x="{xx:.1f}" y="{yy:.1f}" width="{bar_w:.1f}" height="{plot_y1 - yy:.1f}" fill="{color}" opacity="0.88"/>')
        parts.append(svg_text(xx + bar_w / 2, yy - 4, metric_label(value, metric), 8, "middle"))
        label_y = plot_y1 + 13
        parts.append(
            f'<text x="{xx + bar_w / 2:.1f}" y="{label_y:.1f}" transform="rotate(-35 {xx + bar_w / 2:.1f} {label_y:.1f})" '

            'font-family="DejaVu Sans, Arial, sans-serif" font-size="8" text-anchor="end" fill="#0f172a">'
            f'{html.escape(POLICY_SHORT[str(row["policy"])])}</text>'
        )
    parts.append("</g>")
    return "\n".join(parts)


def draw_metrics_dashboard(
    rows: List[Dict[str, object]],
    output_path: str,
    load: str,
    policies: Sequence[str],
) -> None:
    load_rows = [row for row in rows if row["load"] == load]
    panel_w, panel_h = 560, 300
    margin = 28
    width = margin * 2 + panel_w * 2 + 24
    height = 720
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, f"Core Pareto metrics under {load} load", 18, "middle", "700"),
        svg_text(width / 2, 50, "Original baselines, rule variants, and SpecNet weight sweep are shown across all four required metrics.", 11, "middle"),
    ]
    specs = [
        ("p99_latency", "p99 latency", "lower is better"),
        ("avg_quality", "average quality", "higher is better"),
        ("deadline_miss_pct", "deadline miss ratio", "lower is better"),
        ("wasted_speculative_bytes_per_workflow", "wasted speculative bytes / workflow", "lower is better"),
    ]
    positions = [
        (margin, 74),
        (margin + panel_w + 24, 74),
        (margin, 382),
        (margin + panel_w + 24, 382),
    ]
    for (metric, title, subtitle), (x0, y0) in zip(specs, positions):
        parts.append(draw_metric_panel(load_rows, policies, metric, title, subtitle, x0, y0, panel_w, panel_h))
    legend_y = height - 18
    parts.append(f'<rect x="{margin:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{BASELINE_COLOR}" opacity="0.88"/>')
    parts.append(svg_text(margin + 20, legend_y, "original baselines", 11))
    parts.append(f'<rect x="{margin + 150:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{RULE_COLOR}" opacity="0.88"/>')
    parts.append(svg_text(margin + 170, legend_y, "rule variants", 11))
    parts.append(f'<rect x="{margin + 275:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{SPECNET_COLOR}" opacity="0.88"/>')
    parts.append(svg_text(margin + 295, legend_y, "SpecNet quality-weight sweep", 11))
    parts.append("</svg>")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Pareto points and draw latency-quality figures.")
    parser.add_argument("--input-dir", default="outputs/final_9_variants_eval10")
    parser.add_argument("--output-dir", default="outputs/final_9_variants_eval10/pareto")
    args = parser.parse_args()

    summary_rows = read_csv(os.path.join(args.input_dir, "summary_aggregate.csv"))
    rows = selected_rows(summary_rows, TARGET_POLICIES)
    metric_rows = selected_rows(summary_rows, METRIC_POLICIES)
    for load in sorted({str(row["load"]) for row in rows}):
        mark_pareto([row for row in rows if row["load"] == load])

    fieldnames = [
        "load",
        "policy",
        "policy_label",
        "controller",
        "quality_weight",
        "p99_latency",
        "avg_quality",
        "deadline_miss_ratio",
        "wasted_speculative_bytes_per_workflow",
        "pareto_frontier",
    ]
    write_csv(os.path.join(args.output_dir, "pareto_points.csv"), rows, fieldnames)
    write_csv(
        os.path.join(args.output_dir, "pareto_points_heavy.csv"),
        [row for row in rows if row["load"] == "heavy"],
        fieldnames,
    )
    draw_svg(
        rows,
        os.path.join(args.output_dir, "fig_latency_quality_pareto_all_loads.svg"),
        ["light", "medium", "heavy"],
        "Latency-quality Pareto tradeoff",
    )
    draw_svg(
        rows,
        os.path.join(args.output_dir, "fig_latency_quality_pareto_heavy.svg"),
        ["heavy"],
        "Latency-quality Pareto tradeoff under heavy load",
    )
    draw_metrics_dashboard(
        metric_rows,
        os.path.join(args.output_dir, "fig_core_metrics_heavy.svg"),
        "heavy",
        METRIC_POLICIES,
    )
    for load in ["light", "medium"]:
        draw_metrics_dashboard(
            metric_rows,
            os.path.join(args.output_dir, f"fig_core_metrics_{load}.svg"),
            load,
            METRIC_POLICIES,
        )
    print("Wrote Pareto outputs to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
