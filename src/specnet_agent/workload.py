"""Deterministic synthetic workflow generation."""
from __future__ import annotations

import random
from typing import List

from .config import LOAD_CONFIG, TEMPLATES
from .math_utils import lognormal_size
from .models import BranchSpec, WorkflowSpec

def generate_workload(
    seed: int,
    load: str,
    duration: int,
    max_workflows: int,
) -> List[WorkflowSpec]:
    rng = random.Random(seed)
    config = LOAD_CONFIG[load]
    specs: List[WorkflowSpec] = []
    t = 0.0
    workflow_id = 0
    template_names = list(TEMPLATES.keys())
    template_weights = [0.28, 0.24, 0.30, 0.18]

    while t < duration and len(specs) < max_workflows:
        interarrival = rng.expovariate(1.0 / config["mean_interarrival"])
        if rng.random() < config["burst_probability"]:
            interarrival *= 0.18
        t += interarrival
        if t >= duration:
            break

        template = rng.choices(template_names, weights=template_weights, k=1)[0]
        meta = TEMPLATES[template]
        required_count = meta["required_branches"]
        branches: List[BranchSpec] = []
        for idx, service_type in enumerate(meta["branch_types"]):
            branches.append(
                BranchSpec(
                    service_type=service_type,
                    size=lognormal_size(rng, service_type),
                    required=idx < required_count,
                )
            )

        background_sizes = [
            lognormal_size(rng, "background") for _ in range(meta["background_count"])
        ]
        deadline_noise = rng.uniform(0.88, 1.18)
        specs.append(
            WorkflowSpec(
                workflow_id=workflow_id,
                arrival_time=int(t),
                template=template,
                deadline=meta["deadline_base"] * deadline_noise,
                planner_size=lognormal_size(rng, "planner"),
                branches=branches,
                llm_size=lognormal_size(rng, "llm"),
                judge_size=lognormal_size(rng, "judge"),
                background_sizes=background_sizes,
            )
        )
        workflow_id += 1

    return specs
