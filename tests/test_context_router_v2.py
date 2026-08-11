"""Focused regression tests for the repository-content-driven router."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scanners.context_matcher import ContextMatcher
from scanners.project_scanner import ProjectScanner


ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


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
            },
        }
    )


def _project(docs: list[dict], entries: list[dict] | None = None) -> dict:
    return {
        "project": "router-test",
        "project_path": "/tmp/router-test",
        "docs": docs,
        "entry_files": entries or [],
        "modules": [],
        "knowledge_domains": [],
    }


class MarkdownFallbackTests(unittest.TestCase):
    def test_markdown_without_h1_uses_filename_title(self) -> None:
        with tempfile.TemporaryDirectory(prefix="context-router-no-h1-") as temp:
            root = Path(temp)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            docs = root / "docs"
            docs.mkdir()
            (docs / "no-h1.md").write_text(
                "Some introduction text\n\n## Installation\n\nRun it.\n",
                encoding="utf-8",
            )
            (docs / "plain.md").write_text(
                "Some introduction text without any heading.\n",
                encoding="utf-8",
            )

            result = ProjectScanner(SETTINGS).scan(str(root))

            indexed = next(item for item in result["docs"] if item["path"] == "docs/no-h1.md")
            self.assertEqual(indexed["title"], "Installation")
            self.assertEqual(indexed["headings"], ["Installation"])
            plain = next(item for item in result["docs"] if item["path"] == "docs/plain.md")
            self.assertEqual(plain["title"], "plain")


class ContentRoutingTests(unittest.TestCase):
    def test_filename_phrase_match_beats_advanced_and_readme(self) -> None:
        project = _project(
            [
                {
                    "path": "docs/shell-completion.md",
                    "title": "Shell completion",
                    "headings": ["Shell completion"],
                    "summary": "Configure shell completion.",
                },
                {
                    "path": "docs/advanced.md",
                    "title": "Advanced",
                    "headings": ["Advanced"],
                    "summary": "Advanced topics.",
                },
            ],
            [
                {
                    "path": "README.md",
                    "title": "README",
                    "headings": ["Project"],
                    "summary": "Project overview.",
                }
            ],
        )
        matcher = _matcher()

        analyzed = matcher.analyze(project, "fix shell completion behavior")
        scores = {doc["path"]: score for score, doc, _ in analyzed["docs"]}
        reasons = next(reasons for score, doc, reasons in analyzed["docs"] if doc["path"] == "docs/shell-completion.md")
        selected = matcher.match(project, "fix shell completion behavior")["relevant_files"]

        self.assertGreater(scores["docs/shell-completion.md"], scores["README.md"])
        self.assertGreater(scores["docs/shell-completion.md"], scores["docs/advanced.md"])
        self.assertEqual(selected[0], "docs/shell-completion.md")
        self.assertTrue(any(reason.startswith("filename token match") for reason in reasons))
        self.assertTrue(any(reason.startswith("phrase match") for reason in reasons))

    def test_logging_filename_match_is_stronger_than_entry_bonus(self) -> None:
        project = _project(
            [
                {
                    "path": "docs/logging.md",
                    "title": "Logging",
                    "headings": ["Logging configuration"],
                    "summary": "Configure logging output.",
                }
            ],
            [{"path": "README.md", "title": "README", "headings": [], "summary": "Overview."}],
        )
        matcher = _matcher()

        result = matcher.match(project, "logging configuration")

        self.assertEqual(result["relevant_files"], ["docs/logging.md"])

    def test_unknown_domain_uses_repository_filename_and_heading(self) -> None:
        project = _project(
            [
                {
                    "path": "docs/orchestration.md",
                    "title": "Orchestration",
                    "headings": ["Orchestration workflow"],
                    "summary": "Workflow orchestration.",
                },
                {
                    "path": "docs/unrelated.md",
                    "title": "Unrelated",
                    "headings": ["Other topic"],
                    "summary": "Other topic.",
                },
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "orchestration workflow")

        self.assertEqual(result["relevant_files"], ["docs/orchestration.md"])

    def test_dynamic_selection_uses_small_fallback_and_hard_cap(self) -> None:
        simple_project = _project(
            [
                {
                    "path": "docs/unrelated.md",
                    "title": "Unrelated",
                    "headings": [],
                    "summary": "No matching topic.",
                }
            ],
            [{"path": "README.md", "title": "README", "headings": [], "summary": "Overview."}],
        )
        complex_docs = [
            {
                "path": f"docs/feature-{index}.md",
                "title": f"Feature {index}",
                "headings": ["Alpha beta gamma delta"],
                "summary": "Alpha beta gamma delta feature.",
            }
            for index in range(1, 10)
        ]
        complex_project = _project(complex_docs)
        matcher = _matcher()

        simple = matcher.match(simple_project, "quantum widget")["relevant_files"]
        complex_result = matcher.match(
            complex_project,
            "alpha beta gamma delta feature",
        )["relevant_files"]

        self.assertLessEqual(len(simple), 2)
        self.assertGreaterEqual(len(complex_result), 5)
        self.assertLessEqual(len(complex_result), 8)


if __name__ == "__main__":
    unittest.main()
