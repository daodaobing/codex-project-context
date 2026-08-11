"""V0.3 regression tests for canonical family dedupe and diversity selection."""

from __future__ import annotations

import unittest

from scanners.context_matcher import ContextMatcher
from scanners.document_family import (
    alias_normalize,
    alias_similar,
    canonical_family,
    locale_of,
    token_overlap_ratio,
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
                "family_hard_cap": 8,
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


def _project(docs: list[dict]) -> dict:
    return {
        "project": "router-v3-test",
        "project_path": "/tmp/router-v3-test",
        "docs": docs,
        "entry_files": [],
        "modules": [],
        "knowledge_domains": [],
    }


class CanonicalFamilyTests(unittest.TestCase):
    def test_locale_segments_collapse_to_same_family(self) -> None:
        self.assertEqual(
            canonical_family("docs/en/request-config.md"),
            canonical_family("docs/fr/request-config.md"),
        )
        self.assertEqual(
            canonical_family("docs/zh-cn/request-config.md"),
            canonical_family("docs/en/request-config.md"),
        )

    def test_no_locale_and_locale_share_family(self) -> None:
        self.assertEqual(
            canonical_family("docs/request-config.md"),
            canonical_family("docs/fr/request-config.md"),
        )

    def test_filename_stem_normalization(self) -> None:
        self.assertEqual(
            canonical_family("docs/request-config.md"),
            canonical_family("docs/request_config.md"),
        )
        self.assertEqual(
            canonical_family("docs/RequestConfig.md"),
            canonical_family("docs/request-config.md"),
        )

    def test_different_topics_are_not_merged(self) -> None:
        families = {
            canonical_family(path)
            for path in (
                "docs/auth/configuration.md",
                "docs/auth/troubleshooting.md",
                "docs/auth/security.md",
            )
        }
        self.assertEqual(len(families), 3)


class LocalePreferenceTests(unittest.TestCase):
    def test_no_locale_original_wins_over_locale_variant(self) -> None:
        project = _project(
            [
                _doc("docs/fr/request-config.md", "Configuration des requetes"),
                _doc("docs/request-config.md", "Request configuration"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "request configuration")

        self.assertEqual(result["relevant_files"][0], "docs/request-config.md")


class MultilingualFamilySelectionTests(unittest.TestCase):
    def test_multilingual_family_does_not_occupy_every_slot(self) -> None:
        project = _project(
            [
                _doc("docs/en/request-config.md", "Request configuration"),
                _doc("docs/fr/request-config.md", "Configuration des requetes"),
                _doc("docs/zh-cn/request-config.md", "请求配置"),
                _doc("docs/interceptors.md", "Interceptors"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "configure request interceptors")
        files = result["relevant_files"]

        self.assertIn("docs/interceptors.md", files)
        self.assertLessEqual(sum(1 for item in files if "request-config" in item), 1)

    def test_different_auth_topics_are_not_merged(self) -> None:
        project = _project(
            [
                _doc("docs/auth/configuration.md", "Auth configuration"),
                _doc("docs/auth/troubleshooting.md", "Auth troubleshooting"),
                _doc("docs/auth/security.md", "Auth security"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "authentication configuration and security troubleshooting")
        files = result["relevant_files"]

        self.assertGreaterEqual(len(files), 2)

    def test_similar_examples_are_diversity_limited(self) -> None:
        docs = [
            _doc(f"examples/typescript/{name}.md", name.replace("-", " ").title())
            for name in (
                "basic",
                "headers",
                "request",
                "advanced",
                "timeouts",
                "streaming",
                "interceptors",
                "authentication",
                "cancellation",
            )
        ]
        project = _project(docs)
        matcher = _matcher()

        result = matcher.match(project, "typescript request headers")
        files = result["relevant_files"]

        self.assertLessEqual(len(files), 8)
        self.assertGreaterEqual(len(files), 1)


class AliasTests(unittest.TestCase):
    def test_alias_normalization(self) -> None:
        self.assertEqual(alias_normalize("config"), "configuration")
        self.assertEqual(alias_normalize("auth"), "authentication")
        self.assertEqual(alias_normalize("lifecycle"), "lifespan")
        self.assertEqual(alias_normalize("cli"), "commandline")

    def test_alias_similar_for_lifecycle_lifespan(self) -> None:
        self.assertTrue(alias_similar("lifecycle", "lifespan"))
        self.assertFalse(alias_similar("lifecycle", "interceptor"))

    def test_alias_does_not_boost_unrelated_docs(self) -> None:
        project = _project(
            [
                _doc("docs/lifespan.md", "Lifespan"),
                _doc("docs/interceptors.md", "Interceptors"),
                _doc("docs/security.md", "Security"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "server lifecycle")
        files = result["relevant_files"]

        self.assertIn("docs/lifespan.md", files)
        # Unrelated docs must not be boosted into the top slot.
        self.assertNotEqual(files[0], "docs/security.md")


class RegressionTests(unittest.TestCase):
    def test_direct_filename_match_is_preserved(self) -> None:
        project = _project(
            [
                _doc("docs/logging.md", "Logging"),
                _doc("docs/unrelated.md", "Unrelated"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "logging configuration")

        self.assertEqual(result["relevant_files"][0], "docs/logging.md")

    def test_dynamic_selection_does_not_return_everything(self) -> None:
        docs = [
            _doc(f"docs/feature-{index}.md", f"Feature {index}")
            for index in range(1, 10)
        ]
        project = _project(docs)
        matcher = _matcher()

        result = matcher.match(project, "alpha beta gamma delta feature")

        self.assertLessEqual(len(result["relevant_files"]), 8)

    def test_selection_notes_are_explainable(self) -> None:
        project = _project(
            [
                _doc("docs/fr/request-config.md", "Configuration des requetes"),
                _doc("docs/en/request-config.md", "Request configuration"),
                _doc("docs/interceptors.md", "Interceptors"),
            ]
        )
        matcher = _matcher()

        result = matcher.match(project, "configure request interceptors")
        notes = " ".join(result["selection_notes"])

        self.assertIn("canonical_family=docs/request-config", notes)
        self.assertIn("duplicate_family_skipped", notes)


class TokenOverlapTests(unittest.TestCase):
    def test_overlap_ratio(self) -> None:
        self.assertEqual(token_overlap_ratio(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(token_overlap_ratio(["a", "b"], ["c", "d"]), 0.0)


if __name__ == "__main__":
    unittest.main()
