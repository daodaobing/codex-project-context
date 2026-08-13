"""V0.5 ranking tests: compound normalization, BM25-like IDF, field weights,
and query coverage.  Selection-layer behavior is frozen at V0.4 and must not
change; these tests only verify the new ranking signals."""

from __future__ import annotations

import unittest

from scanners.context_matcher import ContextMatcher
from benchmarks.run_benchmark import _aggregate
from benchmarks.analyze_candidate_retrieval import (
    _candidate_pack,
    _ranked_rows,
    _required_doc_rank,
    _retrieval_metrics,
    _serialize_candidate_pack,
)


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
        }
    )


def _doc(path: str, title: str, headings: list[str] | None = None, summary: str = "") -> dict:
    return {
        "path": path,
        "title": title,
        "headings": headings or [title],
        "summary": summary or title,
        "categories": [],
        "role": "other",
    }


def _project(docs: list[dict], entries: list[dict] | None = None) -> dict:
    return {
        "project": "router-v5-test",
        "project_path": "/tmp/router-v5-test",
        "docs": docs,
        "entry_files": entries or [],
        "modules": [],
        "knowledge_domains": [],
    }


class CompoundNormalizationTests(unittest.TestCase):
    def test_camel_case_compound_split(self) -> None:
        """Test 1: SetupAndTeardown splits into set/up/tear/down tokens."""

        tokens = ContextMatcher._tokenize("SetupAndTeardown", drop_stopwords=True)
        self.assertIn("set", tokens)
        self.assertIn("up", tokens)
        self.assertIn("tear", tokens)
        self.assertIn("down", tokens)

    def test_setup_teardown_matches_split_phrase(self) -> None:
        """Test 2: compound expansion aligns setup/teardown with set up/tear down."""

        compound = ContextMatcher._tokenize("setup and teardown", drop_stopwords=True)
        phrase = ContextMatcher._tokenize("set up and tear down", drop_stopwords=True)
        self.assertEqual(compound, phrase)

    def test_phrasal_compound_table(self) -> None:
        """Test 3: generic phrasal compounds expand to two tokens."""

        self.assertEqual(
            ContextMatcher._tokenize("codebase", drop_stopwords=False),
            ["code", "base"],
        )
        self.assertEqual(
            ContextMatcher._tokenize("commandline", drop_stopwords=False),
            ["command", "line"],
        )

    def test_compound_does_not_affect_selection_input(self) -> None:
        """Test 4: similarity penalty still uses the V0.4 token lists."""

        matcher = _matcher()
        compound_doc = _doc("docs/SetupAndTeardown.md", "Setup and Teardown")
        selection_tokens = matcher._selection_document_tokens(compound_doc)
        self.assertIn("setup", selection_tokens)
        self.assertIn("teardown", selection_tokens)
        self.assertNotIn("set", selection_tokens)
        self.assertNotIn("tear", selection_tokens)
        project = _project(
            [
                compound_doc,
                _doc("docs/teardown.md", "Teardown"),
                _doc("docs/unrelated.md", "Unrelated"),
            ]
        )
        result = matcher.match(project, "set up and tear down the test")
        files = result["relevant_files"]
        self.assertIn("docs/SetupAndTeardown.md", files)
        self.assertIn("docs/teardown.md", files)


class BM25LikeTests(unittest.TestCase):
    def test_rare_term_gets_higher_idf(self) -> None:
        """Test 5: a rare term outranks a common term with equal frequency."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/rare.md", "Rare jsdom term", summary="jsdom appears here"),
                _doc("docs/common.md", "Common mock term", summary="mock appears here"),
                _doc("docs/other.md", "Other mock", summary="other mock"),
            ]
        )
        corpus = matcher._build_corpus(project)
        fields_rare = matcher._document_fields(project["docs"][0])
        fields_common = matcher._document_fields(project["docs"][1])
        rare = matcher._bm25_for_doc(corpus, fields_rare, {"jsdom"})
        common = matcher._bm25_for_doc(corpus, fields_common, {"mock"})
        self.assertGreater(rare, common)

    def test_absent_term_contributes_nothing(self) -> None:
        """Test 6: a term missing from the corpus scores zero."""

        matcher = _matcher()
        project = _project([_doc("docs/a.md", "Alpha")])
        corpus = matcher._build_corpus(project)
        fields = matcher._document_fields(project["docs"][0])
        self.assertEqual(matcher._bm25_for_doc(corpus, fields, {"zzz"}), 0.0)

    def test_entry_files_participate_in_repository_corpus(self) -> None:
        """Entry files are indexed context and must affect repository IDF."""

        matcher = _matcher()
        project = _project(
            [_doc("docs/alpha.md", "Alpha")],
            entries=[_doc("README.md", "README", summary="alpha")],
        )
        corpus = matcher._build_corpus(project)
        self.assertEqual(corpus["title"]["n"], 2)

    def test_trace_includes_candidates_below_selection_threshold(self) -> None:
        """Raw ranking diagnostics must retain documents selection drops."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/target.md", "Target topic"),
                _doc("docs/other.md", "Other topic"),
            ]
        )
        result = matcher.trace_match(project, "target")
        paths = {row["path"] for row in result["trace"]}
        self.assertEqual(paths, {"docs/target.md", "docs/other.md"})
        ranks = {row["path"]: row["rank_before"] for row in result["trace"]}
        self.assertEqual(ranks["docs/target.md"], 1)
        self.assertEqual(ranks["docs/other.md"], 2)

    def test_trace_fallback_has_threshold_when_nothing_matches(self) -> None:
        """Fallback routing must remain traceable for an all-zero query."""

        matcher = _matcher()
        result = matcher.trace_match(_project([_doc("docs/alpha.md", "Alpha")]), "zzzz")
        self.assertEqual(result["relevant_files"], ["docs/alpha.md"])
        row = result["trace"][0]
        self.assertEqual(row["path"], "docs/alpha.md")
        self.assertEqual(row["threshold"], 1.0)

    def test_filename_field_boosted_above_summary(self) -> None:
        """Test 7: filename match contributes more than a summary-only match."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/tls.md", "TLS", summary="plain"),
                _doc("docs/other.md", "Other", summary="tls tls tls"),
            ]
        )
        corpus = matcher._build_corpus(project)
        fields_filename = matcher._document_fields(project["docs"][0])
        fields_summary = matcher._document_fields(project["docs"][1])
        filename_score = matcher._bm25_for_doc(corpus, fields_filename, {"tls"})
        summary_score = matcher._bm25_for_doc(corpus, fields_summary, {"tls"})
        self.assertGreater(filename_score, summary_score)


class QueryCoverageTests(unittest.TestCase):
    def test_higher_coverage_gets_bonus(self) -> None:
        """Test 8: covering more task tokens earns the coverage bonus."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/one.md", "One",
                     summary="covering one token light"),
                _doc("docs/two.md", "Other",
                     summary="covering one token light two"),
            ]
        )
        result = matcher.match(
            project, "one two light token covering"
        )
        files = result["relevant_files"]
        self.assertIn("docs/two.md", files)


class RankingIntegrationTests(unittest.TestCase):
    def test_compound_task_ranks_compound_doc_first(self) -> None:
        """Test 9: SetupAndTeardown doc wins for a set-up/tear-down task."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/SetupAndTeardown.md", "Setup and Teardown",
                     ["Repeating Setup", "One-Time Setup"],
                     "beforeEach afterEach hooks run setup and teardown"),
                _doc("docs/TimerMocks.md", "Timer Mocks",
                     ["Fake timers"],
                     "fake timers control time"),
            ]
        )
        result = matcher.trace_match(project, "set up and tear down timer mocks")
        trace = result["trace"]
        setup_row = next(r for r in trace if "SetupAndTeardown" in r["path"])
        timer_row = next(r for r in trace if "TimerMocks" in r["path"])
        self.assertGreater(setup_row["raw_score"], timer_row["raw_score"])

    def test_rare_jsdoc_term_ranks_above_mock_cluster(self) -> None:
        """Test 10: rare term beats a cluster of common mock docs."""

        matcher = _matcher()
        project = _project(
            [
                _doc("docs/MockFunctionAPI.md", "Mock Function API",
                     ["mock functions"], "mock functions API"),
                _doc("docs/MockFunctions.md", "Mock Functions",
                     ["mock functions"], "mock functions"),
                _doc("docs/ManualMocks.md", "Manual Mocks",
                     ["manual mocks"], "manual mocks fake data"),
                _doc("docs/Configuration.md", "Configuring Jest",
                     ["options"], "configuring jest options"),
                _doc("docs/TestEnvironment.md", "Test Environment",
                     ["environment"], "test environment jsdom node"),
            ]
        )
        result = matcher.trace_match(
            project, "configure the test environment to use jsdom"
        )
        trace = result["trace"]
        env_row = next(r for r in trace if "TestEnvironment" in r["path"])
        config_row = next(r for r in trace if "Configuration" in r["path"])
        self.assertGreater(env_row["raw_score"], config_row["raw_score"])


class BenchmarkMetricTests(unittest.TestCase):
    def test_candidate_required_recall_at_k(self) -> None:
        metrics = _retrieval_metrics(
            ["a.md", "c.md"], ["b.md"], ["a.md", "b.md", "x.md", "c.md"], 3
        )
        self.assertEqual(metrics["required_recall"], 0.5)
        self.assertEqual(metrics["relevant_recall"], 2 / 3)
        self.assertEqual(metrics["precision"], 2 / 3)

    def test_candidate_metadata_serialization_is_deterministic(self) -> None:
        rows = [{"path": "docs/a.md", "rank_before": 1, "fusion_score": 1.5, "reasons": ["match", "match"]}]
        metadata = {"docs/a.md": {"title": "A", "summary": "Summary", "role": "guide"}}
        pack = _candidate_pack("demo", "task", rows, metadata, 1)
        self.assertEqual(_serialize_candidate_pack(pack), _serialize_candidate_pack(pack))
        self.assertEqual(pack["candidates"][0]["reasons"], ["match"])

    def test_candidate_ordering_is_deterministic(self) -> None:
        rows = [
            {"path": "b.md", "rank_before": 2},
            {"path": "a.md", "rank_before": 2},
            {"path": "c.md", "rank_before": 1},
        ]
        self.assertEqual([row["path"] for row in _ranked_rows(rows)], ["c.md", "a.md", "b.md"])

    def test_candidate_k_above_document_count(self) -> None:
        metrics = _retrieval_metrics(["a.md"], [], ["a.md"], 10)
        self.assertEqual(metrics["candidate_count"], 1)
        self.assertEqual(metrics["required_recall"], 1.0)
        self.assertEqual(metrics["precision"], 0.1)

    def test_required_document_rank(self) -> None:
        rows = [{"path": "b.md", "rank_before": 2}, {"path": "a.md", "rank_before": 1}]
        self.assertEqual(_required_doc_rank(rows, "b.md"), 2)
        self.assertIsNone(_required_doc_rank(rows, "missing.md"))

    def test_aggregate_rank_metrics_are_weighted_by_required_docs(self) -> None:
        records = [
            {
                "status": "ok",
                "full_context_chars": 10,
                "selected_context_chars": 5,
                "full_context_bytes": 10,
                "selected_context_bytes": 5,
                "full_context_doc_count": 2,
                "selected_doc_count": 1,
                "required_count": 1,
                "required_hits": [],
                "selected_relevant_count": 0,
                "route_latency_ms": 1.0,
                "required_ranked_count": 1,
                "required_mean_rank": 1.0,
                "required_top1_count": 1,
                "required_top3_count": 1,
                "required_top6_count": 1,
                "required_mrr": 1.0,
            },
            {
                "status": "ok",
                "full_context_chars": 10,
                "selected_context_chars": 5,
                "full_context_bytes": 10,
                "selected_context_bytes": 5,
                "full_context_doc_count": 2,
                "selected_doc_count": 1,
                "required_count": 3,
                "required_hits": [],
                "selected_relevant_count": 0,
                "route_latency_ms": 1.0,
                "required_ranked_count": 3,
                "required_mean_rank": 3.0,
                "required_top1_count": 0,
                "required_top3_count": 1,
                "required_top6_count": 3,
                "required_mrr": 1 / 3,
            },
        ]
        summary = _aggregate(records, [1.0])
        self.assertEqual(summary["required_mean_rank"], 2.5)
        self.assertEqual(summary["required_top1_rate"], 0.25)
        self.assertEqual(summary["required_mrr"], 0.5)


if __name__ == "__main__":
    unittest.main()
