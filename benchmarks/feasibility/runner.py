"""Phase 1.5 feasibility harness.

Runs the real candidate retriever over the frozen suite and applies a
deterministic, metadata-only "selection proxy" (NOT an LLM) to model how a
minimal second-stage reader would choose documents from the returned
metadata-only candidate pack.

The proxy reads candidates whose metadata (title/role/reasons/summary) overlaps
the task tokens, capped at a small read budget.  It is a measuring instrument
for metadata sufficiency and read amplification, not a claim about Codex.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from scanners import candidate_retriever  # noqa: E402


SUITE_PATH = ROOT / "benchmarks" / "feasibility" / "tasks.json"
READ_BUDGET = 4


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _meta_score(task_tokens: set[str], candidate: dict) -> int:
    """Deterministic metadata overlap score (title/role/reasons/summary)."""

    fields = " ".join(
        [
            str(candidate.get("title") or ""),
            str(candidate.get("role") or ""),
            " ".join(candidate.get("reasons", [])),
            str(candidate.get("summary") or ""),
        ]
    )
    return len(task_tokens & _tokens(fields))


def _selection_proxy(task_tokens: set[str], candidates: list[dict]) -> list[dict]:
    """Read a candidate when metadata overlaps the task; cap the read budget."""

    scored = sorted(
        candidates,
        key=lambda c: (-_meta_score(task_tokens, c), c["rank"]),
    )
    selected = [c for c in scored if _meta_score(task_tokens, c) > 0]
    return selected[:READ_BUDGET]


def _load_suite() -> list[dict]:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))["tasks"]


def _workspace(repository: str) -> Path:
    return ROOT / "benchmarks" / "workspaces" / repository


def _run_task(task: dict) -> dict[str, Any]:
    workspace = _workspace(task["repository"])
    project = server._ensure_project(str(workspace), force=False)
    started = time.perf_counter()
    pack = candidate_retriever.build_candidates(project, task["task"], 10)
    latency_ms = (time.perf_counter() - started) * 1000
    candidates = pack["candidates"]
    task_tokens = _tokens(task["task"])
    read = _selection_proxy(task_tokens, candidates)
    read_paths = {c["path"] for c in read}
    required = set(task["required_docs"])
    in_candidate = [p for p in required if p in {c["path"] for c in candidates}]
    selected_required = [p for p in in_candidate if p in read_paths]
    return {
        "task_id": task["task_id"],
        "repository": task["repository"],
        "task_class": task["task_class"],
        "project_context_expected": task["project_context_expected"],
        "task": task["task"],
        "required_docs": list(task["required_docs"]),
        "acceptable_docs": list(task["acceptable_docs"]),
        "candidate_count": len(candidates),
        "candidate_paths": [c["path"] for c in candidates],
        "read_paths": sorted(read_paths),
        "read_count": len(read),
        "required_count": len(required),
        "required_in_candidate": in_candidate,
        "required_selected": selected_required,
        "latency_ms": round(latency_ms, 4),
    }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def run(report_path: Path) -> dict[str, Any]:
    suite = _load_suite()
    rows = [_run_task(task) for task in suite]

    # Tool Adoption: whether the workflow called get_context_candidates.  This
    # harness always calls the tool (forced capability); Natural Adoption is a
    # separate property of a live Codex session and is not simulated here.
    expected_true = [r for r in rows if r["project_context_expected"]]
    expected_false = [r for r in rows if not r["project_context_expected"]]

    # Candidate Selection Recall (required in candidate -> actually read)
    num = den = 0
    for r in rows:
        for p in r["required_in_candidate"]:
            den += 1
            if p in r["read_paths"]:
                num += 1
    selection_recall = _rate(num, den)

    # All-Available-Required-Selected (tasks where ALL required docs are in
    # candidate AND ALL of them are read)
    eligible = [r for r in rows if r["required_count"] and set(r["required_docs"]).issubset(set(r["candidate_paths"]))]
    all_selected = [r for r in eligible if set(r["required_docs"]).issubset(set(r["read_paths"]))]

    # Candidate Read Ratio
    read_total = sum(r["read_count"] for r in rows)
    candidate_total = sum(r["candidate_count"] for r in rows)
    read_ratio = _rate(read_total, candidate_total)

    # Top-10 full-read tasks
    full_read = [r for r in rows if r["read_count"] >= r["candidate_count"] and r["candidate_count"] > 0]

    report = {
        "suite": str(SUITE_PATH),
        "task_count": len(rows),
        "class_counts": {
            "project_knowledge": sum(1 for r in rows if r["task_class"] == "project_knowledge"),
            "known_hard": sum(1 for r in rows if r["task_class"] == "known_hard"),
            "simple": sum(1 for r in rows if r["task_class"] == "simple"),
        },
        "adoption": {
            "note": "forced capability only; natural adoption requires a live Codex session against a deployed runtime",
            "expected_true_tasks": len(expected_true),
            "expected_false_tasks": len(expected_false),
        },
        "selection": {
            "selection_recall": selection_recall,
            "selected_required": num,
            "required_in_candidate": den,
            "all_available_required_selected": {
                "eligible_tasks": len(eligible),
                "all_selected_tasks": len(all_selected),
                "rate": _rate(len(all_selected), len(eligible)),
                "task_ids": sorted(r["task_id"] for r in all_selected),
            },
        },
        "usage": {
            "read_ratio": read_ratio,
            "read_total": read_total,
            "candidate_total": candidate_total,
            "full_read_tasks": sorted(r["task_id"] for r in full_read),
            "full_read_count": len(full_read),
        },
        "per_task": rows,
        "missed_selected_required": [
            {
                "task_id": r["task_id"],
                "repository": r["repository"],
                "required_in_candidate_but_not_read": sorted(set(r["required_in_candidate"]) - set(r["read_paths"])),
            }
            for r in rows
            if set(r["required_in_candidate"]) - set(r["read_paths"])
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "benchmarks" / "results" / "feasibility" / "selection-report.json"))
    args = parser.parse_args()
    report = run(Path(args.output))
    print(json.dumps(
        {
            "report": args.output,
            "task_count": report["task_count"],
            "selection_recall": report["selection"]["selection_recall"],
            "all_available_required_selected": report["selection"]["all_available_required_selected"],
            "read_ratio": report["usage"]["read_ratio"],
            "full_read_tasks": report["usage"]["full_read_tasks"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
