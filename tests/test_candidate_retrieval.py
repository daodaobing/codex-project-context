"""Candidate Retrieval V0.1 tests: contract, parity, ratchet, instrumentation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from benchmarks import candidate_ratchet
from scanners import instrumentation
from scanners import candidate_retriever
from scanners.context_matcher import ContextMatcher


ROOT = Path(__file__).resolve().parent.parent
_RAW_RRF = ROOT / "benchmarks" / "results" / "runs" / "v05-fusion-rrf_80_40_k10-diagnostic" / "raw-results.json"


def _matcher() -> ContextMatcher:
    return ContextMatcher(
        {
            "rules": {},
            "index": {
                "top_relevant_files": 6,
                "max_relevant_files": 8,
                "min_relevant_score": 1.0,
                "relative_score_floor": 0.35,
                "fallback_relevant_files": 2,
                "diversity_penalty": 0.35,
                "soft_diversity_floor": 0.6,
                "role_boost": 1.0,
                "bm25_weight": 1.0,
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
                "coverage_bonus": 2.0,
            },
        },
        ranking_mode="rrf",
    )


def _doc(path: str, title: str, headings: list[str] | None = None) -> dict:
    return {
        "path": path,
        "title": title,
        "headings": headings or [title],
        "summary": title,
        "categories": [],
        "role": "other",
    }


def _project(docs: list[dict]) -> dict:
    return {
        "project": "candidate-test",
        "project_path": "/tmp/candidate-test",
        "docs": docs,
        "entry_files": [],
        "modules": [],
        "knowledge_domains": [],
        "scanned_at": "2026-08-13T00:00:00+0800",
    }


class CandidateContractTests(unittest.TestCase):
    def test_validate_limit_bounds(self) -> None:
        self.assertEqual(candidate_retriever.validate_limit(1), 1)
        self.assertEqual(candidate_retriever.validate_limit(20), 20)
        self.assertEqual(candidate_retriever.validate_limit(10), 10)
        self.assertIsNone(candidate_retriever.validate_limit(0))
        self.assertIsNone(candidate_retriever.validate_limit(21))
        self.assertIsNone(candidate_retriever.validate_limit("10"))
        self.assertIsNone(candidate_retriever.validate_limit(True))

    def test_default_limit_is_10(self) -> None:
        self.assertEqual(candidate_retriever.DEFAULT_LIMIT, 10)

    def test_candidate_ranking_deterministic_and_bounded(self) -> None:
        docs = [_doc(f"docs/d{i}.md", f"Doc {i}") for i in range(25)]
        matcher = _matcher()
        ranked = matcher.candidate_ranking(_project(docs), "doc 0 doc 1", limit=10)
        self.assertLessEqual(len(ranked), 10)
        paths = [str(doc.get("path")) for _, doc, _ in ranked]
        self.assertEqual(paths, [str(doc.get("path")) for _, doc, _ in matcher.candidate_ranking(_project(docs), "doc 0 doc 1", limit=10)])

    def test_candidate_reasons_are_bounded_and_concise(self) -> None:
        reasons = [
            "bm25=12.3",
            "filename token match: config",
            "component:structural=4.0",
            "fusion:rrf_score=10.0",
            "role match: configuration",
            "title token match: config",
        ]
        labels = ContextMatcher.candidate_reasons(reasons)
        self.assertEqual(labels, ["filename match", "role match", "title match"])

    def test_candidate_pack_is_metadata_only(self) -> None:
        docs = [
            _doc("docs/config.md", "Configuration"),
            _doc("docs/api.md", "API Reference"),
        ]
        pack = candidate_retriever.build_candidates(_project(docs), "configure", 10)
        self.assertEqual(pack["candidate_count"], 2)
        for candidate in pack["candidates"]:
            self.assertNotIn("content", candidate)
            self.assertNotIn("body", candidate)
            self.assertNotIn("full_text", candidate)
            for key in ("path", "title", "summary", "role", "rank", "reasons"):
                self.assertIn(key, candidate)


class QueryCoverageZeroTests(unittest.TestCase):
    def test_candidate_ranking_has_no_coverage_contribution(self) -> None:
        docs = [_doc("docs/a.md", "A"), _doc("docs/b.md", "B")]
        matcher = _matcher()
        ranked = matcher.candidate_ranking(_project(docs), "a b", limit=10)
        for _, _, reasons in ranked:
            self.assertFalse(any(r.startswith("coverage=") for r in reasons))


class InstrumentationTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            instrumentation.ENV_RUN_ID,
            instrumentation.ENV_TASK_ID,
            instrumentation.ENV_DIR,
        ):
            os.environ.pop(key, None)

    def test_default_off(self) -> None:
        self.assertFalse(instrumentation.enabled())

    def test_opt_in_records_jsonl_without_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            os.environ[instrumentation.ENV_RUN_ID] = "run-1"
            os.environ[instrumentation.ENV_TASK_ID] = "task-1"
            os.environ[instrumentation.ENV_DIR] = temp
            instrumentation.record_event(
                {
                    "tool_name": "get_context_candidates",
                    "called": True,
                    "candidate_paths": ["docs/a.md"],
                    "source_content": "SECRET SOURCE",
                }
            )
            line = (Path(temp) / "events.jsonl").read_text(encoding="utf-8")
            event = json.loads(line)
            self.assertTrue(event["called"])
            self.assertEqual(event["tool_name"], "get_context_candidates")
            # source content is dropped by the producer; the recorder must not
            # invent it.  Assert the recorder never emits a source field here.
            self.assertNotIn("source_content", json.loads(line))

    def test_record_event_off_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            os.environ[instrumentation.ENV_DIR] = temp
            instrumentation.record_event({"tool_name": "get_context_candidates"})
            self.assertFalse((Path(temp) / "events.jsonl").exists())


class RatchetTests(unittest.TestCase):
    def test_baseline_available_when_report_present(self) -> None:
        metrics = candidate_ratchet.baseline()
        if metrics is None:
            self.skipTest("feasibility report not present")
        self.assertGreaterEqual(metrics["required_recall_at_10"], candidate_ratchet.FLOORS["required_recall_at_10"])
        self.assertGreaterEqual(metrics["worst_dataset_recall_at_10"], candidate_ratchet.FLOORS["worst_dataset_recall_at_10"])
        self.assertGreaterEqual(metrics["full_context_reduction"], candidate_ratchet.FLOORS["full_context_reduction"])
        self.assertGreater(metrics["all_required_found_at_10"], 0.0)


class BenchmarkParityTests(unittest.TestCase):
    """Candidate tool Top-N must match benchmark raw RRF Top-N."""

    def test_candidate_top10_matches_benchmark_raw_rrf(self) -> None:
        if not _RAW_RRF.is_file():
            self.skipTest("benchmark raw RRF results not present")
        raw = json.loads(_RAW_RRF.read_text(encoding="utf-8"))
        task = raw["tasks"][0]
        repo = next(r for r in raw["repositories"] if r["id"] == task["repository"])
        workspace = Path(repo["workspace"])
        if not workspace.is_dir():
            self.skipTest("benchmark workspace not cached")

        import server

        project = server._ensure_project(str(workspace), force=False)
        pack = candidate_retriever.build_candidates(project, task["task"], 10)
        got = [c["path"] for c in pack["candidates"]]

        # benchmark raw RRF ordering = trace rows with positive raw score,
        # ordered by rank_before then path (mirrors _ranked_rows dedup).
        seen: dict[str, int] = {}
        for row in task["route"]["trace"]:
            path = row.get("path")
            rank = row.get("rank_before")
            score = row.get("raw_score")
            if not path or not isinstance(rank, int):
                continue
            if score is not None and float(score) <= 0:
                continue
            seen.setdefault(str(path), int(rank))
        expected = [p for p, _ in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))][:10]
        self.assertEqual(got, expected)


if __name__ == "__main__":
    unittest.main()
