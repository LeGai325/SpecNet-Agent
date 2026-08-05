#!/usr/bin/env python3
"""Save deterministic workflow inputs used by the SpecNet-Agent simulator."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict


def load_experiment_module(script_path: str):
    spec = importlib.util.spec_from_file_location("specnet_agent_experiment", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import experiment module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="Export generated workflow traces as JSONL inputs.")
    parser.add_argument("--script", required=True, help="Path to specnet_agent_experiment.py.")
    parser.add_argument("--output-dir", required=True, help="Output directory for JSONL workload inputs.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--eval-runs", type=int, default=5)
    parser.add_argument("--duration", type=int, default=2600)
    parser.add_argument("--max-workflows", type=int, default=120)
    parser.add_argument("--loads", default="light,medium,heavy")
    parser.add_argument("--workload-profile", default="synthetic")
    parser.add_argument("--trace-profile-path", default="")
    args = parser.parse_args()

    module = load_experiment_module(args.script)
    loads = [item.strip() for item in args.loads.split(",") if item.strip()]
    os.makedirs(args.output_dir, exist_ok=True)

    manifest = []
    for load in loads:
        load_index = list(module.LOAD_CONFIG).index(load)
        for run_index in range(args.eval_runs):
            workload_seed = args.seed + 20000 + 1000 * run_index + 17 * load_index
            specs = module.generate_workload(
                workload_seed,
                load,
                args.duration,
                args.max_workflows,
                workload_profile=args.workload_profile,
                phase="test",
                trace_profile_path=args.trace_profile_path or None,
            )
            filename = f"workload_{load}_run_{run_index}_seed_{workload_seed}.jsonl"
            path = os.path.join(args.output_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                for workflow in specs:
                    f.write(json.dumps(asdict(workflow), sort_keys=True) + "\n")
            manifest.append(
                {
                    "load": load,
                    "run": run_index,
                    "seed": workload_seed,
                    "workflows": len(specs),
                    "workload_profile": args.workload_profile,
                    "trace_profile_path": args.trace_profile_path,
                    "file": filename,
                }
            )

    with open(os.path.join(args.output_dir, "workload_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print("Wrote workload inputs to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
