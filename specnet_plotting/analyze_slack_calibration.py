#!/usr/bin/env python3
"""Analyze Slack calibration and queue-weight sensitivity offline.

The script reuses decision-time diagnostics from ``workflow_results.csv`` and
recomputes Slack for several queue weights without retraining or changing the
controller trajectory that produced the data.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


LOAD_CAPACITY = {"light": 16.0, "medium": 16.0, "heavy": 16.0}
BUCKETS = ("tight", "normal", "loose")
BUCKET_ORDER = {bucket: index for index, bucket in enumerate(BUCKETS)}
CONGESTION_ORDER = {"low": 0, "medium": 1, "high": 2}
ROLE_FIELDS = (
    "critical_work",
    "normal_work",
    "speculative_work",
    "background_work",
    "other_work",
)
QUEUE_BASES = ("total", "policy_weighted")


def read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: str, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_list(text: str) -> List[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_weights(text: str) -> List[float]:
    weights = [float(item) for item in parse_list(text)]
    if not weights:
        raise ValueError("at least one queue weight is required")
    if any(weight < 0.0 for weight in weights):
        raise ValueError("queue weights must be non-negative")
    return weights


def parse_queue_bases(text: str) -> List[str]:
    bases = parse_list(text)
    invalid = [basis for basis in bases if basis not in QUEUE_BASES]
    if invalid:
        raise ValueError(f"unknown queue bases: {invalid}")
    if not bases:
        raise ValueError("at least one queue basis is required")
    return bases


def optional_float(row: Dict[str, str], key: str) -> Optional[float]:
    value = row.get(key, "")
    if value in {"", None}:
        return None
    return float(value)


def congestion_bucket(active_work: float, capacity: float) -> str:
    ratio = active_work / max(1.0, capacity * 12.0)
    if ratio < 0.85:
        return "low"
    if ratio < 1.85:
        return "medium"
    return "high"


def slack_bucket(ratio: float) -> str:
    if ratio < 0.0:
        return "tight"
    if ratio < 1.0:
        return "normal"
    return "loose"


def load_decisions(
    path: str,
    policies: Sequence[str],
    loads: Sequence[str],
) -> List[Dict[str, object]]:
    requested_policies = set(policies)
    requested_loads = set(loads)
    decisions: List[Dict[str, object]] = []
    for row in read_csv(path):
        policy = row.get("policy", "")
        load = row.get("load", "")
        if requested_policies and policy not in requested_policies:
            continue
        if requested_loads and load not in requested_loads:
            continue
        required_work = optional_float(row, "decision_required_work")
        active_work = optional_float(row, "decision_active_work")
        remaining_budget = optional_float(row, "decision_remaining_budget")
        actual_remaining = optional_float(row, "actual_remaining_latency")
        if None in {required_work, active_work, remaining_budget, actual_remaining}:
            continue
        capacity = optional_float(row, "decision_link_capacity")
        if capacity is None:
            capacity = LOAD_CAPACITY.get(load)
        if capacity is None:
            raise ValueError(f"missing capacity for load: {load}")
        ratio = optional_float(row, "decision_congestion_ratio")
        if ratio is None:
            ratio = float(active_work) / max(1.0, capacity * 12.0)
        congestion = row.get("decision_congestion_bucket", "") or congestion_bucket(
            float(active_work), capacity
        )
        role_values = {
            "critical_work": optional_float(row, "decision_active_critical_work"),
            "normal_work": optional_float(row, "decision_active_normal_work"),
            "speculative_work": optional_float(row, "decision_active_speculative_work"),
            "background_work": optional_float(row, "decision_active_background_work"),
            "other_work": optional_float(row, "decision_active_other_work"),
        }
        decisions.append(
            {
                "policy": policy,
                "load": load,
                "template": row.get("template", ""),
                "action": row.get("action", ""),
                "run": row.get("run", ""),
                "seed": row.get("seed", ""),
                "train_seed": row.get("train_seed", ""),
                "eval_seed": row.get("eval_seed", ""),
                "required_work": float(required_work),
                "active_work": float(active_work),
                "remaining_budget": float(remaining_budget),
                "actual_remaining": float(actual_remaining),
                "deadline_miss": int(float(row.get("deadline_miss", "0") or 0)),
                "capacity": capacity,
                "congestion_ratio": ratio,
                "congestion_bucket": congestion,
                "active_flow_count": optional_float(row, "decision_active_flow_count"),
                "weighted_work": optional_float(row, "decision_active_weighted_work"),
                "weight_sum": optional_float(row, "decision_active_weight_sum"),
                **role_values,
            }
        )
    if not decisions:
        raise ValueError("no decision rows matched the requested policies and loads")
    return decisions


def evaluate_candidate(
    decision: Dict[str, object],
    queue_weight: float,
    queue_basis: str = "total",
) -> Dict[str, object]:
    capacity = max(1.0, float(decision["capacity"]))
    own_service_time = float(decision["required_work"]) / capacity
    if queue_basis == "total":
        queue_work = float(decision["active_work"])
    elif queue_basis == "policy_weighted":
        if decision["weighted_work"] is None:
            raise ValueError("policy_weighted queue basis requires decision_active_weighted_work")
        queue_work = float(decision["weighted_work"])
    else:
        raise ValueError(f"unknown queue basis: {queue_basis}")
    queue_time = queue_work / capacity
    estimated = own_service_time + queue_weight * queue_time
    remaining_budget = float(decision["remaining_budget"])
    actual_remaining = float(decision["actual_remaining"])
    normalized_slack = (remaining_budget - estimated) / max(estimated, 1.0)
    return {
        **decision,
        "queue_basis": queue_basis,
        "queue_weight": queue_weight,
        "queue_work": queue_work,
        "own_service_time": own_service_time,
        "queue_time": queue_time,
        "estimated_remaining": estimated,
        "estimation_error": estimated - actual_remaining,
        "absolute_error": abs(estimated - actual_remaining),
        "normalized_slack": normalized_slack,
        "slack_bucket": slack_bucket(normalized_slack),
        "actual_budget_ratio": actual_remaining / max(remaining_budget, 1.0),
    }


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 2 or len(ys) < 2:
        return 0.0
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    centered_x = [value - mean_x for value in xs]
    centered_y = [value - mean_y for value in ys]
    denominator = math.sqrt(
        sum(value * value for value in centered_x) * sum(value * value for value in centered_y)
    )
    if denominator <= 1e-12:
        return 0.0
    return sum(x * y for x, y in zip(centered_x, centered_y)) / denominator


def metric_summary(records: Sequence[Dict[str, object]]) -> Dict[str, object]:
    errors = [float(record["estimation_error"]) for record in records]
    estimates = [float(record["estimated_remaining"]) for record in records]
    actuals = [float(record["actual_remaining"]) for record in records]
    counts = Counter(str(record["slack_bucket"]) for record in records)
    misses = Counter()
    for record in records:
        misses[str(record["slack_bucket"])] += int(record["deadline_miss"])
    miss_ratios = {
        bucket: misses[bucket] / counts[bucket] if counts[bucket] else None
        for bucket in BUCKETS
    }
    risk_order_ok: object = ""
    if all(counts[bucket] for bucket in BUCKETS):
        risk_order_ok = int(
            float(miss_ratios["tight"]) >= float(miss_ratios["normal"])
            >= float(miss_ratios["loose"])
        )
    n = len(records)
    return {
        "n": n,
        "bias": statistics.mean(errors),
        "mae": statistics.mean(abs(error) for error in errors),
        "rmse": math.sqrt(statistics.mean(error * error for error in errors)),
        "pearson": pearson(estimates, actuals),
        "tight_count": counts["tight"],
        "tight_share": counts["tight"] / n,
        "tight_miss_ratio": miss_ratios["tight"] if miss_ratios["tight"] is not None else "",
        "normal_count": counts["normal"],
        "normal_share": counts["normal"] / n,
        "normal_miss_ratio": miss_ratios["normal"] if miss_ratios["normal"] is not None else "",
        "loose_count": counts["loose"],
        "loose_share": counts["loose"] / n,
        "loose_miss_ratio": miss_ratios["loose"] if miss_ratios["loose"] is not None else "",
        "false_loose_count": misses["loose"],
        "false_loose_rate": miss_ratios["loose"] if miss_ratios["loose"] is not None else "",
        "false_tight_count": counts["tight"] - misses["tight"],
        "false_tight_rate": (counts["tight"] - misses["tight"]) / counts["tight"]
        if counts["tight"]
        else "",
        "risk_order_ok": risk_order_ok,
    }


def grouped(
    records: Iterable[Dict[str, object]],
    keys: Sequence[str],
) -> Dict[Tuple[object, ...], List[Dict[str, object]]]:
    result: Dict[Tuple[object, ...], List[Dict[str, object]]] = defaultdict(list)
    for record in records:
        result[tuple(record[key] for key in keys)].append(record)
    return result


def make_weight_summary(records: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = []
    for (basis, weight, policy, load), items in grouped(
        records, ("queue_basis", "queue_weight", "policy", "load")
    ).items():
        rows.append(
            {
                "queue_basis": basis,
                "queue_weight": weight,
                "policy": policy,
                "load": load,
                **metric_summary(items),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["queue_basis"]),
            float(row["queue_weight"]),
            str(row["policy"]),
            str(row["load"]),
        ),
    )


def make_seed_summary(records: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = []
    for (basis, weight, policy, load, train_seed, eval_seed), items in grouped(
        records,
        ("queue_basis", "queue_weight", "policy", "load", "train_seed", "eval_seed"),
    ).items():
        rows.append(
            {
                "queue_basis": basis,
                "queue_weight": weight,
                "policy": policy,
                "load": load,
                "train_seed": train_seed,
                "eval_seed": eval_seed,
                **metric_summary(items),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["queue_basis"]),
            float(row["queue_weight"]),
            str(row["policy"]),
            str(row["load"]),
            str(row["train_seed"]),
            str(row["eval_seed"]),
        ),
    )


def make_bucket_summary(records: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = []
    group_keys = ("queue_basis", "queue_weight", "policy", "load")
    parent_counts = {key: len(items) for key, items in grouped(records, group_keys).items()}
    for (basis, weight, policy, load, bucket), items in grouped(
        records, (*group_keys, "slack_bucket")
    ).items():
        actual_ratios = [float(item["actual_budget_ratio"]) for item in items]
        errors = [float(item["estimation_error"]) for item in items]
        misses = sum(int(item["deadline_miss"]) for item in items)
        rows.append(
            {
                "queue_basis": basis,
                "queue_weight": weight,
                "policy": policy,
                "load": load,
                "slack_bucket": bucket,
                "n": len(items),
                "share": len(items) / parent_counts[(basis, weight, policy, load)],
                "deadline_miss_ratio": misses / len(items),
                "actual_budget_ratio_mean": statistics.mean(actual_ratios),
                "actual_budget_ratio_median": statistics.median(actual_ratios),
                "bias": statistics.mean(errors),
                "mae": statistics.mean(abs(error) for error in errors),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["queue_basis"]),
            float(row["queue_weight"]),
            str(row["policy"]),
            str(row["load"]),
            BUCKET_ORDER[str(row["slack_bucket"])],
        ),
    )


def make_conditional_summary(records: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    rows = []
    parent_keys = ("queue_basis", "queue_weight", "policy", "load", "congestion_bucket")
    parent_counts = {key: len(items) for key, items in grouped(records, parent_keys).items()}
    for (basis, weight, policy, load, congestion, bucket), items in grouped(
        records, (*parent_keys, "slack_bucket")
    ).items():
        misses = sum(int(item["deadline_miss"]) for item in items)
        actual_ratios = [float(item["actual_budget_ratio"]) for item in items]
        rows.append(
            {
                "queue_basis": basis,
                "queue_weight": weight,
                "policy": policy,
                "load": load,
                "congestion_bucket": congestion,
                "slack_bucket": bucket,
                "n": len(items),
                "share_within_congestion": len(items)
                / parent_counts[(basis, weight, policy, load, congestion)],
                "deadline_miss_ratio": misses / len(items),
                "actual_budget_ratio_mean": statistics.mean(actual_ratios),
                "actual_budget_ratio_median": statistics.median(actual_ratios),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            str(row["queue_basis"]),
            float(row["queue_weight"]),
            str(row["policy"]),
            str(row["load"]),
            CONGESTION_ORDER.get(str(row["congestion_bucket"]), 99),
            BUCKET_ORDER[str(row["slack_bucket"])],
        ),
    )


def make_composition_summary(decisions: Sequence[Dict[str, object]]) -> List[Dict[str, object]]:
    complete = [
        decision
        for decision in decisions
        if all(decision[field] is not None for field in ROLE_FIELDS)
    ]
    rows = []
    for (policy, load), items in grouped(complete, ("policy", "load")).items():
        partition_errors = []
        flow_counts = []
        role_totals = {field: 0.0 for field in ROLE_FIELDS}
        total_active_work = 0.0
        total_weighted_work = 0.0
        empty_queue_count = 0
        for item in items:
            total = float(item["active_work"])
            partition_total = sum(float(item[field]) for field in ROLE_FIELDS)
            partition_errors.append(abs(partition_total - total))
            total_active_work += total
            empty_queue_count += int(total <= 1e-12)
            for field in ROLE_FIELDS:
                role_totals[field] += float(item[field])
            if item["weighted_work"] is not None:
                total_weighted_work += float(item["weighted_work"])
            if item["active_flow_count"] is not None:
                flow_counts.append(float(item["active_flow_count"]))
        rows.append(
            {
                "policy": policy,
                "load": load,
                "n": len(items),
                "partition_abs_error_max": max(partition_errors, default=0.0),
                "active_flow_count_mean": statistics.mean(flow_counts) if flow_counts else "",
                "empty_queue_share": empty_queue_count / len(items),
                "critical_work_share": role_totals["critical_work"] / total_active_work
                if total_active_work
                else 0.0,
                "normal_work_share": role_totals["normal_work"] / total_active_work
                if total_active_work
                else 0.0,
                "speculative_work_share": role_totals["speculative_work"] / total_active_work
                if total_active_work
                else 0.0,
                "background_work_share": role_totals["background_work"] / total_active_work
                if total_active_work
                else 0.0,
                "other_work_share": role_totals["other_work"] / total_active_work
                if total_active_work
                else 0.0,
                "weighted_work_ratio": total_weighted_work / total_active_work
                if total_active_work
                else 0.0,
            }
        )
    return sorted(rows, key=lambda row: (str(row["policy"]), str(row["load"])))


def conditional_pair_score(
    records: Sequence[Dict[str, object]],
    min_count: int,
) -> Tuple[int, int]:
    ordered = 0
    total = 0
    for _, items in grouped(
        records, ("policy", "load", "congestion_bucket")
    ).items():
        bucket_items = grouped(items, ("slack_bucket",))
        miss_by_bucket = {}
        for bucket in BUCKETS:
            values = bucket_items.get((bucket,), [])
            if len(values) >= min_count:
                miss_by_bucket[bucket] = sum(int(item["deadline_miss"]) for item in values) / len(values)
        for tighter, looser in (("tight", "normal"), ("normal", "loose"), ("tight", "loose")):
            if tighter not in miss_by_bucket or looser not in miss_by_bucket:
                continue
            total += 1
            ordered += int(miss_by_bucket[tighter] >= miss_by_bucket[looser])
    return ordered, total


def make_ranking(
    records: Sequence[Dict[str, object]],
    weight_summary: Sequence[Dict[str, object]],
    min_conditional_count: int,
) -> List[Dict[str, object]]:
    rows = []
    for (basis, weight), items in grouped(records, ("queue_basis", "queue_weight")).items():
        metrics = metric_summary(items)
        risk_groups = [
            row
            for row in weight_summary
            if row["queue_basis"] == basis
            and float(row["queue_weight"]) == float(weight)
            and row["risk_order_ok"] != ""
        ]
        conditional_ordered, conditional_total = conditional_pair_score(items, min_conditional_count)
        rows.append(
            {
                "queue_basis": basis,
                "queue_weight": weight,
                **metrics,
                "risk_groups_ordered": sum(int(row["risk_order_ok"]) for row in risk_groups),
                "risk_groups_total": len(risk_groups),
                "conditional_pairs_ordered": conditional_ordered,
                "conditional_pairs_total": conditional_total,
                "conditional_pair_order_rate": conditional_ordered / conditional_total
                if conditional_total
                else "",
            }
        )
    for _, basis_rows in grouped(rows, ("queue_basis",)).items():
        for rank, row in enumerate(sorted(basis_rows, key=lambda item: float(item["mae"])), start=1):
            row["mae_rank"] = rank
    return sorted(rows, key=lambda row: (str(row["queue_basis"]), float(row["queue_weight"])))


def fmt(value: object, digits: int = 4) -> str:
    if value == "":
        return "-"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{digits}f}"


def write_markdown_report(
    path: str,
    input_csv: str,
    policies: Sequence[str],
    ranking: Sequence[Dict[str, object]],
    current_weight: float,
    composition_rows: Sequence[Dict[str, object]],
) -> None:
    best_by_basis = {
        basis: min(
            (row for row in ranking if row["queue_basis"] == basis),
            key=lambda row: float(row["mae"]),
        )
        for basis in sorted({str(row["queue_basis"]) for row in ranking})
    }
    lines = [
        "# Slack Queue-weight Calibration Report",
        "",
        f"Input: `{input_csv}`",
        "",
        f"Policies: `{','.join(policies)}`",
        "",
        "## Queue-weight sensitivity",
        "",
        "| Queue basis | Queue weight | N | Bias | MAE | RMSE | Pearson | Tight | Normal | Loose | Loose miss | Risk groups | Conditional order |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ranking:
        risk_groups = f"{row['risk_groups_ordered']}/{row['risk_groups_total']}"
        conditional = (
            f"{row['conditional_pairs_ordered']}/{row['conditional_pairs_total']}"
            if row["conditional_pairs_total"]
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    str(row["queue_basis"]),
                    fmt(row["queue_weight"], 2),
                    fmt(row["n"], 0),
                    fmt(row["bias"]),
                    fmt(row["mae"]),
                    fmt(row["rmse"]),
                    fmt(row["pearson"]),
                    f"{100 * float(row['tight_share']):.2f}%",
                    f"{100 * float(row['normal_share']):.2f}%",
                    f"{100 * float(row['loose_share']):.2f}%",
                    f"{100 * float(row['false_loose_rate']):.2f}%"
                    if row["false_loose_rate"] != ""
                    else "-",
                    risk_groups,
                    conditional,
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            *[
                f"`{basis}` basis 的 MAE 最低离线候选是 `queue_weight={float(best['queue_weight']):g}`。"
                for basis, best in best_by_basis.items()
            ],
            "",
            f"当前运行时参数是 `queue_basis=total, queue_weight={current_weight:g}`。离线候选不会自动替换运行时参数。",
            "",
            "注意：`actual_remaining_latency` 会受到记录该轨迹的 action/policy 影响，因此估计误差不是完全无干预的物理真值。报告同时保留多个 policy，避免只依赖一条策略轨迹。",
        ]
    )
    if composition_rows:
        lines.extend(
            [
                "",
                "## Heavy-load active queue composition",
                "",
                "| Policy | N | Empty queue | Critical | Normal | Speculative | Background | Other | Weighted/total |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in composition_rows:
            if row["load"] != "heavy":
                continue
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(row["policy"]),
                        str(row["n"]),
                        f"{100 * float(row['empty_queue_share']):.1f}%",
                        f"{100 * float(row['critical_work_share']):.1f}%",
                        f"{100 * float(row['normal_work_share']):.1f}%",
                        f"{100 * float(row['speculative_work_share']):.1f}%",
                        f"{100 * float(row['background_work_share']):.1f}%",
                        f"{100 * float(row['other_work_share']):.1f}%",
                        fmt(row["weighted_work_ratio"]),
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Output interpretation",
            "",
            "- `slack_weight_summary.csv`: 每个 queue weight、policy、load 的整体误差和三桶风险。",
            "- `slack_seed_summary.csv`: 每个 calibration train/eval seed 的分 policy、load 结果。",
            "- `slack_bucket_summary.csv`: 每个 bucket 的占比、miss 和 actual/budget。",
            "- `slack_congestion_conditional.csv`: 固定 congestion 后 Slack 是否仍能区分风险。",
            "- `slack_queue_composition.csv`: 新诊断字段中的 active queue role 组成。",
            "- `slack_weight_ranking.csv`: 所有选定轨迹的 queue-weight 汇总，不代表自动选参结论。",
            "",
        ]
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Slack queue-weight calibration offline.")
    parser.add_argument("--input-dir", default="outputs/slack_v2_calibration_seed101")
    parser.add_argument(
        "--input-dirs",
        default="",
        help="Comma-separated experiment directories. When set, overrides --input-dir.",
    )
    parser.add_argument("--output-dir", default="outputs/slack_v2_calibration_seed101/calibration_analysis")
    parser.add_argument("--queue-weights", default="0.5,0.65,0.8,1.0,1.2")
    parser.add_argument(
        "--queue-bases",
        default="total",
        help="Comma-separated queue bases: total,policy_weighted.",
    )
    parser.add_argument(
        "--policies",
        default="critical_path_only,rule_balanced,specnet_agent",
        help="Comma-separated policies whose trajectories are included. Empty means all policies.",
    )
    parser.add_argument("--loads", default="light,medium,heavy")
    parser.add_argument("--current-queue-weight", type=float, default=1.0)
    parser.add_argument("--min-conditional-count", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.min_conditional_count <= 0:
        raise SystemExit("--min-conditional-count must be positive")
    weights = parse_weights(args.queue_weights)
    queue_bases = parse_queue_bases(args.queue_bases)
    policies = parse_list(args.policies)
    loads = parse_list(args.loads)
    input_dirs = parse_list(args.input_dirs) or [args.input_dir]
    input_csvs = [os.path.join(input_dir, "workflow_results.csv") for input_dir in input_dirs]
    decisions = [
        decision
        for input_csv in input_csvs
        for decision in load_decisions(input_csv, policies, loads)
    ]
    candidates = []
    for queue_basis in queue_bases:
        for queue_weight in weights:
            for decision in decisions:
                if queue_basis == "policy_weighted" and decision["weighted_work"] is None:
                    continue
                candidates.append(evaluate_candidate(decision, queue_weight, queue_basis))
    if not candidates:
        raise SystemExit("no candidate records could be evaluated for the requested queue bases")

    weight_summary = make_weight_summary(candidates)
    seed_summary = make_seed_summary(candidates)
    bucket_summary = make_bucket_summary(candidates)
    conditional_summary = make_conditional_summary(candidates)
    composition_summary = make_composition_summary(decisions)
    ranking = make_ranking(candidates, weight_summary, args.min_conditional_count)

    common_metric_fields = [
        "n", "bias", "mae", "rmse", "pearson",
        "tight_count", "tight_share", "tight_miss_ratio",
        "normal_count", "normal_share", "normal_miss_ratio",
        "loose_count", "loose_share", "loose_miss_ratio",
        "false_loose_count", "false_loose_rate", "false_tight_count", "false_tight_rate",
        "risk_order_ok",
    ]
    write_csv(
        os.path.join(args.output_dir, "slack_weight_summary.csv"),
        weight_summary,
        ["queue_basis", "queue_weight", "policy", "load", *common_metric_fields],
    )
    write_csv(
        os.path.join(args.output_dir, "slack_seed_summary.csv"),
        seed_summary,
        [
            "queue_basis", "queue_weight", "policy", "load", "train_seed", "eval_seed",
            *common_metric_fields,
        ],
    )
    write_csv(
        os.path.join(args.output_dir, "slack_bucket_summary.csv"),
        bucket_summary,
        [
            "queue_basis", "queue_weight", "policy", "load", "slack_bucket", "n", "share",
            "deadline_miss_ratio", "actual_budget_ratio_mean", "actual_budget_ratio_median",
            "bias", "mae",
        ],
    )
    write_csv(
        os.path.join(args.output_dir, "slack_congestion_conditional.csv"),
        conditional_summary,
        [
            "queue_basis", "queue_weight", "policy", "load", "congestion_bucket",
            "slack_bucket", "n",
            "share_within_congestion", "deadline_miss_ratio", "actual_budget_ratio_mean",
            "actual_budget_ratio_median",
        ],
    )
    write_csv(
        os.path.join(args.output_dir, "slack_queue_composition.csv"),
        composition_summary,
        [
            "policy", "load", "n", "partition_abs_error_max", "active_flow_count_mean",
            "empty_queue_share", "critical_work_share", "normal_work_share",
            "speculative_work_share", "background_work_share", "other_work_share",
            "weighted_work_ratio",
        ],
    )
    write_csv(
        os.path.join(args.output_dir, "slack_weight_ranking.csv"),
        ranking,
        [
            "mae_rank", "queue_basis", "queue_weight", *common_metric_fields,
            "risk_groups_ordered", "risk_groups_total", "conditional_pairs_ordered",
            "conditional_pairs_total", "conditional_pair_order_rate",
        ],
    )
    write_markdown_report(
        os.path.join(args.output_dir, "SLACK_CALIBRATION_REPORT.md"),
        ",".join(input_csvs),
        policies,
        ranking,
        args.current_queue_weight,
        composition_summary,
    )
    print("Wrote Slack calibration analysis to:", os.path.abspath(args.output_dir))
    for basis in sorted({str(row["queue_basis"]) for row in ranking}):
        best = min(
            (row for row in ranking if row["queue_basis"] == basis),
            key=lambda row: float(row["mae"]),
        )
        print(
            "Offline MAE minimum:",
            f"queue_basis={basis}",
            f"queue_weight={float(best['queue_weight']):g}",
            f"mae={float(best['mae']):.4f}",
        )
    if not composition_summary:
        print("NOTE: input predates role-breakdown diagnostics; queue composition output is empty.")


if __name__ == "__main__":
    main()
