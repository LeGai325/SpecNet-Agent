"""Domain data structures used by the workload, controller, and simulator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .config import StateKey

@dataclass
class BranchSpec:
    service_type: str
    size: float
    required: bool


@dataclass
class WorkflowSpec:
    workflow_id: int
    arrival_time: int
    template: str
    deadline: float
    planner_size: float
    branches: List[BranchSpec]
    llm_size: float
    judge_size: float
    background_sizes: List[float]


@dataclass
class Flow:
    flow_id: int
    workflow_id: int
    service_type: str
    size: float
    remaining: float
    role: str
    stage: str
    required: bool = False
    speculative: bool = False
    background: bool = False
    created_at: int = 0
    completed_at: Optional[int] = None
    cancelled: bool = False
    served: float = 0.0


@dataclass
class WorkflowRuntime:
    spec: WorkflowSpec
    stage: str = "not_arrived"
    start_time: Optional[int] = None
    complete_time: Optional[int] = None
    planner_flow: Optional[int] = None
    branch_flows: List[int] = field(default_factory=list)
    required_branch_flows: List[int] = field(default_factory=list)
    speculative_branch_flows: List[int] = field(default_factory=list)
    background_flows: List[int] = field(default_factory=list)
    llm_flow: Optional[int] = None
    judge_flow: Optional[int] = None
    action: Optional[str] = None
    decision_state: Optional[StateKey] = None
    decision_time: Optional[int] = None
    decision_remaining_budget: Optional[float] = None
    decision_required_work: Optional[float] = None
    decision_active_work: Optional[float] = None
    decision_link_capacity: Optional[float] = None
    decision_active_flow_count: Optional[int] = None
    decision_active_critical_work: Optional[float] = None
    decision_active_normal_work: Optional[float] = None
    decision_active_speculative_work: Optional[float] = None
    decision_active_background_work: Optional[float] = None
    decision_active_other_work: Optional[float] = None
    decision_active_weighted_work: Optional[float] = None
    decision_active_weight_sum: Optional[float] = None
    decision_congestion_ratio: Optional[float] = None
    decision_congestion_bucket: Optional[str] = None
    decision_spec_pressure_ratio: Optional[float] = None
    decision_spec_pressure_bucket: Optional[str] = None
    decision_queue_time: Optional[float] = None
    decision_estimated_remaining_time: Optional[float] = None
    decision_slack_ratio: Optional[float] = None
    decision_slack_bucket: Optional[str] = None
    quality: float = 1.0
    wasted_speculative_bytes: float = 0.0
    background_bytes_served: float = 0.0

    @property
    def deadline_time(self) -> float:
        return self.spec.arrival_time + self.spec.deadline
