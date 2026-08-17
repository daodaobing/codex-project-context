"""Scoring support for Pilot V1.

Provides the result schema, per-run record loader, and summary tables for the
metrics defined in protocol.json. Scoring itself is performed from run
transcripts (rollout JSONL + final answers + workspace diffs); this module
keeps the record format stable so the final report can be reproduced.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmarks" / "results" / "pilot-runs"


EMPTY_RUN = {
    "task_id": None,
    "arm": None,
    "status": "pending",  # pending | ok | infra_failed | invalid
    "agent_session": None,
    "final_answer": None,
    "constraint_compliance": None,  # PASS | FAIL | N/A (T3/T4)
    "task_success": None,  # PASS | FAIL
    "wrong_direction": False,
    "rework": False,
    "exploration": {
        "tool_calls": 0,
        "search_calls": 0,
        "read_calls": 0,
        "source_files_read": 0,
        "docs_read": 0,
        "test_runs": 0,
        "failed_commands": 0,
        "candidate_tool_calls": 0,
        "candidates_returned": 0,
        "candidates_read": 0,
        "non_candidate_docs_read": 0,
    },
    "token": {
        "input": None,
        "uncached_input": None,
        "cache_read": None,
        "output": None,
        "total": None,
    },
    "latency_sec": None,
    "evidence_notes": "",
}


def run_path(task_id: str, arm: str) -> Path:
    return OUT / task_id / arm


def save_run(task_id: str, arm: str, record: dict) -> Path:
    target = run_path(task_id, arm) / "result.json"
    target.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def load_runs() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for task_dir in sorted(p for p in OUT.iterdir() if p.is_dir()):
        for arm_dir in sorted(p for p in task_dir.iterdir() if p.is_dir()):
            result = arm_dir / "result.json"
            if result.exists():
                out[f"{task_dir.name}/{arm_dir.name}"] = json.loads(
                    result.read_text(encoding="utf-8")
                )
    return out


def summary_table(records: dict[str, dict]) -> dict:
    by_arm: dict[str, dict] = {}
    for key, rec in records.items():
        arm = rec["arm"]
        bucket = by_arm.setdefault(
            arm,
            {
                "n": 0,
                "task_success": 0,
                "constraint_pass": 0,
                "constraint_fail": 0,
                "wrong_direction": 0,
                "rework": 0,
                "total_tool_calls": 0,
                "total_search": 0,
                "total_reads": 0,
                "candidate_calls": 0,
            },
        )
        if rec["status"] != "ok":
            continue
        bucket["n"] += 1
        bucket["task_success"] += 1 if rec["task_success"] == "PASS" else 0
        bucket["constraint_pass"] += 1 if rec["constraint_compliance"] == "PASS" else 0
        bucket["constraint_fail"] += 1 if rec["constraint_compliance"] == "FAIL" else 0
        bucket["wrong_direction"] += 1 if rec["wrong_direction"] else 0
        bucket["rework"] += 1 if rec["rework"] else 0
        bucket["total_tool_calls"] += rec["exploration"]["tool_calls"]
        bucket["total_search"] += rec["exploration"]["search_calls"]
        bucket["total_reads"] += rec["exploration"]["read_calls"]
        bucket["candidate_calls"] += rec["exploration"]["candidate_tool_calls"]
    return by_arm
