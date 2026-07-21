"""Discrete-time workflow and network simulator."""
from __future__ import annotations

import math
import statistics
from typing import Dict, Iterable, List, Optional

from .config import (
    ACTION_CONFIG, DEFAULT_SLACK_QUEUE_BASIS, DEFAULT_SLACK_QUEUE_WEIGHT,
    LOAD_CONFIG, SLACK_LOOSE_THRESHOLD, SLACK_QUEUE_BASES, SLACK_TIGHT_THRESHOLD,
    TEMPLATES,
)
from .math_utils import percentile
from .models import Flow, WorkflowRuntime, WorkflowSpec
from .policies.base import Policy


class Simulator:
    def __init__(
        self,
        specs: List[WorkflowSpec],
        policy: Policy,
        load: str,
        seed: int,
        duration: int,
        max_time: int,
        quality_weight: float = 1.60,
        slack_queue_basis: str = DEFAULT_SLACK_QUEUE_BASIS,
        slack_queue_weight: float = DEFAULT_SLACK_QUEUE_WEIGHT,
    ) -> None:
        if slack_queue_basis not in SLACK_QUEUE_BASES:
            raise ValueError(f"unknown Slack queue basis: {slack_queue_basis}")
        if slack_queue_weight < 0.0:
            raise ValueError("Slack queue weight must be non-negative")
        self.specs = list(specs)
        self.policy = policy
        self.load = load
        self.seed = seed
        self.duration = duration
        self.max_time = max_time
        self.quality_weight = quality_weight
        self.slack_queue_basis = slack_queue_basis
        self.slack_queue_weight = slack_queue_weight
        self.capacity = LOAD_CONFIG[load]["capacity"]
        self.time = 0
        self.next_flow_id = 0
        self.flows: Dict[int, Flow] = {}
        self.workflows: Dict[int, WorkflowRuntime] = {
            spec.workflow_id: WorkflowRuntime(spec=spec) for spec in specs
        }
        self.completed_workflows: List[WorkflowRuntime] = []
        self.total_capacity = 0.0
        self.total_served = 0.0
        self.queue_pressure_samples: List[float] = []

    def new_flow(
        self,
        workflow: WorkflowRuntime,
        service_type: str,
        size: float,
        role: str,
        stage: str,
        required: bool = False,
        speculative: bool = False,
        background: bool = False,
    ) -> int:
        flow = Flow(
            flow_id=self.next_flow_id,
            workflow_id=workflow.spec.workflow_id,
            service_type=service_type,
            size=size,
            remaining=size,
            role=role,
            stage=stage,
            required=required,
            speculative=speculative,
            background=background,
            created_at=self.time,
        )
        self.flows[flow.flow_id] = flow
        self.next_flow_id += 1
        return flow.flow_id

    def active_flows(self) -> List[Flow]:
        return [
            flow
            for flow in self.flows.values()
            if flow.completed_at is None and not flow.cancelled and flow.remaining > 1e-9
        ]

    def remaining_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows())

    def speculative_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows() if flow.speculative)

    def background_active_bytes(self) -> float:
        return sum(flow.remaining for flow in self.active_flows() if flow.background)

    def active_queue_diagnostics(self) -> Dict[str, float]:
        diagnostics = {
            "flow_count": 0.0,
            "critical_work": 0.0,
            "normal_work": 0.0,
            "speculative_work": 0.0,
            "background_work": 0.0,
            "other_work": 0.0,
            "weighted_work": 0.0,
            "weight_sum": 0.0,
        }
        for flow in self.active_flows():
            diagnostics["flow_count"] += 1.0
            if flow.speculative:
                category = "speculative_work"
            elif flow.background:
                category = "background_work"
            elif flow.role in {"critical_control", "critical_bulk"}:
                category = "critical_work"
            elif flow.role == "normal":
                category = "normal_work"
            else:
                category = "other_work"
            diagnostics[category] += flow.remaining
            weight = max(0.0, self.policy.flow_weight(flow, self))
            diagnostics["weighted_work"] += flow.remaining * weight
            diagnostics["weight_sum"] += weight
        return diagnostics

    def congestion_ratio(self) -> float:
        # A rough pressure metric: active bytes relative to 12 scheduling epochs of capacity.
        return self.remaining_active_bytes() / max(1.0, self.capacity * 12.0)

    def congestion_level(self) -> str:
        ratio = self.congestion_ratio()
        if ratio < 0.85:
            return "low"
        if ratio < 1.85:
            return "medium"
        return "high"

    def background_pressure(self) -> float:
        total = self.remaining_active_bytes()
        return self.background_active_bytes() / total if total else 0.0

    def speculative_pressure_bucket(self) -> str:
        total = self.remaining_active_bytes()
        ratio = self.speculative_active_bytes() / total if total else 0.0
        if ratio < 0.15:
            return "low_spec"
        if ratio < 0.35:
            return "mid_spec"
        return "high_spec"

    def workflow_required_work(self, workflow: WorkflowRuntime) -> float:
        required_branch_work = sum(branch.size for branch in workflow.spec.branches if branch.required)
        return required_branch_work + workflow.spec.llm_size + workflow.spec.judge_size

    def slack_queue_work(self, queue_diagnostics: Optional[Dict[str, float]] = None) -> float:
        if self.slack_queue_basis == "total":
            return self.remaining_active_bytes()
        diagnostics = queue_diagnostics or self.active_queue_diagnostics()
        return diagnostics["weighted_work"]

    def workflow_estimated_remaining_time(self, workflow: WorkflowRuntime) -> float:
        own_service_time = self.workflow_required_work(workflow) / max(1.0, self.capacity)
        queue_time = self.slack_queue_work() / max(1.0, self.capacity)
        return own_service_time + self.slack_queue_weight * queue_time

    def workflow_budget_ratio(self, workflow: WorkflowRuntime) -> float:
        remaining_budget = workflow.deadline_time - self.time
        return remaining_budget / max(1.0, workflow.spec.deadline)

    def workflow_slack_ratio(self, workflow: WorkflowRuntime) -> float:
        remaining_budget = workflow.deadline_time - self.time
        estimated_remaining_time = self.workflow_estimated_remaining_time(workflow)
        absolute_slack = remaining_budget - estimated_remaining_time
        return absolute_slack / max(1.0, estimated_remaining_time)

    def workflow_slack_bucket(self, workflow: WorkflowRuntime) -> str:
        ratio = self.workflow_slack_ratio(workflow)
        if ratio < SLACK_TIGHT_THRESHOLD:
            return "tight"
        if ratio < SLACK_LOOSE_THRESHOLD:
            return "normal"
        return "loose"

    def record_slack_decision(self, workflow: WorkflowRuntime) -> None:
        queue_diagnostics = self.active_queue_diagnostics()
        workflow.decision_time = self.time
        workflow.decision_remaining_budget = workflow.deadline_time - self.time
        workflow.decision_required_work = self.workflow_required_work(workflow)
        workflow.decision_active_work = self.remaining_active_bytes()
        workflow.decision_link_capacity = self.capacity
        workflow.decision_active_flow_count = int(queue_diagnostics["flow_count"])
        workflow.decision_active_critical_work = queue_diagnostics["critical_work"]
        workflow.decision_active_normal_work = queue_diagnostics["normal_work"]
        workflow.decision_active_speculative_work = queue_diagnostics["speculative_work"]
        workflow.decision_active_background_work = queue_diagnostics["background_work"]
        workflow.decision_active_other_work = queue_diagnostics["other_work"]
        workflow.decision_active_weighted_work = queue_diagnostics["weighted_work"]
        workflow.decision_active_weight_sum = queue_diagnostics["weight_sum"]
        workflow.decision_congestion_ratio = self.congestion_ratio()
        workflow.decision_congestion_bucket = self.congestion_level()
        workflow.decision_spec_pressure_ratio = (
            workflow.decision_active_speculative_work / workflow.decision_active_work
            if workflow.decision_active_work
            else 0.0
        )
        workflow.decision_spec_pressure_bucket = self.speculative_pressure_bucket()
        workflow.decision_queue_time = self.slack_queue_work(queue_diagnostics) / max(1.0, self.capacity)
        workflow.decision_estimated_remaining_time = self.workflow_estimated_remaining_time(workflow)
        workflow.decision_slack_ratio = self.workflow_slack_ratio(workflow)
        workflow.decision_slack_bucket = self.workflow_slack_bucket(workflow)

    def spawn_arrivals(self) -> None:
        for workflow in self.workflows.values():
            if workflow.stage == "not_arrived" and workflow.spec.arrival_time <= self.time:
                workflow.stage = "planner"
                workflow.start_time = self.time
                workflow.planner_flow = self.new_flow(
                    workflow,
                    "planner",
                    workflow.spec.planner_size,
                    role="critical_control",
                    stage="planner",
                    required=True,
                )

    def completed(self, flow_id: Optional[int]) -> bool:
        if flow_id is None:
            return False
        flow = self.flows[flow_id]
        return flow.completed_at is not None

    def all_completed(self, flow_ids: Iterable[int]) -> bool:
        return all(self.completed(flow_id) for flow_id in flow_ids)

    def branch_count_for_action(self, workflow: WorkflowRuntime, action: str) -> int:
        meta = TEMPLATES[workflow.spec.template]
        max_branches = meta["max_branches"]
        required = meta["required_branches"]
        config = ACTION_CONFIG[action]
        if action == "critical_only":
            return required
        by_fraction = math.ceil(max_branches * config["fanout_fraction"])
        by_extra = required + int(config["extra_branches"])
        return max(required, min(max_branches, by_fraction, by_extra))

    def quality_for_action(self, workflow: WorkflowRuntime, action: str, branch_count: int) -> float:
        meta = TEMPLATES[workflow.spec.template]
        max_branches = meta["max_branches"]
        required = meta["required_branches"]
        if max_branches == required:
            return 1.0
        extra_ratio = (branch_count - required) / max(1, max_branches - required)
        smooth_quality = 0.74 + 0.26 * math.log1p(4 * extra_ratio) / math.log1p(4)
        return min(1.0, max(ACTION_CONFIG[action]["quality_floor"], smooth_quality))

    def spawn_branches(self, workflow: WorkflowRuntime) -> None:
        self.record_slack_decision(workflow)
        action = self.policy.decide_action(self, workflow)
        workflow.action = action
        branch_count = self.branch_count_for_action(workflow, action)
        workflow.quality = self.quality_for_action(workflow, action, branch_count)
        workflow.stage = "branches"

        for index, branch in enumerate(workflow.spec.branches[:branch_count]):
            required = branch.required

            speculative = not required
            role = "critical_bulk" if required and branch.size >= 32.0 else "critical_control" if required else "speculative"
            flow_id = self.new_flow(
                workflow,
                branch.service_type,
                branch.size,
                role=role,
                stage="branch",
                required=required,
                speculative=speculative,
            )
            workflow.branch_flows.append(flow_id)
            if required:
                workflow.required_branch_flows.append(flow_id)
            else:
                workflow.speculative_branch_flows.append(flow_id)

        config = ACTION_CONFIG[action]
        if config["spawn_background"]:
            for size in workflow.spec.background_sizes:
                scaled_size = max(1.0, size * config["background_scale"])
                flow_id = self.new_flow(
                    workflow,
                    "background",
                    scaled_size,
                    role="background",
                    stage="background",
                    background=True,
                )
                workflow.background_flows.append(flow_id)

    def progress_workflows(self) -> None:
        for workflow in self.workflows.values():
            if workflow.complete_time is not None:
                continue
            if workflow.stage == "planner" and self.completed(workflow.planner_flow):
                self.spawn_branches(workflow)
            elif workflow.stage == "branches" and self.all_completed(workflow.required_branch_flows):
                workflow.stage = "llm"
                workflow.llm_flow = self.new_flow(
                    workflow,
                    "llm",
                    workflow.spec.llm_size,
                    role="critical_bulk",
                    stage="llm",
                    required=True,
                )
            elif workflow.stage == "llm" and self.completed(workflow.llm_flow):
                workflow.stage = "judge"
                workflow.judge_flow = self.new_flow(
                    workflow,
                    "judge",
                    workflow.spec.judge_size,
                    role="critical_control",
                    stage="judge",
                    required=True,
                )
            elif workflow.stage == "judge" and self.completed(workflow.judge_flow):
                self.finish_workflow(workflow)

    def finish_workflow(self, workflow: WorkflowRuntime) -> None:
        workflow.stage = "done"
        workflow.complete_time = self.time
        for flow_id in workflow.speculative_branch_flows:
            flow = self.flows[flow_id]
            workflow.wasted_speculative_bytes += flow.served
            if flow.completed_at is None:
                flow.cancelled = True
        for flow_id in workflow.background_flows:
            flow = self.flows[flow_id]
            workflow.background_bytes_served += flow.served
            if flow.completed_at is None:
                flow.cancelled = True
        self.completed_workflows.append(workflow)
        self.policy.on_workflow_complete(workflow, self)

    def serve_active_flows(self) -> None:
        capacity = self.capacity
        active = self.active_flows()
        self.total_capacity += capacity
        if not active:
            return

        pressure = sum(flow.remaining for flow in active) / max(1.0, capacity)
        self.queue_pressure_samples.append(pressure)

        # Weighted max-min style allocation. It avoids wasting capacity when
        # small flows finish during the epoch.
        remaining_capacity = capacity
        candidates = list(active)
        while candidates and remaining_capacity > 1e-9:
            weighted = [(flow, max(0.0, self.policy.flow_weight(flow, self))) for flow in candidates]
            total_weight = sum(weight for _, weight in weighted)
            if total_weight <= 1e-12:
                break
            served_this_round = 0.0
            next_candidates: List[Flow] = []
            progressed: List[Flow] = []
            for flow, weight in weighted:
                if remaining_capacity <= 1e-9:
                    break
                share = remaining_capacity * weight / total_weight
                served = min(flow.remaining, share)
                if served <= 1e-12:
                    continue
                flow.remaining -= served
                flow.served += served
                self.total_served += served
                served_this_round += served
                progressed.append(flow)
            for flow in candidates:
                if flow.remaining <= 1e-9 and flow.completed_at is None:
                    flow.completed_at = self.time + 1
                elif flow.remaining > 1e-9:
                    next_candidates.append(flow)
            remaining_capacity -= served_this_round
            if served_this_round <= 1e-12 or not progressed:
                break
            candidates = next_candidates

    def workflow_reward(self, workflow: WorkflowRuntime) -> float:
        if workflow.complete_time is None:
            return -10.0
        latency = workflow.complete_time - workflow.spec.arrival_time
        normalized_latency = latency / max(1.0, workflow.spec.deadline)
        deadline_miss = 1.0 if latency > workflow.spec.deadline else 0.0
        wasted_norm = workflow.wasted_speculative_bytes / max(1.0, sum(b.size for b in workflow.spec.branches))
        quality_loss = 1.0 - workflow.quality
        background_norm = workflow.background_bytes_served / max(1.0, sum(workflow.spec.background_sizes))
        return -(
            1.00 * normalized_latency
            + 3.00 * deadline_miss
            + 0.80 * wasted_norm
            + self.quality_weight * quality_loss
            + 0.15 * background_norm
        )

    def run(self) -> Dict[str, object]:
        self.policy.reset_for_run()
        for self.time in range(self.max_time):
            self.spawn_arrivals()
            self.progress_workflows()
            self.serve_active_flows()
            self.progress_workflows()

            all_arrived = all(w.stage != "not_arrived" for w in self.workflows.values())
            no_active = not self.active_flows()
            all_done_or_arrived = all(
                w.complete_time is not None or w.stage != "not_arrived"
                for w in self.workflows.values()
            )
            if all_arrived and no_active and all_done_or_arrived:
                break

        # Mark unfinished workflows as timed out at max_time.
        for workflow in self.workflows.values():
            if workflow.complete_time is None and workflow.stage != "not_arrived":
                workflow.complete_time = self.max_time
                self.completed_workflows.append(workflow)

        return self.summary()

    def summary(self) -> Dict[str, object]:
        records = []
        for workflow in self.completed_workflows:
            latency = workflow.complete_time - workflow.spec.arrival_time if workflow.complete_time is not None else 0.0
            records.append(
                {
                    "workflow_id": workflow.spec.workflow_id,
                    "template": workflow.spec.template,
                    "arrival_time": workflow.spec.arrival_time,
                    "deadline": workflow.spec.deadline,
                    "latency": latency,
                    "deadline_miss": 1 if latency > workflow.spec.deadline else 0,
                    "quality": workflow.quality,
                    "action": workflow.action or "none",
                    "decision_time": workflow.decision_time if workflow.decision_time is not None else "",
                    "decision_remaining_budget": workflow.decision_remaining_budget
                    if workflow.decision_remaining_budget is not None
                    else "",
                    "decision_required_work": workflow.decision_required_work
                    if workflow.decision_required_work is not None
                    else "",
                    "decision_active_work": workflow.decision_active_work
                    if workflow.decision_active_work is not None
                    else "",
                    "decision_link_capacity": workflow.decision_link_capacity
                    if workflow.decision_link_capacity is not None
                    else "",
                    "decision_active_flow_count": workflow.decision_active_flow_count
                    if workflow.decision_active_flow_count is not None
                    else "",
                    "decision_active_critical_work": workflow.decision_active_critical_work
                    if workflow.decision_active_critical_work is not None
                    else "",
                    "decision_active_normal_work": workflow.decision_active_normal_work
                    if workflow.decision_active_normal_work is not None
                    else "",
                    "decision_active_speculative_work": workflow.decision_active_speculative_work
                    if workflow.decision_active_speculative_work is not None
                    else "",
                    "decision_active_background_work": workflow.decision_active_background_work
                    if workflow.decision_active_background_work is not None
                    else "",
                    "decision_active_other_work": workflow.decision_active_other_work
                    if workflow.decision_active_other_work is not None
                    else "",
                    "decision_active_weighted_work": workflow.decision_active_weighted_work
                    if workflow.decision_active_weighted_work is not None
                    else "",
                    "decision_active_weight_sum": workflow.decision_active_weight_sum
                    if workflow.decision_active_weight_sum is not None
                    else "",
                    "decision_congestion_ratio": workflow.decision_congestion_ratio
                    if workflow.decision_congestion_ratio is not None
                    else "",
                    "decision_congestion_bucket": workflow.decision_congestion_bucket or "",
                    "decision_spec_pressure_ratio": workflow.decision_spec_pressure_ratio
                    if workflow.decision_spec_pressure_ratio is not None
                    else "",
                    "decision_spec_pressure_bucket": workflow.decision_spec_pressure_bucket or "",
                    "decision_queue_time": workflow.decision_queue_time
                    if workflow.decision_queue_time is not None
                    else "",
                    "decision_estimated_remaining_time": workflow.decision_estimated_remaining_time
                    if workflow.decision_estimated_remaining_time is not None
                    else "",
                    "decision_slack_ratio": workflow.decision_slack_ratio
                    if workflow.decision_slack_ratio is not None
                    else "",
                    "decision_slack_bucket": workflow.decision_slack_bucket or "",
                    "actual_remaining_latency": workflow.complete_time - workflow.decision_time
                    if workflow.decision_time is not None
                    else "",
                    "wasted_speculative_bytes": workflow.wasted_speculative_bytes,
                    "background_bytes_served": workflow.background_bytes_served,
                }
            )
        latencies = [row["latency"] for row in records]
        completed = len(records)
        miss_ratio = sum(row["deadline_miss"] for row in records) / max(1, completed)
        total_wasted = sum(row["wasted_speculative_bytes"] for row in records)
        total_bg = sum(row["background_bytes_served"] for row in records)
        avg_quality = sum(row["quality"] for row in records) / max(1, completed)
        return {
            "policy": self.policy.name,
            "load": self.load,
            "seed": self.seed,
            "slack_queue_basis": self.slack_queue_basis,
            "slack_queue_weight": self.slack_queue_weight,
            "completed": completed,
            "mean_latency": statistics.mean(latencies) if latencies else 0.0,
            "p95_latency": percentile(latencies, 0.95),
            "p99_latency": percentile(latencies, 0.99),
            "deadline_miss_ratio": miss_ratio,
            "wasted_speculative_bytes_per_workflow": total_wasted / max(1, completed),
            "background_bytes_served_per_workflow": total_bg / max(1, completed),
            "avg_quality": avg_quality,
            "link_utilization": self.total_served / max(1.0, self.total_capacity),
            "avg_queue_pressure": statistics.mean(self.queue_pressure_samples) if self.queue_pressure_samples else 0.0,
            "action_counts": dict(self.policy.action_counter),
            "workflow_records": records,
        }
