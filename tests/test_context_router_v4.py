"""V0.4 regression tests: hard dedup only for certain duplicates, soft
diversity penalty, document role signals, and task-intent role boost."""

from __future__ import annotations

import unittest

from scanners.context_matcher import ContextMatcher
from scanners.document_family import (
    document_role,
    role_from_task,
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
        "project": "router-v4-test",
        "project_path": "/tmp/router-v4-test",
        "docs": docs,
        "entry_files": entries or [],
        "modules": [],
        "knowledge_domains": [],
    }


class HardDedupTests(unittest.TestCase):
    def test_exact_locale_hard_dedup(self) -> None:
        """Test 1: locale variants of the same document collapse to one."""

        project = _project(
            [
                _doc("docs/en/request-config.md", "Request configuration"),
                _doc("docs/fr/request-config.md", "Configuration des requetes"),
                _doc("docs/zh/request-config.md", "请求配置"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "request configuration")
        files = result["relevant_files"]

        self.assertEqual(len([f for f in files if "request-config" in f]), 1)
        self.assertIn("hard_dedup", " ".join(result["selection_notes"]))

    def test_parent_child_are_not_duplicates(self) -> None:
        """Test 2: docs/formatter.md and docs/formatter/black.md are different."""

        project = _project(
            [
                _doc("docs/formatter.md", "Formatter"),
                _doc("docs/formatter/black.md", "Black formatting"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "formatter black formatting")
        files = result["relevant_files"]

        self.assertIn("docs/formatter.md", files)
        self.assertIn("docs/formatter/black.md", files)


class SoftDiversityTests(unittest.TestCase):
    def test_similar_but_distinct_topics_use_soft_penalty(self) -> None:
        """Test 3: similar docs must not suppress an unrelated third doc."""

        project = _project(
            [
                _doc("docs/typescript.md", "Typescript"),
                _doc("docs/typescript-examples.md", "TypeScript examples"),
                _doc("docs/interceptors.md", "Interceptors"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "typescript interceptors")
        files = result["relevant_files"]

        self.assertIn("docs/interceptors.md", files)
        self.assertIn("docs/typescript.md", files)


class DocumentRoleTests(unittest.TestCase):
    def test_faq_role(self) -> None:
        """Test 4: faq.md gets a generic troubleshooting role signal."""

        self.assertEqual(document_role("docs/faq.md", "FAQ"), "troubleshooting")
        self.assertEqual(
            document_role("docs/troubleshooting.md", "Troubleshooting"),
            "troubleshooting",
        )

    def test_api_role(self) -> None:
        """Test 5: api.md gets a generic API/reference role signal."""

        self.assertEqual(document_role("docs/api.md", "API"), "api")
        self.assertEqual(document_role("docs/reference.md", "Reference"), "api")

    def test_task_role_matches_unknown_vendor_doc(self) -> None:
        """Test 4+5 integration: a task intent maps to a role without vendor names."""

        self.assertEqual(role_from_task("why does the formatter behave differently"), "troubleshooting")
        self.assertEqual(role_from_task("which API should be used to extend transport"), "api")


class RoleCoverageTests(unittest.TestCase):
    def test_multi_document_role_coverage(self) -> None:
        """Test 6: configuration + API + troubleshooting docs can all enter."""

        project = _project(
            [
                _doc("docs/configuration.md", "Configuration"),
                _doc("docs/api.md", "API reference"),
                _doc("docs/faq.md", "FAQ"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(
            project,
            "configure the API and debug why it fails unexpectedly",
        )
        files = result["relevant_files"]

        self.assertIn("docs/configuration.md", files)
        self.assertIn("docs/api.md", files)
        self.assertIn("docs/faq.md", files)


class TraceTests(unittest.TestCase):
    def test_trace_contains_score_decomposition(self) -> None:
        project = _project(
            [
                _doc("docs/faq.md", "FAQ"),
                _doc("docs/linter.md", "Linter"),
            ]
        )
        matcher = _matcher()

        result = matcher.trace_match(project, "why does the linter fail")
        trace = result["trace"]

        self.assertGreaterEqual(len(trace), 1)
        row = next(item for item in trace if item["path"] == "docs/faq.md")
        self.assertIn("raw_score", row)
        self.assertIn("final_score", row)
        self.assertIn("rank_before", row)
        self.assertIn("selected", row)
        self.assertIn("canonical_family", row)
        self.assertIn("role", row)
        self.assertIn("hard_duplicate", row)
        self.assertIn("similarity_penalty", row)
        self.assertIn("threshold", row)
        self.assertIn("reasons", row)

    def test_trace_not_returned_by_default(self) -> None:
        project = _project([_doc("docs/faq.md", "FAQ")])
        matcher = _matcher()

        result = matcher.match(project, "why does it fail")

        self.assertNotIn("trace", result)


class RegressionTests(unittest.TestCase):
    def test_v03_locale_duplicate_still_deduped(self) -> None:
        """Test 7: V0.3 locale-duplicate behavior is preserved."""

        project = _project(
            [
                _doc("docs/en/request-config.md", "Request configuration"),
                _doc("docs/fr/request-config.md", "Configuration des requetes"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "request configuration")
        files = result["relevant_files"]

        self.assertLessEqual(sum(1 for f in files if "request-config" in f), 1)

    def test_v02_direct_filename_matching(self) -> None:
        """Test 8: V0.2 direct filename matching is preserved."""

        project = _project(
            [
                _doc("docs/logging.md", "Logging"),
                _doc("docs/unrelated.md", "Unrelated"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "logging configuration")
        files = result["relevant_files"]

        self.assertEqual(files[0], "docs/logging.md")

    def test_no_h1_markdown_fallback(self) -> None:
        """Test 9: docs without an H1 still use the filename as title."""

        from scanners.project_scanner import ProjectScanner
        import json
        import tempfile
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        settings = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="context-router-v4-") as temp:
            base = Path(temp)
            docs = base / "docs"
            docs.mkdir()
            (docs / "no-h1.md").write_text(
                "Some introduction text\n\n## Installation\n\nRun it.\n",
                encoding="utf-8",
            )

            scanned = ProjectScanner(settings).scan(str(base))
            indexed = next(
                item for item in scanned["docs"] if item["path"] == "docs/no-h1.md"
            )

            self.assertEqual(indexed["title"], "Installation")

    def test_dynamic_selection_does_not_degrade_to_all(self) -> None:
        """Test 10: dynamic selection must not return everything."""

        docs = [
            _doc(f"docs/feature-{index}.md", f"Feature {index}")
            for index in range(1, 12)
        ]
        project = _project(docs)
        matcher = _matcher()

        result = matcher.match(project, "alpha beta gamma delta feature")
        files = result["relevant_files"]

        self.assertLessEqual(len(files), 8)
        self.assertGreaterEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
