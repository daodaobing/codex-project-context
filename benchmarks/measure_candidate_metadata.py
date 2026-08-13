"""Measure the real ``get_context_candidates`` metadata size and latency.

This is the authoritative metadata-size source for the V0.1 ratchet: it runs
the same candidate pack builder used by the MCP tool over the exposed RRF
benchmark tasks (no LLM, no network).  Outputs avg/p95 metadata chars and
assembly latency to stdout; used by ``candidate_ratchet`` when the feasibility
report is present.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from benchmarks import run_benchmark as bench  # noqa: E402
from scanners import candidate_retriever  # noqa: E402


RUN_PREFIX = "v05-fusion-rrf_80_40_k10"
DATASETS = ("diagnostic", "validation", "validation-v0.3", "blind-v0.4")


def _raw_path(dataset: str) -> Path:
    return bench.DATASET_FILES[dataset][2] / "runs" / f"{RUN_PREFIX}-{dataset}" / "raw-results.json"


def _measure() -> dict[str, Any]:
    chars: list[int] = []
    latencies: list[float] = []
    for dataset in DATASETS:
        path = _raw_path(dataset)
        if not path.is_file():
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        repos = {r["id"]: r for r in raw["repositories"]}
        for task in raw["tasks"]:
            workspace = Path(repos[task["repository"]]["workspace"])
            if not workspace.is_dir():
                continue
            project = server._ensure_project(str(workspace), force=False)
            started = time.perf_counter()
            pack = candidate_retriever.build_candidates(project, task["task"], 10)
            latencies.append((time.perf_counter() - started) * 1000)
            # Exclude the echoed task text from the size budget; it is not
            # candidate metadata and is not part of the retrieval payload.
            budget = dict(pack)
            budget.pop("task", None)
            chars.append(len(json.dumps(budget, ensure_ascii=False, sort_keys=True)))
    if not chars:
        return {"available": False, "reason": "no cached workspaces"}
    def percentile(values: list[float], q: float) -> float:
        ordered = sorted(values)
        idx = min(len(ordered) - 1, int(len(ordered) * q))
        return ordered[idx]

    return {
        "available": True,
        "task_count": len(chars),
        "avg_metadata_chars": round(statistics.fmean(chars), 2),
        "p95_metadata_chars": percentile(chars, 0.95),
        "median_assembly_ms": round(statistics.median(latencies), 4),
        "p95_assembly_ms": round(percentile(latencies, 0.95), 4),
    }


def measure_metadata() -> dict[str, Any]:
    return _measure()


if __name__ == "__main__":
    print(json.dumps(_measure(), ensure_ascii=False, indent=2))
