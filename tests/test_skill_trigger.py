"""Phase 1.7 skill-trigger tests.

Static and deterministic scenario tests for the project-context skill's
candidate-retrieval trigger model. These validate the written spec and the
skill copy sync; they do NOT measure real Codex adoption (live adoption is a
separate live-run measurement).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / "skill" / "project-context" / "SKILL.md"
INSTALLED_PATH = Path.home() / ".codex" / "skills" / "project-context" / "SKILL.md"
DEPLOYED_PATH = Path.home() / ".codex" / "context-server" / "skill" / "project-context" / "SKILL.md"

FORBIDDEN_TERMS = [
    "click",
    "jest",
    "axios",
    "45-task",
    "phase 1.6",
    "benchmark",
    "recall@10",
    "blind holdout",
    "required_docs",
    "ground truth",
]

TRIGGER_MARKERS = [
    r"unfamiliar",
    r"architecture",
    r"convention",
    r"multiple docs",
    r"design decisions",
    r"vague",
    r"existing design",
    r"project-specific knowledge",
]

ANTI_TRIGGER_MARKERS = [
    r"exact file path",
    r"function name",
    r"localized",
    r"function-level bug",
    r"already known",
    r"simple command",
    r"usage question",
]


def _text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def classify(scenario: str) -> str:
    """Deterministic mirror of the decision model written in SKILL.md.

    Returns "trigger", "no_trigger", or "ambiguous". This is a spec-mirror
    classifier used to pin the documented markers, not a claim about how a
    live agent behaves.
    """

    text = scenario.casefold()
    trigger = any(re.search(m, text) for m in TRIGGER_MARKERS)
    anti = any(re.search(m, text) for m in ANTI_TRIGGER_MARKERS)
    if anti:
        return "no_trigger"
    if trigger:
        return "trigger"
    return "ambiguous"


SCENARIOS = [
    ("add a plugin registration that respects the project architecture and naming conventions; several design documents describe them", "trigger"),
    ("a lint rule is inactive; find which configuration keys and per-file exceptions control it across multiple docs", "trigger"),
    ("unfamiliar codebase; explain the build configuration conventions before changing the release pipeline", "trigger"),
    ("before changing the auth flow, recover the design decisions about sessions from the project docs", "trigger"),
    ("in src/parser/lexer.py, change the token limit constant from 100 to 200; the exact file path is known", "no_trigger"),
    ("the function compute_total in utils.py has a clear function-level bug; fix it in place", "no_trigger"),
    ("how do I list files in a directory with PowerShell? a simple command question", "no_trigger"),
    ("rename the variable x to y in data.py line 40; the exact file path and line are already known", "no_trigger"),
]


class SkillCopySyncTests(unittest.TestCase):
    def test_source_installed_deployed_content_equal(self) -> None:
        source = SKILL_PATH.read_text(encoding="utf-8")
        for label, path in (("installed", INSTALLED_PATH), ("deployed", DEPLOYED_PATH)):
            if not path.is_file():
                self.skipTest(f"{label} copy not present on this machine: {path}")
            self.assertEqual(source, path.read_text(encoding="utf-8"), label)

    def test_frontmatter_mentions_candidate_entry(self) -> None:
        text = _text()
        self.assertIn("Prefer get_context_candidates", text)
        self.assertIn('name: project-context', text)


class SkillStructureTests(unittest.TestCase):
    def test_required_sections_present(self) -> None:
        text = _text()
        for section in (
            "When to use this skill",
            "Two-stage workflow",
            "Stage 1 — Candidate discovery",
            "Stage 2 — Selective reading",
            "Decision model",
            "Full context (legacy path)",
            "Guardrails",
        ):
            self.assertIn(section, text, section)

    def test_legacy_tools_retained_but_repositioned(self) -> None:
        text = _text()
        self.assertIn("get_project_context", text)
        self.assertIn("get_context_pack", text)
        self.assertIn("no longer the default first step", text)

    def test_no_benchmark_contamination(self) -> None:
        text = _text().casefold()
        for term in FORBIDDEN_TERMS:
            self.assertNotIn(term, text, term)

    def test_no_absolute_local_paths(self) -> None:
        text = _text()
        self.assertNotRegex(text, r"[A-Za-z]:[\\/]", "windows absolute path")
        self.assertNotRegex(text, r"/Users/|/home/", "unix home path")

    def test_no_unconditional_mandate(self) -> None:
        text = _text()
        self.assertNotIn("Always call get_context_candidates", text)
        self.assertNotIn("always call `get_context_candidates`", text.casefold())
        self.assertNotIn("must read the top", text.casefold())
        self.assertNotIn("top-10", text.casefold())
        self.assertNotIn("top 10", text.casefold())

    def test_source_truth_guardrail_present(self) -> None:
        text = _text()
        self.assertIn("Source and tests remain the final truth", text)


class TriggerScenarioTests(unittest.TestCase):
    def test_scenario_classification(self) -> None:
        for scenario, expected in SCENARIOS:
            with self.subTest(scenario=scenario):
                self.assertEqual(classify(scenario), expected)

    def test_trigger_markers_exist_in_skill(self) -> None:
        text = _text().casefold()
        # Every trigger marker must be backed by wording present in the skill.
        for marker in TRIGGER_MARKERS:
            self.assertRegex(text, marker, marker)

    def test_anti_trigger_markers_exist_in_skill(self) -> None:
        text = _text().casefold()
        for marker in ANTI_TRIGGER_MARKERS:
            self.assertRegex(text, marker, marker)


class RegistryFreshnessTests(unittest.TestCase):
    def test_deployed_registry_describes_candidate_entry(self) -> None:
        import json

        registry = Path.home() / ".codex" / "context-server" / "index" / "skill-registry.json"
        if not registry.is_file():
            self.skipTest("deployed skill registry not built on this machine")
        data = json.loads(registry.read_text(encoding="utf-8"))
        entries = [s for s in data.get("skills", []) if s.get("name") == "project-context"]
        if not entries:
            self.skipTest("project-context not yet indexed in deployed registry")
        description = (entries[0].get("description") or "").casefold()
        self.assertIn("get_context_candidates", description)


if __name__ == "__main__":
    unittest.main()
