"""Candidate Retrieval V0.1 regression ratchet.

Reads the feasibility report (produced by ``analyze_candidate_retrieval.py``)
and computes the metrics that gate Candidate Retrieval.  The regression floors
below are conservative guardrails derived from the V0.1 baseline; they are not
product marketing numbers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "benchmarks" / "results" / "candidate-retrieval-feasibility.json"

# Conservative floors (guardrails, not claims).
FLOORS: dict[str, float] = {
    "required_recall_at_10": 0.93,
    "worst_dataset_recall_at_10": 0.88,
    "full_context_reduction": 0.60,
    "metadata_size_headroom": 1.15,  # p95 metadata must not grow >15% vs baseline
}


def _dataset_key(label: str) -> str:
    return label.split(":")[0].strip()


def compute_metrics(report: dict[str, Any]) -> dict[str, float]:
    """Extract the ratchet metrics from a feasibility report."""

    overall = report["overall_curve"]["10"]
    dataset_recalls = [
        value["curve"]["10"]["required_recall"]
        for value in report["datasets"].values()
    ]
    return {
        "required_recall_at_10": float(overall["required_recall"]),
        "worst_dataset_recall_at_10": min(dataset_recalls) if dataset_recalls else 0.0,
        "all_required_found_at_10": float(overall["all_required_found_rate"]),
        "full_context_reduction": float(overall["full_context_reduction"]),
        "metadata_reduction": float(overall["metadata_reduction"]),
        "avg_metadata_chars": float(overall["avg_metadata_chars"]),
        "p95_metadata_chars": float(overall["p95_metadata_chars"]),
    }


def load_report() -> dict[str, Any] | None:
    if not REPORT_PATH.is_file():
        return None
    with REPORT_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def baseline() -> dict[str, float] | None:
    """Current baseline values, or None when the report is unavailable."""

    report = load_report()
    if report is None:
        return None
    return compute_metrics(report)


def _baseline_p95_metadata() -> float:
    report = load_report()
    if report is None:
        return 0.0
    return float(report["overall_curve"]["10"]["p95_metadata_chars"])


def _measured_p95_metadata() -> float:
    from benchmarks import measure_candidate_metadata

    measured = measure_candidate_metadata.measure_metadata()
    if not measured.get("available"):
        return 0.0
    return float(measured["p95_metadata_chars"])


def _baseline_measured_p95_metadata() -> float:
    # Persisted by write_baseline after the authoritative measurement run.
    target = ROOT / "benchmarks" / "baselines" / "candidate-retrieval-v0.1.json"
    if not target.is_file():
        return 0.0
    artifact = json.loads(target.read_text(encoding="utf-8"))
    return float(artifact.get("measured", {}).get("p95_metadata_chars", 0.0))


def check() -> dict[str, Any]:
    """Evaluate the ratchet and return pass/fail with per-metric detail."""

    metrics = baseline()
    if metrics is None:
        return {"available": False, "passed": False, "reason": "report missing"}
    results = {
        "required_recall_at_10": metrics["required_recall_at_10"] >= FLOORS["required_recall_at_10"],
        "worst_dataset_recall_at_10": metrics["worst_dataset_recall_at_10"] >= FLOORS["worst_dataset_recall_at_10"],
        "full_context_reduction": metrics["full_context_reduction"] >= FLOORS["full_context_reduction"],
        "metadata_size_headroom": metrics["p95_metadata_chars"] <= _baseline_p95_metadata() * FLOORS["metadata_size_headroom"],
        "measured_metadata_size_headroom": _measured_p95_metadata() <= _baseline_measured_p95_metadata() * FLOORS["metadata_size_headroom"]
        if _baseline_measured_p95_metadata()
        else True,
    }
    return {
        "available": True,
        "passed": all(results.values()),
        "metrics": metrics,
        "floors": FLOORS,
        "results": results,
    }


def write_baseline(path: Path | None = None) -> dict[str, Any]:
    """Persist the current baseline as a tracked JSON artifact."""

    metrics = baseline()
    if metrics is None:
        raise FileNotFoundError(f"feasibility report not found: {REPORT_PATH}")
    from benchmarks import measure_candidate_metadata

    measured = measure_candidate_metadata.measure_metadata()
    # The product-level metadata size is the authoritative number; the
    # feasibility analysis uses a benchmark-only pack format and is kept only
    # as the recall/reduction source of truth.
    metrics["avg_metadata_chars"] = float(measured.get("avg_metadata_chars", 0.0))
    metrics["p95_metadata_chars"] = float(measured.get("p95_metadata_chars", 0.0))
    target = path or (ROOT / "benchmarks" / "baselines" / "candidate-retrieval-v0.1.json")
    artifact = {
        "name": "candidate-retrieval-v0.1",
        "metrics": metrics,
        "measured": measured,
        "floors": FLOORS,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact
