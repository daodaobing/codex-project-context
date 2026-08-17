"""Protocol integrity tests for Pilot V1 (frozen files only; no runs executed)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
PILOT = ROOT / "benchmarks" / "value_pilot"

FORBIDDEN_TERMS = [
    "benchmark",
    "recall",
    "ratchet",
    "ground truth",
    "required_docs",
    "get_context_candidates",
    "project-context",
    "pilot",
    "arm",
    "adoption",
    "0/9",
]


def _task_text(task: dict) -> str:
    return json.dumps(task, ensure_ascii=False)


def test_tasks_frozen_and_structured() -> None:
    data = json.loads((PILOT / "tasks.json").read_text(encoding="utf-8"))
    assert data["frozen"] is True
    tasks = data["tasks"]
    assert len(tasks) == 12
    counts = {}
    for task in tasks:
        counts[task["category"]] = counts.get(task["category"], 0) + 1
    assert counts == {"T1": 3, "T2": 3, "T3": 3, "T4": 3}
    for task in tasks:
        for field in (
            "task_id",
            "repository",
            "commit",
            "category",
            "prompt",
            "functional_requirements",
            "project_constraints",
            "required_knowledge",
            "acceptable_implementation",
            "forbidden_implementation",
            "validation_commands",
            "expected_files_or_areas",
        ):
            assert field in task, f"{task['task_id']} missing {field}"


def test_prompts_contain_no_benchmark_terms_or_tool_names() -> None:
    data = json.loads((PILOT / "tasks.json").read_text(encoding="utf-8"))
    for task in data["tasks"]:
        text = _task_text(task)
        for term in FORBIDDEN_TERMS:
            assert term.lower() not in text.lower(), (
                f"{task['task_id']} contains forbidden term {term!r}"
            )


def test_prompts_contain_no_absolute_local_paths() -> None:
    data = json.loads((PILOT / "tasks.json").read_text(encoding="utf-8"))
    pattern = re.compile(r"[A-Za-z]:[\\/]")
    for task in data["tasks"]:
        assert not pattern.search(_task_text(task)), task["task_id"]


def test_frozen_hashes_match() -> None:
    protocol = json.loads((PILOT / "protocol.json").read_text(encoding="utf-8"))
    tasks_hash = hashlib.sha256(
        (PILOT / "tasks.json").read_bytes()
    ).hexdigest().upper()
    assert protocol["frozen_hashes"]["tasks_json"] == tasks_hash

    canonical = dict(protocol)
    canonical.pop("frozen_hashes")
    body = json.dumps(canonical, ensure_ascii=False, indent=2, sort_keys=True)
    protocol_hash = hashlib.sha256(body.encode("utf-8")).hexdigest().upper()
    assert protocol["frozen_hashes"]["protocol_json"] == protocol_hash


def test_arm_suffixes_fixed() -> None:
    protocol = json.loads((PILOT / "protocol.json").read_text(encoding="utf-8"))
    assert protocol["arms"]["A"]["prompt_suffix"] == ""
    assert "get_context_candidates" not in protocol["arms"]["B"]["prompt_suffix"]
    assert "candidate retrieval tool" in protocol["arms"]["C"]["prompt_suffix"]
    assert "must" not in protocol["arms"]["C"]["prompt_suffix"].lower()
