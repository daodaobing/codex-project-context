"""Runner documentation and per-run prompt materialization for Pilot V1.

The actual execution uses Codex sub-agents (one per run, context-free). This
script materializes the exact per-run prompt (task prompt + arm suffix) into
``benchmarks/results/pilot-runs/<task_id>/<arm>/prompt.txt`` so the frozen
protocol is auditable and scoring has a stable reference.

Usage:
    python benchmarks/value_pilot/runner.py [--tasks t1-1-click-confirm] [--arms A B C]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmarks" / "results" / "pilot-runs"
PROTOCOL_PATH = ROOT / "benchmarks" / "value_pilot" / "protocol.json"
TASKS_PATH = ROOT / "benchmarks" / "value_pilot" / "tasks.json"


def load_protocol() -> dict:
    with PROTOCOL_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_tasks() -> list[dict]:
    with TASKS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["tasks"]


def materialize(task: dict, arm: str, suffix: str) -> Path:
    run_dir = OUT / task["task_id"] / arm
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = run_dir / "prompt.txt"
    prompt = task["prompt"]
    if suffix:
        prompt = f"{prompt}\n\n{suffix}"
    prompt_path.write_text(prompt, encoding="utf-8")
    (run_dir / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return prompt_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", nargs="*", default=None)
    parser.add_argument("--arms", nargs="*", default=["A", "B", "C"])
    args = parser.parse_args()

    protocol = load_protocol()
    tasks = load_tasks()
    if args.tasks:
        wanted = set(args.tasks)
        tasks = [t for t in tasks if t["task_id"] in wanted]

    for task in tasks:
        for arm in args.arms:
            suffix = protocol["arms"][arm]["prompt_suffix"]
            path = materialize(task, arm, suffix)
            print(f"materialized {task['task_id']}/{arm} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
