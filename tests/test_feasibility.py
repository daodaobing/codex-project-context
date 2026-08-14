"""Phase 1.5 feasibility suite invariants and harness smoke test."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SUITE_PATH = ROOT / "benchmarks" / "feasibility" / "tasks.json"


class FeasibilitySuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
        self.tasks = self.suite["tasks"]

    def test_suite_is_frozen_and_classified(self) -> None:
        self.assertTrue(self.suite["frozen"])
        classes = {t["task_class"] for t in self.tasks}
        self.assertEqual(classes, {"project_knowledge", "known_hard", "simple"})
        self.assertGreaterEqual(len(self.tasks), 20)
        self.assertLessEqual(len(self.tasks), 40)

    def test_context_need_gate_field_present(self) -> None:
        for task in self.tasks:
            self.assertIsInstance(task["project_context_expected"], bool)
        expected_true = [t for t in self.tasks if t["project_context_expected"]]
        expected_false = [t for t in self.tasks if not t["project_context_expected"]]
        self.assertGreaterEqual(len(expected_true), 12)
        self.assertGreaterEqual(len(expected_false), 5)

    def test_ground_truth_backed_tasks_have_required_docs(self) -> None:
        for task in self.tasks:
            if task["task_class"] in ("project_knowledge", "known_hard"):
                self.assertTrue(task["required_docs"], task["task_id"])
                self.assertEqual(task["oracle_type"], "required_docs")

    def test_simple_tasks_have_verified_answer_file(self) -> None:
        for task in self.tasks:
            if task["task_class"] != "simple":
                continue
            self.assertEqual(task["oracle_type"], "single_file")
            path = ROOT / "benchmarks" / "workspaces" / task["repository"] / task["answer_file"]
            self.assertTrue(path.is_file(), f"{task['task_id']}: {path}")


class FeasibilityRunnerTests(unittest.TestCase):
    def test_runner_produces_report(self) -> None:
        from benchmarks.feasibility import runner

        with tempfile.TemporaryDirectory() as temp:
            report = runner.run(Path(temp) / "report.json")
            self.assertEqual(report["task_count"], len(report["per_task"]))
            self.assertIn("selection", report)
            self.assertIn("usage", report)
            self.assertIsInstance(report["selection"]["selection_recall"], float)


if __name__ == "__main__":
    unittest.main()
