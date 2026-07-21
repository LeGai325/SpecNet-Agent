"""Export deterministic generated workloads as JSONL."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from dataclasses import asdict
from typing import Optional, Sequence

from .. import config as package_config
from ..workload import generate_workload as package_generate_workload


def load_experiment_module(script_path: str):
    spec = importlib.util.spec_from_file_location("specnet_agent_experiment", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import experiment module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Export generated workflow traces as JSONL inputs.")
    parser.add_argument(
        "--script",
        default="",
        help="Optional compatibility path to specnet_agent_experiment.py; package generator is used by default.",
    )
    parser.add_argument("--output-dir", required=True, help="Output directory for JSONL workload inputs.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--eval-runs", type=int, default=5)
    parser.add_argument("--duration", type=int, default=2600)
    parser.add_argument("--max-workflows", type=int, default=120)
    parser.add_argument("--loads", default="light,medium,heavy")
    args = parser.parse_args(argv)

    module = load_experiment_module(args.script) if args.script else None
    load_config = module.LOAD_CONFIG if module else package_config.LOAD_CONFIG
    generator = module.generate_workload if module else package_generate_workload
    loads = [item.strip() for item in args.loads.split(",") if item.strip()]
    invalid = [load for load in loads if load not in load_config]
    if invalid:
        raise SystemExit(f"Invalid loads: {invalid}")
    os.makedirs(args.output_dir, exist_ok=True)

    manifest = []
    for load in loads:
        load_index = list(load_config).index(load)
        for run_index in range(args.eval_runs):
            workload_seed = args.seed + 20000 + 1000 * run_index + 17 * load_index
            specs = generator(workload_seed, load, args.duration, args.max_workflows)
            filename = f"workload_{load}_run_{run_index}_seed_{workload_seed}.jsonl"
            path = os.path.join(args.output_dir, filename)
            with open(path, "w", encoding="utf-8") as handle:
                for workflow in specs:
                    handle.write(json.dumps(asdict(workflow), sort_keys=True) + "\n")
            manifest.append(
                {"load": load, "run": run_index, "seed": workload_seed,
                 "workflows": len(specs), "file": filename}
            )
    with open(os.path.join(args.output_dir, "workload_manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    print("Wrote workload inputs to:", os.path.abspath(args.output_dir))


if __name__ == "__main__":
    main()
