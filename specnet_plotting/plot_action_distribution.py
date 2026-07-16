#!/usr/bin/env python3
"""Draw action distribution charts for final SpecNet-Agent variants.

The script reads action_counts.csv from an experiment directory and exports
stacked-percentage SVG figures plus a compact CSV table. It uses only the
Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Sequence


LOADS = ["light", "medium", "heavy"]
ACTIONS = ["full", "moderate", "recovery", "conservative", "critical_only"]
ACTION_COLOR = {
    "full": "#16a34a",
    "moderate": "#2563eb",
    "recovery": "#0d9488",
    "conservative": "#f59e0b",
    "critical_only": "#dc2626",
}
RULE_POLICIES = ["rule_aggressive", "rule_balanced", "rule_quality_preserving"]


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


def load_policy_weights(input_dir: str) -> Dict[str, float]:
    weights: Dict[str, float] = {}
    summary_path = os.path.join(input_dir, "summary_aggregate.csv")
    if not os.path.exists(summary_path):
        return weights
    for row in read_csv(summary_path):
        if row.get("quality_weight"):
            weights[row["policy"]] = float(row["quality_weight"])
    return weights


def collect_action_rows(input_dir: str) -> List[Dict[str, object]]:
    action_counts = read_csv(os.path.join(input_dir, "action_counts.csv"))
    policy_weights = load_policy_weights(input_dir)
    counts: Dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in action_counts:
        counts[(row["load"], row["policy"])][row["action"]] += int(row["count"])

    rows: List[Dict[str, object]] = []
    for (load, policy), counter in sorted(counts.items()):
        total = sum(counter.values())
        quality_weight = policy_weights.get(policy, "")
        group = "specnet" if policy.startswith("specnet_agent_qw_") else "rule" if policy in RULE_POLICIES else "reference"
        out: Dict[str, object] = {
            "load": load,
            "policy": policy,
            "group": group,
            "quality_weight": quality_weight,
            "total_actions": total,
        }
        for action in ACTIONS:
            out[f"{action}_count"] = counter[action]
            out[f"{action}_pct"] = counter[action] / total if total else 0.0
        rows.append(out)
    return rows


def specnet_label(row: Dict[str, object]) -> str:
    return f"qw={float(row['quality_weight']):g}"


def all_policy_label(row: Dict[str, object]) -> str:
    policy = str(row["policy"])
    if policy == "rule_aggressive":
        return "rule aggressive"
    if policy == "rule_balanced":
        return "rule balanced"
    if policy == "rule_quality_preserving":
        return "rule quality"
    if str(row["group"]) == "specnet":
        return specnet_label(row)
    return policy


def sort_rows(rows: Iterable[Dict[str, object]], include_rules: bool) -> List[Dict[str, object]]:
    selected = []
    for row in rows:
        if str(row["group"]) == "specnet":
            selected.append(row)
        elif include_rules and str(row["policy"]) in RULE_POLICIES:
            selected.append(row)

    def key(row: Dict[str, object]) -> tuple[int, float, str]:
        if str(row["policy"]) in RULE_POLICIES:
            return (0, float(RULE_POLICIES.index(str(row["policy"]))), str(row["policy"]))
        return (1, float(row["quality_weight"]), str(row["policy"]))

    return sorted(selected, key=key)


def draw_action_mix_svg(
    rows: Sequence[Dict[str, object]],
    output_path: str,
    title: str,
    subtitle: str,
    include_rules: bool,
) -> None:
    width = 1180 if include_rules else 940
    height = 520
    margin_l, margin_r, margin_t, margin_b = 78.0, 34.0, 78.0, 126.0
    plot_x0, plot_x1 = margin_l, width - margin_r
    plot_y0, plot_y1 = margin_t, height - margin_b
    bar_gap = 18.0 if include_rules else 28.0
    bar_w = (plot_x1 - plot_x0 - bar_gap * (len(rows) - 1)) / max(1, len(rows))

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        svg_text(width / 2, 28, title, 18, "middle", "700"),
        svg_text(width / 2, 50, subtitle, 11, "middle"),
    ]

    for i in range(5):
        pct = i / 4
        yy = scale(pct, 0.0, 1.0, plot_y1, plot_y0)
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
            if pct >= 0.10:
                parts.append(svg_text(xx + bar_w / 2, yy + height_px / 2 + 3, f"{round(100 * pct):.0f}%", 9, "middle", "700"))
            y_cursor = yy

        label = all_policy_label(row) if include_rules else specnet_label(row)
        x_label = xx + bar_w / 2
        if include_rules:
            parts.append(
                f'<text x="{x_label:.1f}" y="{plot_y1 + 22:.1f}" font-family="DejaVu Sans, Arial, sans-serif" '
                'font-size="9" text-anchor="end" fill="#0f172a" transform="rotate(-35 '
                f'{x_label:.1f} {plot_y1 + 22:.1f})">{html.escape(label)}</text>'
            )
        else:
            parts.append(svg_text(x_label, plot_y1 + 20, label, 10, "middle", "700"))
        parts.append(svg_text(x_label, plot_y1 + 38, f'n={int(row["total_actions"])}', 8, "middle"))

    legend_x, legend_y = plot_x0, height - 22
    for action in ACTIONS:
        parts.append(f'<rect x="{legend_x:.1f}" y="{legend_y - 10:.1f}" width="14" height="10" fill="{ACTION_COLOR[action]}"/>')
        parts.append(svg_text(legend_x + 19, legend_y, action, 10))
        legend_x += 134
    parts.append("</svg>")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot action distributions for final SpecNet-Agent variants.")
    parser.add_argument("--input-dir", default="outputs/final_9_variants_eval10")
    parser.add_argument("--output-dir", default="outputs/final_9_variants_eval10/action_distribution")
    args = parser.parse_args()

    rows = collect_action_rows(args.input_dir)
    fieldnames = ["load", "policy", "group", "quality_weight", "total_actions"]
    for action in ACTIONS:
        fieldnames.append(f"{action}_count")
        fieldnames.append(f"{action}_pct")
    write_csv(os.path.join(args.output_dir, "action_distribution_by_policy_load.csv"), rows, fieldnames)

    for load in LOADS:
        load_rows = [row for row in rows if row["load"] == load]
        specnet_rows = sort_rows(load_rows, include_rules=False)
        all_rows = sort_rows(load_rows, include_rules=True)
        draw_action_mix_svg(
            specnet_rows,
            os.path.join(args.output_dir, f"fig_action_distribution_specnet_{load}.svg"),
            f"SpecNet action distribution by quality weight ({load} load)",
            "Each stacked bar aggregates action counts across 10 evaluation runs.",
            include_rules=False,
        )
        draw_action_mix_svg(
            all_rows,
            os.path.join(args.output_dir, f"fig_action_distribution_rules_specnet_{load}.svg"),
            f"Rule baselines and SpecNet action distribution ({load} load)",
            "Rule profiles are shown first, followed by SpecNet quality-weight variants.",
            include_rules=True,
        )

    print("Wrote action distribution figures to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
