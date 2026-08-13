"""Run the fixed V0.5 fusion comparison and Oracle ceiling analysis.

The script reuses the frozen benchmark definitions and writes only ignored
runtime output.  It does not create a holdout or change Selection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import run_benchmark as bench  # noqa: E402


DATASETS = (
    ("diagnostic", "Diagnostic: Click/Uvicorn/Gin"),
    ("validation", "Validation A: Axios/Catch2"),
    ("validation-v0.3", "Validation B: Ruff/HTTPX"),
    ("blind-v0.4", "Validation C: OkHttp/Jest"),
)

BOUNDED_CONFIGS = (
    ("bounded_k10_m4", {"bounded_bm25_k": 10.0, "bounded_bm25_max_boost": 4.0}),
    ("bounded_k20_m4", {"bounded_bm25_k": 20.0, "bounded_bm25_max_boost": 4.0}),
    ("bounded_k10_m2", {"bounded_bm25_k": 10.0, "bounded_bm25_max_boost": 2.0}),
)

RRF_CONFIGS = (
    ("rrf_80_40_k10", {"rrf_structural_weight": 80.0, "rrf_bm25_weight": 40.0, "rrf_k": 10.0}),
    ("rrf_2_1_k10", {"rrf_structural_weight": 2.0, "rrf_bm25_weight": 1.0, "rrf_k": 10.0}),
    ("rrf_3_1_k20", {"rrf_structural_weight": 3.0, "rrf_bm25_weight": 1.0, "rrf_k": 20.0}),
)


def _raw_path(dataset: str, run_id: str) -> Path:
    return bench.DATASET_FILES[dataset][2] / "runs" / run_id / "raw-results.json"


def _run(
    dataset: str,
    mode: str,
    suffix: str,
    overrides: dict[str, float] | None = None,
    *,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    run_id = f"v05-fusion-{suffix}-{dataset}"
    if reuse_existing:
        summary = json.loads(
            (bench.DATASET_FILES[dataset][2] / "runs" / run_id / "summary.json")
            .read_text(encoding="utf-8")
        )
        exit_code = 0
    else:
        summary, exit_code = bench.run(
            run_id,
            dataset=dataset,
            ranking_mode=mode,
            ranking_overrides=overrides,
        )
    raw = json.loads(_raw_path(dataset, run_id).read_text(encoding="utf-8"))
    raw["summary"] = summary
    raw["exit_code"] = exit_code
    return raw


def _oracle_task(record: dict[str, Any]) -> dict[str, Any]:
    selected = len(set(record.get("relevant_files", [])))
    required = set(record.get("required_docs", []))
    acceptable = set(record.get("acceptable_docs", []))
    eligible = required | acceptable
    ceiling_count = min(selected, len(eligible))
    oracle_precision = ceiling_count / selected if selected else 0.0
    oracle_recall = min(selected, len(required)) / len(required) if required else 0.0
    actual_precision = float(record.get("precision", 0.0))
    return {
        "id": record.get("id"),
        "repository": record.get("repository"),
        "selected_count": selected,
        "required_count": len(required),
        "eligible_count": len(eligible),
        "actual_precision": round(actual_precision, 6),
        "oracle_precision": round(oracle_precision, 6),
        "oracle_recall": round(oracle_recall, 6),
        "oracle_normalized_precision": round(
            actual_precision / oracle_precision, 6
        ) if oracle_precision else 0.0,
    }


def _oracle_aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [_oracle_task(record) for record in records]
    selected_total = sum(row["selected_count"] for row in rows)
    actual_relevant = sum(
        len(set(record.get("relevant_hits", [])))
        for record in records
    )
    oracle_relevant = sum(
        min(row["selected_count"], row["eligible_count"])
        for row in rows
    )
    required_total = sum(row["required_count"] for row in rows)
    oracle_recall_numerator = sum(
        row["oracle_recall"] * row["required_count"] for row in rows
    )
    return {
        "actual_precision": round(actual_relevant / selected_total, 6) if selected_total else 0.0,
        "oracle_precision": round(oracle_relevant / selected_total, 6) if selected_total else 0.0,
        "oracle_normalized_precision": round(actual_relevant / oracle_relevant, 6) if oracle_relevant else 0.0,
        "oracle_recall": round(oracle_recall_numerator / required_total, 6) if required_total else 0.0,
        "tasks_oracle_precision_below_65": sum(row["oracle_precision"] < 0.65 for row in rows),
        "task_count": len(rows),
    }


def _oracle_report(raw: dict[str, Any]) -> dict[str, Any]:
    records = raw.get("tasks", [])
    per_repository: dict[str, Any] = {}
    for repository in sorted({record.get("repository") for record in records}):
        repo_records = [record for record in records if record.get("repository") == repository]
        per_repository[repository] = {
            "per_task": [_oracle_task(record) for record in repo_records],
            "aggregate": _oracle_aggregate(repo_records),
        }
    return {
        "per_task": [_oracle_task(record) for record in records],
        "per_repository": per_repository,
        "aggregate": _oracle_aggregate(records),
    }


def _stability(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    datasets = [summary["overall"] for summary in summaries.values()]
    repositories = [
        metrics
        for summary in summaries.values()
        for metrics in summary["per_repository"].values()
    ]
    recalls = [metrics["required_recall"] for metrics in datasets]
    return {
        "worst_dataset_recall": min(recalls),
        "worst_repo_recall": min(metrics["required_recall"] for metrics in repositories),
        "worst_dataset_top6": min(metrics["required_top6_rate"] for metrics in datasets),
        "recall_spread": round(max(recalls) - min(recalls), 6),
        "worst_cached_p95": max(
            metrics["cached_routing_latency_ms"]["p95"] for metrics in datasets
        ),
    }


def _trace_row(raw: dict[str, Any], task_id: str, path: str) -> dict[str, Any] | None:
    task = next((item for item in raw.get("tasks", []) if item.get("id") == task_id), None)
    if not task:
        return None
    return next(
        (row for row in task.get("route", {}).get("trace", []) if row.get("path") == path),
        None,
    )


def _trace_comparison(raws: dict[str, dict[str, Any]], wanted: tuple[str, ...]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for task in raws["structural"].get("tasks", []):
        for path in task.get("required_docs", []):
            if not any(path == wanted_path or path.endswith(wanted_path) for wanted_path in wanted):
                continue
            modes: dict[str, Any] = {}
            for mode, raw in raws.items():
                row = _trace_row(raw, task["id"], path) or {}
                modes[mode] = {
                    "rank": row.get("rank_before"),
                    "selected": row.get("selected"),
                    "top6": row.get("rank_before") is not None and row["rank_before"] <= 6,
                    "structural_rank": row.get("structural_rank"),
                    "bm25_rank": row.get("bm25_rank"),
                    "fusion_score": row.get("fusion_score"),
                }
            output.append({"task_id": task["id"], "path": path, "modes": modes})
    return output


def _score_scale(raw: dict[str, Any]) -> dict[str, Any]:
    fields = ("structural_score", "bm25_score", "coverage_score", "raw_score", "final_score")
    buckets = {"required": {field: [] for field in fields}, "ordinary": {field: [] for field in fields}}
    for task in raw.get("tasks", []):
        required = set(task.get("required_docs", []))
        for row in task.get("route", {}).get("trace", []):
            bucket = "required" if row.get("path") in required else "ordinary"
            for field in fields:
                value = row.get(field)
                if isinstance(value, (int, float)):
                    buckets[bucket][field].append(float(value))
    result: dict[str, Any] = {}
    for bucket, values in buckets.items():
        result[bucket] = {}
        for field, numbers in values.items():
            ordered = sorted(numbers)
            result[bucket][field] = {
                "count": len(ordered),
                "median": ordered[len(ordered) // 2] if ordered else None,
                "p90": ordered[max(0, int(len(ordered) * 0.9) - 1)] if ordered else None,
                "max": max(ordered) if ordered else None,
            }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(ROOT / "benchmarks" / "results" / "v05-fusion-calibration.json"),
    )
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="reuse already generated v05-fusion-* runtime outputs",
    )
    args = parser.parse_args()

    report: dict[str, Any] = {
        "datasets": {},
        "oracle": {},
        "stability": {},
        "trace_comparison": {},
        "score_scale": {},
        "parameters": {"bounded": dict(BOUNDED_CONFIGS), "rrf": dict(RRF_CONFIGS)},
    }
    raw_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for dataset, label in DATASETS:
        report["datasets"][dataset] = {"label": label, "modes": {}}
        report["oracle"][dataset] = {}
        runs = (
            (("structural", "structural", None), ("raw_bm25", "raw_bm25", None))
            + tuple((name, "bounded_bm25", overrides) for name, overrides in BOUNDED_CONFIGS)
            + tuple((name, "rrf", overrides) for name, overrides in RRF_CONFIGS)
        )
        for key, mode, overrides in runs:
            raw = _run(
                dataset,
                mode,
                key,
                overrides,
                reuse_existing=args.reuse_existing,
            )
            raw_by_key[(dataset, key)] = raw
            report["datasets"][dataset]["modes"][key] = raw["summary"]
            report["oracle"][dataset][key] = _oracle_report(raw)
            report["score_scale"][f"{dataset}/{key}"] = _score_scale(raw)
            print(
                f"completed dataset={dataset} mode={key} "
                f"recall={raw['summary']['overall']['required_recall']:.3f} "
                f"precision={raw['summary']['overall']['precision']:.3f}",
                flush=True,
            )

    stability_keys = (
        "structural",
        "raw_bm25",
        "bounded_k10_m4",
        "bounded_k20_m4",
        "bounded_k10_m2",
        "rrf_80_40_k10",
        "rrf_2_1_k10",
        "rrf_3_1_k20",
    )
    for key in stability_keys:
        report["stability"][key] = _stability(
            {dataset: report["datasets"][dataset]["modes"][key] for dataset, _ in DATASETS}
        )

    wanted = (
        "options.md", "handling-files.md", "advanced.md", "black.md", "faq.md",
        "extensions.md", "calls.md", "ManualMocks.md", "Configuration.md", "UsingMatchers.md",
    )
    for dataset, _label in DATASETS:
        raws = {
            "structural": raw_by_key[(dataset, "structural")],
            "raw_bm25": raw_by_key[(dataset, "raw_bm25")],
            "bounded_bm25": raw_by_key[(dataset, "bounded_k10_m4")],
            "rrf": raw_by_key[(dataset, "rrf_80_40_k10")],
        }
        report["trace_comparison"][dataset] = _trace_comparison(raws, wanted)

    exposed_records = []
    for dataset in ("validation", "validation-v0.3", "blind-v0.4"):
        exposed_records.extend(raw_by_key[(dataset, "rrf_80_40_k10")].get("tasks", []))
    report["oracle"]["combined_exposed_validation"] = _oracle_aggregate(exposed_records)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "stability": report["stability"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
