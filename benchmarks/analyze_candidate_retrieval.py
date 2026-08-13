"""Analyze RRF raw rankings as a high-recall candidate retriever.

This benchmark-only analysis reuses exposed V0.5 RRF traces.  It does not run
a holdout, change Selection, or alter the MCP response schema.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from benchmarks import run_benchmark as bench  # noqa: E402
from scanners.project_scanner import ProjectScanner  # noqa: E402


KS = (3, 5, 6, 8, 10, 12, 15)
DATASETS = (
    ("diagnostic", "Diagnostic"),
    ("validation", "Validation A"),
    ("validation-v0.3", "Validation B"),
    ("blind-v0.4", "Validation C"),
)
RUN_PREFIX = "v05-fusion-rrf_80_40_k10"


def _ranked_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one deterministic raw-ranking row per path."""

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("path") or "")
        rank = row.get("rank_before")
        if not path or not isinstance(rank, int):
            continue
        previous = unique.get(path)
        if previous is None or rank < int(previous["rank_before"]):
            unique[path] = row
    return sorted(unique.values(), key=lambda row: (row["rank_before"], row["path"]))


def _required_doc_rank(rows: Iterable[dict[str, Any]], path: str) -> int | None:
    return next(
        (int(row["rank_before"]) for row in _ranked_rows(rows) if row["path"] == path),
        None,
    )


def _retrieval_metrics(
    required_docs: Iterable[str],
    acceptable_docs: Iterable[str],
    ranked_paths: Iterable[str],
    k: int,
) -> dict[str, int | float]:
    """Compute task-level Recall@K and the prompt-defined Precision@K."""

    required = set(required_docs)
    relevant = required | set(acceptable_docs)
    candidates = list(ranked_paths)[:k]
    required_hits = len(required & set(candidates))
    relevant_hits = len(relevant & set(candidates))
    return {
        "candidate_count": len(candidates),
        "required_count": len(required),
        "relevant_count": len(relevant),
        "required_hits": required_hits,
        "relevant_hits": relevant_hits,
        "required_recall": required_hits / len(required) if required else 0.0,
        "relevant_recall": relevant_hits / len(relevant) if relevant else 0.0,
        "all_required_found": required_hits == len(required) and len(required) > 0,
        # The requested benchmark definition divides by K even when a repository
        # has fewer than K indexed documents.
        "precision": relevant_hits / k if k else 0.0,
    }


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _candidate_pack(
    project: str,
    task: str,
    rows: Iterable[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    k: int,
) -> dict[str, Any]:
    candidates = []
    for row in _ranked_rows(rows)[:k]:
        doc = metadata.get(row["path"], {})
        candidates.append(
            {
                "path": row["path"],
                "title": str(doc.get("title") or row["path"]),
                "summary": str(doc.get("summary") or ""),
                "role": str(doc.get("role") or row.get("role") or ""),
                "rank": int(row["rank_before"]),
                "score": float(row.get("fusion_score") or row.get("raw_score") or 0.0),
                "reasons": _dedupe(row.get("reasons", [])),
            }
        )
    return {"project": project, "task": task, "candidates": candidates}


def _serialize_candidate_pack(pack: dict[str, Any]) -> str:
    """Stable benchmark-only metadata serialization."""

    return json.dumps(
        pack,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _raw_path(dataset: str) -> Path:
    results_root = bench.DATASET_FILES[dataset][2]
    run_id = f"{RUN_PREFIX}-{dataset}"
    return results_root / "runs" / run_id / "raw-results.json"


def _read_raw(dataset: str) -> dict[str, Any]:
    path = _raw_path(dataset)
    if not path.is_file():
        raise FileNotFoundError(
            f"missing exposed RRF result: {path}; run benchmarks/analyze_v05.py first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _repo_contexts(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scanner = ProjectScanner(server._load_settings())
    contexts: dict[str, dict[str, Any]] = {}
    for repository in raw.get("repositories", []):
        repo_id = repository["id"]
        root = Path(repository["workspace"])
        project = scanner.scan(str(root))
        docs = project.get("entry_files", []) + project.get("docs", [])
        metadata = {doc["path"]: doc for doc in docs}
        full_cost: dict[str, dict[str, int]] = {}
        for path in repository.get("baseline", {}).get("paths", []):
            file_path = bench._repo_file(root, path)
            data = file_path.read_bytes()
            full_cost[path] = {"chars": len(bench._decode(data)), "bytes": len(data)}
        contexts[repo_id] = {
            "project": project["project"],
            "root": root,
            "metadata": metadata,
            "full_cost": full_cost,
        }
    return contexts


def _task_rows(raw: dict[str, Any], dataset: str) -> list[dict[str, Any]]:
    contexts = _repo_contexts(raw)
    output = []
    for task in raw.get("tasks", []):
        context = contexts[task["repository"]]
        ranked = _ranked_rows(task.get("route", {}).get("trace", []))
        ranked_paths = [row["path"] for row in ranked]
        selected = set(task.get("relevant_files", []))
        costs = context["full_cost"]
        by_k: dict[str, Any] = {}
        for k in KS:
            metrics = _retrieval_metrics(
                task.get("required_docs", []),
                task.get("acceptable_docs", []),
                ranked_paths,
                k,
            )
            started = time.perf_counter_ns()
            pack = _candidate_pack(
                context["project"],
                task["task"],
                ranked,
                context["metadata"],
                k,
            )
            serialized = _serialize_candidate_pack(pack)
            assembly_ms = (time.perf_counter_ns() - started) / 1_000_000
            candidate_paths = ranked_paths[:k]
            metrics.update(
                {
                    "metadata_chars": len(serialized),
                    "metadata_bytes": len(serialized.encode("utf-8")),
                    "full_candidate_chars": sum(costs.get(path, {}).get("chars", 0) for path in candidate_paths),
                    "full_candidate_bytes": sum(costs.get(path, {}).get("bytes", 0) for path in candidate_paths),
                    "full_documentation_chars": int(task["full_context_chars"]),
                    "full_documentation_bytes": int(task["full_context_bytes"]),
                    "full_documentation_docs": int(task["full_context_doc_count"]),
                    "metadata_assembly_ms": assembly_ms,
                }
            )
            by_k[str(k)] = metrics

        required_ranks = []
        for path in task.get("required_docs", []):
            row = next((item for item in ranked if item["path"] == path), None)
            required_ranks.append(
                {
                    "path": path,
                    "rank": int(row["rank_before"]) if row else None,
                    "structural_rank": int(row.get("structural_rank") or 0) if row else None,
                    "bm25_rank": int(row.get("bm25_rank") or 0) if row else None,
                    "selected": path in selected,
                }
            )
        output.append(
            {
                "dataset": dataset,
                "task_id": task["id"],
                "repository": task["repository"],
                "task": task["task"],
                "required_docs": task.get("required_docs", []),
                "acceptable_docs": task.get("acceptable_docs", []),
                "required_ranks": required_ranks,
                "ranked": ranked,
                "by_k": by_k,
                "pack_example": _candidate_pack(
                    context["project"], task["task"], ranked, context["metadata"], 10
                ),
            }
        )
    return output


def _aggregate(tasks: list[dict[str, Any]], k: int) -> dict[str, Any]:
    rows = [task["by_k"][str(k)] for task in tasks]
    required_count = sum(int(row["required_count"]) for row in rows)
    relevant_count = sum(int(row["relevant_count"]) for row in rows)
    required_hits = sum(int(row["required_hits"]) for row in rows)
    relevant_hits = sum(int(row["relevant_hits"]) for row in rows)
    full_chars = sum(int(row["full_documentation_chars"]) for row in rows)
    full_bytes = sum(int(row["full_documentation_bytes"]) for row in rows)
    candidate_chars = sum(int(row["full_candidate_chars"]) for row in rows)
    candidate_bytes = sum(int(row["full_candidate_bytes"]) for row in rows)
    metadata_chars = sum(int(row["metadata_chars"]) for row in rows)
    metadata_bytes = sum(int(row["metadata_bytes"]) for row in rows)
    metadata_samples = [float(row["metadata_chars"]) for row in rows]
    candidate_samples = [float(row["full_candidate_chars"]) for row in rows]
    latency = [float(row["metadata_assembly_ms"]) for row in rows]
    all_required_found = sum(1 for row in rows if row["all_required_found"])
    count = len(rows)
    return {
        "task_count": count,
        "required_recall": round(required_hits / required_count, 6) if required_count else 0.0,
        "relevant_recall": round(relevant_hits / relevant_count, 6) if relevant_count else 0.0,
        "all_required_found_rate": round(all_required_found / count, 6) if count else 0.0,
        "precision": round(relevant_hits / (k * count), 6) if count and k else 0.0,
        "full_context_reduction": round(1 - candidate_chars / full_chars, 6) if full_chars else 0.0,
        "metadata_reduction": round(1 - metadata_chars / full_chars, 6) if full_chars else 0.0,
        "full_byte_reduction": round(1 - candidate_bytes / full_bytes, 6) if full_bytes else 0.0,
        "metadata_byte_reduction": round(1 - metadata_bytes / full_bytes, 6) if full_bytes else 0.0,
        "avg_metadata_chars": round(statistics.fmean(metadata_samples), 2) if count else 0.0,
        "p95_metadata_chars": int(_percentile(metadata_samples, 0.95)),
        "avg_full_candidate_chars": round(statistics.fmean(candidate_samples), 2) if count else 0.0,
        "p95_full_candidate_chars": int(_percentile(candidate_samples, 0.95)),
        "metadata_assembly_median_ms": round(statistics.median(latency), 6) if count else 0.0,
        "metadata_assembly_p95_ms": round(_percentile(latency, 0.95), 6),
    }


def _curve(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(k): _aggregate(tasks, k) for k in KS}


def _failure_classification(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {"rank_le_6": 0, "rank_7_8": 0, "rank_9_10": 0, "rank_11_12": 0, "rank_13_15": 0, "rank_gt_15": 0}
    missing = []
    for task in tasks:
        for item in task["required_ranks"]:
            if item["selected"]:
                continue
            rank = item["rank"]
            if rank is not None and rank <= 6:
                bucket = "rank_le_6"
            elif rank is not None and rank <= 8:
                bucket = "rank_7_8"
            elif rank is not None and rank <= 10:
                bucket = "rank_9_10"
            elif rank is not None and rank <= 12:
                bucket = "rank_11_12"
            elif rank is not None and rank <= 15:
                bucket = "rank_13_15"
            else:
                bucket = "rank_gt_15"
            buckets[bucket] += 1
            missing.append({"task_id": task["task_id"], "repository": task["repository"], **item})
    return {
        "missing_required_count": len(missing),
        **buckets,
        "selection_failure_rank_le_12": sum(buckets[key] for key in ("rank_le_6", "rank_7_8", "rank_9_10", "rank_11_12")),
        "retrieval_failure_rank_gt_12": buckets["rank_13_15"] + buckets["rank_gt_15"],
        "items": missing,
    }


def _missed_at_15(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    misses = []
    for task in tasks:
        for item in task["required_ranks"]:
            rank = item["rank"]
            if rank is not None and rank <= 15:
                continue
            competitors = [
                {
                    "path": row["path"],
                    "rrf_rank": row["rank_before"],
                    "structural_rank": row.get("structural_rank"),
                    "bm25_rank": row.get("bm25_rank"),
                }
                for row in task["ranked"][:5]
            ]
            srank = item["structural_rank"]
            brank = item["bm25_rank"]
            if srank is None or brank is None:
                reason = "required document was absent from the indexed raw ranking"
            elif srank > 15 and brank > 15:
                reason = "both structural and BM25 rankings placed it outside Top-15"
            elif srank > 15:
                reason = "weak structural rank outweighed its BM25 position"
            elif brank > 15:
                reason = "weak BM25 rank outweighed its structural position"
            else:
                reason = "RRF combination and ties placed it outside Top-15"
            misses.append(
                {
                    "dataset": task["dataset"],
                    "repository": task["repository"],
                    "task_id": task["task_id"],
                    "task": task["task"],
                    "required_doc": item["path"],
                    "structural_rank": srank,
                    "bm25_rank": brank,
                    "rrf_rank": rank,
                    "top_competitors": competitors,
                    "reason": reason,
                }
            )
    return misses


def _write_csv(path: Path, report: dict[str, Any]) -> None:
    fields = (
        "scope", "name", "k",
        "required_recall", "relevant_recall", "all_required_found_rate",
        "precision", "full_context_reduction", "metadata_reduction",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        scopes = [("overall", "overall", report["overall_curve"])]
        scopes += [("dataset", name, value["curve"]) for name, value in report["datasets"].items()]
        scopes += [("repository", name, value) for name, value in report["per_repository"].items()]
        for scope, name, curve in scopes:
            for k, metrics in curve.items():
                writer.writerow({"scope": scope, "name": name, "k": k, **{field: metrics[field] for field in fields[3:]}})


def _rank_distribution(tasks: list[dict[str, Any]]) -> dict[str, int]:
    """Required-doc raw-rank distribution across all required docs (no K)."""

    buckets = {"1-3": 0, "4-6": 0, "7-10": 0, "11-15": 0, ">15": 0}
    for task in tasks:
        for item in task["required_ranks"]:
            rank = item["rank"]
            if rank is None:
                buckets[">15"] += 1
            elif rank <= 3:
                buckets["1-3"] += 1
            elif rank <= 6:
                buckets["4-6"] += 1
            elif rank <= 10:
                buckets["7-10"] += 1
            elif rank <= 15:
                buckets["11-15"] += 1
            else:
                buckets[">15"] += 1
    return buckets


def _small_corpus_analysis(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-repo documentation size vs K10 candidate metadata overhead."""

    rows: dict[str, dict[str, Any]] = {}
    for task in tasks:
        repo = task["repository"]
        doc_count = int(task["by_k"]["10"]["full_documentation_docs"])
        full_chars = int(task["by_k"]["10"]["full_documentation_chars"])
        metadata_chars = int(task["by_k"]["10"]["metadata_chars"])
        entry = rows.setdefault(
            repo,
            {"repository": repo, "doc_count": doc_count, "total_doc_chars": full_chars, "k10_metadata_chars": 0},
        )
        entry["k10_metadata_chars"] += metadata_chars
    for entry in rows.values():
        entry["candidate_overhead_ratio"] = (
            round(entry["k10_metadata_chars"] / entry["total_doc_chars"], 6)
            if entry["total_doc_chars"]
            else 0.0
        )
    return sorted(rows.values(), key=lambda row: row["doc_count"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(bench.RESULTS_ROOT / "candidate-retrieval-feasibility.json"))
    args = parser.parse_args()

    all_tasks = []
    datasets: dict[str, Any] = {}
    for dataset, label in DATASETS:
        tasks = _task_rows(_read_raw(dataset), dataset)
        all_tasks.extend(tasks)
        datasets[label] = {"source": dataset, "curve": _curve(tasks)}

    repositories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in all_tasks:
        repositories[task["repository"]].append(task)
    overall_curve = _curve(all_tasks)
    failures = _failure_classification(all_tasks)
    report = {
        "schema_version": 1,
        "analysis": "Candidate Retrieval Feasibility",
        "ranking": {"mode": "weighted_rrf", "structural_weight": 80.0, "bm25_weight": 40.0, "k": 10.0},
        "candidate_ks": list(KS),
        "overall_curve": overall_curve,
        "datasets": datasets,
        "per_repository": {name: _curve(tasks) for name, tasks in sorted(repositories.items())},
        "per_task": {
            task["task_id"]: {
                "dataset": task["dataset"],
                "repository": task["repository"],
                "task": task["task"],
                "curve": {str(k): task["by_k"][str(k)] for k in KS},
                "required_ranks": task["required_ranks"],
            }
            for task in all_tasks
        },
        "plateau": {
            "6_to_8": round(overall_curve["8"]["required_recall"] - overall_curve["6"]["required_recall"], 6),
            "8_to_10": round(overall_curve["10"]["required_recall"] - overall_curve["8"]["required_recall"], 6),
            "10_to_12": round(overall_curve["12"]["required_recall"] - overall_curve["10"]["required_recall"], 6),
            "12_to_15": round(overall_curve["15"]["required_recall"] - overall_curve["12"]["required_recall"], 6),
        },
        "failure_classification": failures,
        "missed_at_15": _missed_at_15(all_tasks),
        "rank_distribution": _rank_distribution(all_tasks),
        "small_corpus_analysis": _small_corpus_analysis(all_tasks),
        "candidate_context_cost": {str(k): overall_curve[str(k)] for k in (6, 8, 10, 12)},
        "candidate_pack_example": all_tasks[0]["pack_example"],
        "notes": {
            "precision_denominator": "K, including when indexed document count is below K",
            "metadata_serialization": "deterministic compact JSON; no MCP public API change",
            "selection_bypassed": ["dynamic threshold", "max selection", "hard dedup", "soft diversity"],
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(output.with_suffix(".csv"), report)
    print(json.dumps({"report": str(output), "overall_curve": overall_curve, "failures": failures, "missed_at_15": report["missed_at_15"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
