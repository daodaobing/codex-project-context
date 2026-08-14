"""Regenerate the frozen Phase 1.5 feasibility suite from benchmark ground truth.

The project-knowledge and known-hard tasks are copied verbatim from the exposed
benchmark tasks so required/acceptable docs can never drift from ground truth.
Only the simple/localized tasks are defined here (with a verified single file).
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "benchmarks" / "feasibility" / "tasks.json"

KNOWN_HARD = {"click-03", "click-05", "axios-h04", "ruff-h02", "jest-h02", "jest-h05"}
PROJECT_KNOWLEDGE = {
    "click-01", "click-02", "click-04",
    "uvicorn-01", "uvicorn-02", "uvicorn-03",
    "gin-01", "gin-02",
    "axios-h01", "axios-h02",
    "catch2-h01", "catch2-h03",
    "ruff-h01",
    "httpx-h01", "httpx-h04",
    "okhttp-h01", "okhttp-h04",
    "jest-h01", "jest-h03",
}

SIMPLES = [
    {"task_id": "simple-01", "repository": "gin", "task": "Find the exact version string declared in the project package metadata.", "answer_file": "README.md"},
    {"task_id": "simple-02", "repository": "uvicorn", "task": "What is the default value of the log-level setting?", "answer_file": "docs/settings.md"},
    {"task_id": "simple-03", "repository": "click", "task": "List the command-line options shown in the quickstart example.", "answer_file": "docs/quickstart.md"},
    {"task_id": "simple-04", "repository": "catch2", "task": "What is the include directive to use Catch2 in a single-file test?", "answer_file": "README.md"},
    {"task_id": "simple-05", "repository": "axios", "task": "What HTTP verb appears in the minimal request example in the first-steps docs?", "answer_file": "docs/pages/getting-started/first-steps.md"},
    {"task_id": "simple-06", "repository": "httpx", "task": "What is the first client example shown in the quickstart?", "answer_file": "docs/quickstart.md"},
]

DATASETS = ("tasks.json", "validation/tasks.json", "validation-v0.3/tasks.json", "blind-v0.4/tasks.json")


def load_ground_truth() -> dict[str, dict]:
    truth: dict[str, dict] = {}
    for name in DATASETS:
        data = json.loads((ROOT / "benchmarks" / name).read_text(encoding="utf-8"))
        for task in data["tasks"]:
            truth[task["id"]] = task
    return truth


def main() -> None:
    truth = load_ground_truth()
    tasks = []
    for task_id, gt in truth.items():
        if task_id in KNOWN_HARD:
            task_class = "known_hard"
        elif task_id in PROJECT_KNOWLEDGE:
            task_class = "project_knowledge"
        else:
            continue
        tasks.append(
            {
                "task_id": task_id,
                "repository": gt["repository"],
                "task_class": task_class,
                "project_context_expected": True,
                "oracle_type": "required_docs",
                "task": gt["task"],
                "required_docs": list(gt["required_docs"]),
                "acceptable_docs": list(gt["acceptable_docs"]),
            }
        )
    for simple in SIMPLES:
        tasks.append(
            {
                "task_id": simple["task_id"],
                "repository": simple["repository"],
                "task_class": "simple",
                "project_context_expected": False,
                "oracle_type": "single_file",
                "task": simple["task"],
                "required_docs": [],
                "acceptable_docs": [],
                "answer_file": simple["answer_file"],
            }
        )
    suite = {
        "version": 1,
        "frozen": True,
        "note": "Phase 1.5 feasibility suite. project-knowledge and known-hard tasks are copied from exposed benchmark ground truth; simple tasks use a verified single answer file.",
        "tasks": tasks,
    }
    SUITE_PATH.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    classes = {}
    for task in tasks:
        classes[task["task_class"]] = classes.get(task["task_class"], 0) + 1
    print(json.dumps({"suite": str(SUITE_PATH), "total": len(tasks), "classes": classes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
