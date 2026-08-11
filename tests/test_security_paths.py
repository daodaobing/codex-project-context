"""Regression tests for project-root path containment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from scanners.bootstrap import ProjectBootstrap
from scanners.project_scanner import ProjectScanner


ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, data: dict) -> None:
    (root / "context-manifest.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _scan(root: Path) -> dict:
    return ProjectScanner(SETTINGS).scan(str(root))


class SecurityPathTests(unittest.TestCase):
    def test_manifest_document_inside_project_is_indexed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-normal-") as temp:
            root = Path(temp) / "project"
            doc = root / "docs" / "auth.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("# Authentication\n\n合法项目内文档。\n", encoding="utf-8")
            _write_manifest(root, {"documents": {"architecture": ["docs/auth.md"]}})

            result = _scan(root)

            self.assertIn("docs/auth.md", [item["path"] for item in result["docs"]])

    def test_manifest_parent_escape_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-parent-") as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            outside = base / "secret.md"
            outside.write_text("PARENT_ESCAPE_SECRET", encoding="utf-8")
            _write_manifest(root, {"documents": {"architecture": ["../secret.md"]}})

            result = _scan(root)
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertNotIn("PARENT_ESCAPE_SECRET", serialized)
            self.assertNotIn("secret.md", [item["path"] for item in result["docs"]])
            self.assertNotIn("../secret.md", serialized)

    def test_manifest_absolute_escape_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-absolute-") as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            outside = base / "absolute-secret.md"
            outside.write_text("ABSOLUTE_ESCAPE_SECRET", encoding="utf-8")
            absolute = str(outside.resolve())
            windows_absolute = r"C:\Users\outside\absolute-secret.md"
            _write_manifest(
                root,
                {
                    "documents": {"architecture": [absolute, windows_absolute]},
                    "modules": {
                        "outside": {
                            "paths": [absolute, windows_absolute],
                            "decisions": [absolute, windows_absolute],
                        }
                    },
                    "knowledge_domains": {
                        "private": {
                            "paths": [absolute, windows_absolute],
                            "decisions": [absolute, windows_absolute],
                        }
                    },
                },
            )

            result = _scan(root)
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertNotIn("ABSOLUTE_ESCAPE_SECRET", serialized)
            self.assertNotIn(absolute, serialized)
            self.assertNotIn(windows_absolute, serialized)
            self.assertNotIn("absolute-secret.md", serialized)

    def test_external_directory_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-symlink-") as temp:
            base = Path(temp)
            root = base / "project"
            docs = root / "docs"
            outside = base / "outside"
            docs.mkdir(parents=True)
            outside.mkdir()
            (outside / "private.md").write_text("SYMLINK_ESCAPE_SECRET", encoding="utf-8")
            link = docs / "external"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            result = _scan(root)
            serialized = json.dumps(result, ensure_ascii=False)

            self.assertNotIn("SYMLINK_ESCAPE_SECRET", serialized)
            self.assertNotIn("private.md", serialized)

    def test_internal_directory_symlink_loop_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-loop-") as temp:
            root = Path(temp) / "project"
            docs = root / "docs"
            docs.mkdir(parents=True)
            (docs / "real.md").write_text("# Real\n", encoding="utf-8")
            try:
                (docs / "loop").symlink_to(docs, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            script = """
import json
import sys
from pathlib import Path
from scanners.project_scanner import ProjectScanner

root = Path(sys.argv[1])
settings = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
result = ProjectScanner(settings).scan(str(root))
print(json.dumps(result, ensure_ascii=False))
"""
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            completed = subprocess.run(
                [sys.executable, "-c", script, str(root), str(ROOT / "config" / "settings.json")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=10,
                check=True,
            )
            result = json.loads(completed.stdout)
            paths = [item["path"] for item in result["docs"]]

            self.assertEqual(paths.count("docs/real.md"), 1)
            self.assertNotIn("docs/loop/real.md", paths)

    def test_legal_manifest_module_and_knowledge_paths_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-legal-") as temp:
            root = Path(temp)
            (root / "src" / "auth").mkdir(parents=True)
            (root / "src" / "auth" / "login.py").write_text("# auth\n", encoding="utf-8")
            doc = root / "docs" / "contracts" / "api.md"
            doc.parent.mkdir(parents=True)
            doc.write_text("# API Contract\n", encoding="utf-8")
            _write_manifest(
                root,
                {
                    "modules": {
                        "auth": {
                            "paths": ["src/auth"],
                            "documents": ["docs/contracts/api.md"],
                        }
                    },
                    "knowledge_domains": {
                        "billing": {"documents": ["docs/contracts/api.md"]}
                    },
                },
            )

            result = _scan(root)

            self.assertIn("src/auth", [item["path"] for item in result["modules"]])
            self.assertIn("docs/contracts/api.md", [item["path"] for item in result["docs"]])
            self.assertEqual(result["knowledge_domains"][0]["documents"], ["docs/contracts/api.md"])

    def test_bootstrap_does_not_write_through_external_symlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ctx-security-bootstrap-") as temp:
            base = Path(temp)
            root = base / "project"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            try:
                (root / "docs").symlink_to(outside, target_is_directory=True)
                (root / ".codex").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            result = ProjectBootstrap(SETTINGS).create(str(root))

            self.assertNotIn("docs/architecture.md", result["created"])
            self.assertNotIn(".codex/workflow.md", result["created"])
            self.assertFalse((outside / "architecture.md").exists())
            self.assertFalse((outside / "workflow.md").exists())


if __name__ == "__main__":
    unittest.main()
