#!/usr/bin/env python3
"""Run the frozen OSS documentation-routing benchmark.

The benchmark deliberately calls the production server functions directly.  It
does not import an LLM client, alter matcher settings, or copy source into the
formal repository.  Generated indexes and results live below ignored paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks"
WORKSPACE_ROOT = BENCHMARK_ROOT / "workspaces"
RESULTS_ROOT = BENCHMARK_ROOT / "results"
REPOS_FILE = BENCHMARK_ROOT / "repos.json"
TASKS_FILE = BENCHMARK_ROOT / "tasks.json"
DATASET_FILES = {
    "diagnostic": (REPOS_FILE, TASKS_FILE, RESULTS_ROOT),
    "validation": (
        BENCHMARK_ROOT / "validation" / "repos.json",
        BENCHMARK_ROOT / "validation" / "tasks.json",
        RESULTS_ROOT / "validation",
    ),
    "validation-v0.3": (
        BENCHMARK_ROOT / "validation-v0.3" / "repos.json",
        BENCHMARK_ROOT / "validation-v0.3" / "tasks.json",
        RESULTS_ROOT / "validation-v0.3",
    ),
    "blind-v0.4": (
        BENCHMARK_ROOT / "blind-v0.4" / "repos.json",
        BENCHMARK_ROOT / "blind-v0.4" / "tasks.json",
        RESULTS_ROOT / "blind-v0.4",
    ),
}

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402  (the formal repository is the system under test)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_command(args: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        command = " ".join(args)
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"command failed ({completed.returncode}): {command}\n{detail}")
    return completed.stdout.strip()


def _safe_relative_path(value: str) -> str:
    """Normalize a task path and reject absolute or parent-traversing paths."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("document path must be a non-empty string")
    raw = value.replace("\\", "/")
    path = Path(raw)
    if raw.startswith(("/", "\\")) or path.is_absolute() or path.drive:
        raise ValueError(f"document path must be relative: {value}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"document path escapes repository: {value}")
    return "/".join(parts)


def _repo_file(root: Path, relative: str) -> Path:
    normalized = _safe_relative_path(relative)
    candidate = (root / Path(*normalized.split("/"))).resolve(strict=False)
    resolved_root = root.resolve(strict=True)
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError(f"path escapes repository: {relative}")
    return candidate


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _document_stats(root: Path, paths: list[str]) -> dict[str, Any]:
    unique: list[str] = []
    seen: set[str] = set()
    missing: list[str] = []
    total_chars = 0
    total_bytes = 0
    for raw_path in paths:
        path = _safe_relative_path(raw_path)
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
        file_path = _repo_file(root, path)
        try:
            data = file_path.read_bytes()
        except OSError:
            missing.append(path)
            continue
        total_bytes += len(data)
        total_chars += len(_decode(data))
    return {
        "paths": unique,
        "missing": missing,
        "doc_count": len(unique) - len(missing),
        "chars": total_chars,
        "bytes": total_bytes,
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    """Deterministic nearest-rank percentile (p95 is ceil(0.95*n))."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return round(ordered[rank - 1], 6)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 6)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _reduction(selected: int, full: int) -> float:
    if full <= 0:
        return 0.0
    return round(1.0 - selected / full, 6)


def _validate_definitions(
    repos_file: Path,
    tasks_file: Path,
    expected_repositories: int,
    expected_tasks: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    repos_data = _read_json(repos_file)
    tasks_data = _read_json(tasks_file)
    repos = repos_data.get("repositories")
    tasks = tasks_data.get("tasks")
    if repos_data.get("version") != 1 or not isinstance(repos, list):
        raise ValueError("repos.json must contain version 1 and repositories")
    if tasks_data.get("version") != 1 or tasks_data.get("frozen") is not True:
        raise ValueError("tasks.json must be version 1 and explicitly frozen")
    if not isinstance(tasks, list):
        raise ValueError("tasks.json must contain tasks")
    if len(repos) != expected_repositories or len(tasks) != expected_tasks:
        raise ValueError(
            "benchmark dataset has unexpected size: "
            f"expected {expected_repositories} repositories/{expected_tasks} tasks"
        )

    repo_ids: set[str] = set()
    repo_by_id: dict[str, dict[str, Any]] = {}
    for repo in repos:
        if not isinstance(repo, dict):
            raise ValueError("repository entries must be objects")
        repo_id = repo.get("id")
        commit = repo.get("commit")
        if not isinstance(repo_id, str) or not repo_id or repo_id in repo_ids:
            raise ValueError(f"invalid or duplicate repository id: {repo_id}")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"repository {repo_id} must pin a 40-character commit SHA")
        if not isinstance(repo.get("url"), str) or not repo["url"].startswith("https://"):
            raise ValueError(f"repository {repo_id} must use an HTTPS public URL")
        repo_ids.add(repo_id)
        repo_by_id[repo_id] = repo

    task_ids: set[str] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("task entries must be objects")
        task_id = task.get("id")
        repo_id = task.get("repository")
        if not isinstance(task_id, str) or not task_id or task_id in task_ids:
            raise ValueError(f"invalid or duplicate task id: {task_id}")
        if repo_id not in repo_by_id:
            raise ValueError(f"task {task_id} refers to unknown repository {repo_id}")
        if not isinstance(task.get("task"), str) or not task["task"].strip():
            raise ValueError(f"task {task_id} has no task text")
        for field in ("required_docs", "acceptable_docs"):
            values = task.get(field)
            if not isinstance(values, list) or not values:
                raise ValueError(f"task {task_id} must have non-empty {field}")
            normalized = [_safe_relative_path(value) for value in values]
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"task {task_id} has duplicate {field}")
        task_ids.add(task_id)
        counts[repo_id] += 1
    if set(counts) != repo_ids or any(value != 5 for value in counts.values()):
        raise ValueError("each repository must have exactly five tasks")

    return repos, tasks


def _ensure_workspace(repo: dict[str, Any]) -> tuple[Path, str]:
    repo_id = repo["id"]
    workspace = WORKSPACE_ROOT / repo_id
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    if not workspace.exists():
        _run_command(
            ["git", "clone", "--quiet", "--no-tags", repo["url"], str(workspace)],
            cwd=ROOT,
        )
    if not (workspace / ".git").exists():
        raise RuntimeError(f"benchmark workspace is not a Git checkout: {workspace}")
    dirty = _run_command(["git", "status", "--porcelain"], cwd=workspace)
    if dirty:
        raise RuntimeError(f"benchmark workspace is dirty; refusing to overwrite: {workspace}")
    commit = repo["commit"]
    try:
        _run_command(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=workspace)
    except RuntimeError:
        _run_command(["git", "fetch", "--quiet", "--no-tags", "origin", commit], cwd=workspace)
    current = _run_command(["git", "rev-parse", "HEAD"], cwd=workspace)
    if current != commit:
        _run_command(["git", "checkout", "--quiet", "--detach", commit], cwd=workspace)
    verified = _run_command(["git", "rev-parse", "HEAD"], cwd=workspace)
    if verified != commit:
        raise RuntimeError(f"pinned commit verification failed for {repo_id}: {verified}")
    return workspace.resolve(), verified


def _baseline_paths(project: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for item in list(project.get("entry_files", [])) + list(project.get("docs", [])):
        path = item.get("path") if isinstance(item, dict) else None
        if isinstance(path, str) and path not in paths:
            paths.append(path)
    return paths


def _task_metrics(
    task: dict[str, Any],
    route: dict[str, Any],
    baseline: dict[str, Any],
    selected: dict[str, Any],
    route_latency_ms: float,
) -> dict[str, Any]:
    required = [_safe_relative_path(path) for path in task["required_docs"]]
    acceptable = [_safe_relative_path(path) for path in task["acceptable_docs"]]
    selected_paths = selected["paths"]
    selected_set = set(selected_paths)
    required_set = set(required)
    acceptable_set = set(acceptable)
    required_hits = sorted(required_set & selected_set)
    relevant_hits = sorted((required_set | acceptable_set) & selected_set)
    return {
        "id": task["id"],
        "repository": task["repository"],
        "kind": task.get("kind", ""),
        "task": task["task"],
        "status": "ok",
        "required_docs": required,
        "acceptable_docs": acceptable,
        "required_hits": required_hits,
        "relevant_hits": relevant_hits,
        "relevant_files": selected_paths,
        "missing_selected_files": selected["missing"],
        "matched_topics": route.get("matched_topics", []),
        "matched_knowledge": route.get("matched_knowledge", []),
        "recommended_read": route.get("recommended_read", []),
        "route": route,
        "full_context_doc_count": baseline["doc_count"],
        "full_context_chars": baseline["chars"],
        "full_context_bytes": baseline["bytes"],
        "selected_doc_count": selected["doc_count"],
        "selected_context_chars": selected["chars"],
        "selected_context_bytes": selected["bytes"],
        "context_reduction": _reduction(selected["chars"], baseline["chars"]),
        "byte_reduction": _reduction(selected["bytes"], baseline["bytes"]),
        "file_reduction": _reduction(selected["doc_count"], baseline["doc_count"]),
        "required_recall": _ratio(len(required_hits), len(required_set)),
        "precision": _ratio(len(relevant_hits), len(selected_set)),
        "required_count": len(required_set),
        "selected_relevant_count": len(relevant_hits),
        "route_latency_ms": round(route_latency_ms, 6),
    }


def _aggregate(records: list[dict[str, Any]], cold_latencies: list[float]) -> dict[str, Any]:
    full_chars = sum(int(record["full_context_chars"]) for record in records)
    selected_chars = sum(int(record["selected_context_chars"]) for record in records)
    full_bytes = sum(int(record["full_context_bytes"]) for record in records)
    selected_bytes = sum(int(record["selected_context_bytes"]) for record in records)
    full_docs = sum(int(record["full_context_doc_count"]) for record in records)
    selected_docs = sum(int(record["selected_doc_count"]) for record in records)
    required_count = sum(int(record["required_count"]) for record in records)
    required_hits = sum(len(record["required_hits"]) for record in records)
    relevant_hits = sum(int(record["selected_relevant_count"]) for record in records)
    route_latencies = [float(record["route_latency_ms"]) for record in records]
    return {
        "task_count": len(records),
        "failed_task_count": sum(record.get("status") != "ok" for record in records),
        "full_context_chars": full_chars,
        "selected_context_chars": selected_chars,
        "context_reduction": _reduction(selected_chars, full_chars),
        "full_context_bytes": full_bytes,
        "selected_context_bytes": selected_bytes,
        "byte_reduction": _reduction(selected_bytes, full_bytes),
        "full_context_doc_count": full_docs,
        "selected_doc_count": selected_docs,
        "file_reduction": _reduction(selected_docs, full_docs),
        "required_count": required_count,
        "required_hits": required_hits,
        "required_recall": _ratio(required_hits, required_count),
        "selected_relevant_count": relevant_hits,
        "precision": _ratio(relevant_hits, selected_docs),
        "cold_scan_latency_ms": {
            "samples": [round(value, 6) for value in cold_latencies],
            "median": _median(cold_latencies),
            "p95": _percentile(cold_latencies, 0.95),
        },
        "cached_routing_latency_ms": {
            "samples": [round(value, 6) for value in route_latencies],
            "median": _median(route_latencies),
            "p95": _percentile(route_latencies, 0.95),
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "repository",
        "kind",
        "status",
        "required_count",
        "required_hits",
        "selected_relevant_count",
        "selected_doc_count",
        "full_context_doc_count",
        "full_context_chars",
        "selected_context_chars",
        "context_reduction",
        "full_context_bytes",
        "selected_context_bytes",
        "byte_reduction",
        "file_reduction",
        "required_recall",
        "precision",
        "route_latency_ms",
        "relevant_files",
        "missing_selected_files",
        "matched_topics",
        "matched_knowledge",
        "recommended_read",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            row = {field: record.get(field, "") for field in fields}
            row["required_hits"] = json.dumps(record.get("required_hits", []), ensure_ascii=False)
            for field in (
                "relevant_files",
                "missing_selected_files",
                "matched_topics",
                "matched_knowledge",
                "recommended_read",
            ):
                row[field] = json.dumps(record.get(field, []), ensure_ascii=False)
            writer.writerow(row)


def _write_outputs(
    run_id: str,
    raw: dict[str, Any],
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    results_root: Path,
) -> None:
    run_dir = results_root / "runs" / run_id
    for directory in (run_dir, results_root):
        _write_json(directory / "raw-results.json", raw)
        _write_json(directory / "summary.json", summary)
        _write_csv(directory / "task-results.csv", records)


def run(run_id: str, dataset: str = "diagnostic") -> tuple[dict[str, Any], int]:
    if dataset not in DATASET_FILES:
        raise ValueError(f"unknown benchmark dataset: {dataset}")
    repos_file, tasks_file, results_root = DATASET_FILES[dataset]
    expected_repositories, expected_tasks = (3, 15) if dataset == "diagnostic" else (2, 10)
    repos, tasks = _validate_definitions(
        repos_file,
        tasks_file,
        expected_repositories,
        expected_tasks,
    )
    definitions = {
        "repos_sha256": _sha256(repos_file),
        "tasks_sha256": _sha256(tasks_file),
    }
    tasks_by_repo: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        tasks_by_repo[task["repository"]].append(task)

    old_index_path = server.INDEX_PATH
    repo_results: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="codex-benchmark-index-", dir=str(WORKSPACE_ROOT)) as index_dir:
            server.INDEX_PATH = Path(index_dir) / "project-index.json"
            for repo in repos:
                workspace, verified_commit = _ensure_workspace(repo)
                scan_started = time.perf_counter()
                try:
                    scan_info = server.scan_project(str(workspace), force=True)
                    scan_error = None
                except Exception as exc:  # preserve an actionable raw failure
                    scan_info = {}
                    scan_error = f"{type(exc).__name__}: {exc}"
                    errors.append(f"{repo['id']} scan: {scan_error}")
                cold_latency_ms = (time.perf_counter() - scan_started) * 1000
                if scan_error:
                    repo_results.append({
                        "id": repo["id"],
                        "name": repo.get("name", repo["id"]),
                        "language": repo.get("language", ""),
                        "url": repo["url"],
                        "commit": verified_commit,
                        "workspace": str(workspace),
                        "scan": {"error": scan_error},
                        "cold_scan_latency_ms": round(cold_latency_ms, 6),
                        "baseline": {},
                        "tasks": [],
                    })
                    continue

                project = server.get_project_index(str(workspace))
                baseline_paths = _baseline_paths(project)
                baseline = _document_stats(workspace, baseline_paths)
                task_records: list[dict[str, Any]] = []
                for task in tasks_by_repo[repo["id"]]:
                    route_started = time.perf_counter()
                    try:
                        route = server.get_project_context(str(workspace), task["task"])
                        route_latency_ms = (time.perf_counter() - route_started) * 1000
                        selected = _document_stats(workspace, route.get("relevant_files", []))
                        record = _task_metrics(task, route, baseline, selected, route_latency_ms)
                    except Exception as exc:  # keep all failures visible in raw output
                        route_latency_ms = (time.perf_counter() - route_started) * 1000
                        record = {
                            "id": task["id"],
                            "repository": task["repository"],
                            "kind": task.get("kind", ""),
                            "task": task["task"],
                            "status": "error",
                            "required_docs": task["required_docs"],
                            "acceptable_docs": task["acceptable_docs"],
                            "required_hits": [],
                            "relevant_hits": [],
                            "relevant_files": [],
                            "missing_selected_files": [],
                            "matched_topics": [],
                            "matched_knowledge": [],
                            "recommended_read": [],
                            "route": {},
                            "full_context_doc_count": baseline["doc_count"],
                            "full_context_chars": baseline["chars"],
                            "full_context_bytes": baseline["bytes"],
                            "selected_doc_count": 0,
                            "selected_context_chars": 0,
                            "selected_context_bytes": 0,
                            "context_reduction": 0.0,
                            "byte_reduction": 0.0,
                            "file_reduction": 0.0,
                            "required_recall": 0.0,
                            "precision": 0.0,
                            "required_count": len(task["required_docs"]),
                            "selected_relevant_count": 0,
                            "route_latency_ms": round(route_latency_ms, 6),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                        errors.append(f"{task['id']}: {record['error']}")
                    task_records.append(record)
                    all_records.append(record)

                repo_results.append({
                    "id": repo["id"],
                    "name": repo.get("name", repo["id"]),
                    "language": repo.get("language", ""),
                    "url": repo["url"],
                    "commit": verified_commit,
                    "workspace": str(workspace),
                    "scan": scan_info,
                    "cold_scan_latency_ms": round(cold_latency_ms, 6),
                    "baseline": {
                        "paths": baseline_paths,
                        "missing": baseline["missing"],
                        "doc_count": baseline["doc_count"],
                        "chars": baseline["chars"],
                        "bytes": baseline["bytes"],
                    },
                    "tasks": task_records,
                })
    finally:
        server.INDEX_PATH = old_index_path

    cold_latencies = [
        float(repo["cold_scan_latency_ms"])
        for repo in repo_results
        if repo.get("baseline")
    ]
    overall = _aggregate(all_records, cold_latencies)
    per_repository: dict[str, Any] = {}
    for repo in repo_results:
        repo_records = [record for record in all_records if record["repository"] == repo["id"]]
        repo_cold = [float(repo["cold_scan_latency_ms"])] if repo.get("baseline") else []
        per_repository[repo["id"]] = _aggregate(repo_records, repo_cold)

    summary = {
        "schema_version": 1,
        "dataset": dataset,
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "definitions": definitions,
        "overall": overall,
        "per_repository": per_repository,
        "errors": errors,
    }
    raw = {
        "schema_version": 1,
        "dataset": dataset,
        "run_id": run_id,
        "generated_at": summary["generated_at"],
        "definitions": definitions,
        "repositories": repo_results,
        "tasks": all_records,
        "errors": errors,
    }
    _write_outputs(run_id, raw, summary, all_records, results_root)
    return summary, 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default=None,
        help="stable output directory name; defaults to a UTC timestamp",
    )
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASET_FILES),
        default="diagnostic",
        help="benchmark dataset to run (diagnostic is the default)",
    )
    args = parser.parse_args()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
        parser.error("--run-id may contain only letters, numbers, dot, underscore, and hyphen")
    try:
        summary, exit_code = run(run_id, dataset=args.dataset)
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if exit_code:
        print("benchmark completed with recorded failures", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
