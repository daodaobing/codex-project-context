"""Portable end-to-end smoke test for the Context MCP Server.

Run from the repository root with the package virtualenv:
    python tests/test_server.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def _text(result) -> str:
    return "".join(part.text for part in result.content if hasattr(part, "text"))


def _git_fixture(target: Path) -> Path:
    shutil.copytree(FIXTURES / "v2-git-demo", target)
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "add", "-A"], cwd=target, check=True)
    subprocess.run(
        ["git", "-c", "user.name=context-test", "-c", "user.email=context@test.invalid", "commit", "-q", "-m", "init"],
        cwd=target,
        check=True,
    )
    db = target / "src" / "db.py"
    db.write_text(db.read_text(encoding="utf-8") + "\n# changed by smoke test\n", encoding="utf-8")
    return target


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="context-mcp-test-") as temp_name:
        temp = Path(temp_name)
        empty = temp / "empty-project"
        empty.mkdir()
        (empty / "README.md").write_text("# Portable Demo\n\nA small test project.\n", encoding="utf-8")
        (empty / "package.json").write_text('{"name":"portable-demo","scripts":{"test":"echo ok"}}', encoding="utf-8")
        git_demo = _git_fixture(temp / "git-demo")

        params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(ROOT))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [tool.name for tool in (await session.list_tools()).tools]
                required = {
                    "get_project_context", "get_context_pack", "scan_project", "list_projects",
                    "initialize_project_context", "suggest_projects", "analyze_changes",
                    "get_context_candidates",
                }
                missing = required.difference(names)
                assert not missing, f"missing tools: {sorted(missing)}"

                preview = json.loads(_text(await session.call_tool(
                    "initialize_project_context", {"project_path": str(empty), "dry_run": True}
                )))
                assert preview["dry_run"] is True
                assert preview["files"]

                created = json.loads(_text(await session.call_tool(
                    "initialize_project_context", {"project_path": str(empty), "dry_run": False}
                )))
                assert created["created"]

                context = json.loads(_text(await session.call_tool(
                    "get_project_context", {"project_path": str(git_demo), "task": "debug database failure"}
                )))
                assert context["relevant_files"]
                assert context["matched_topics"]

                cand = json.loads(_text(await session.call_tool(
                    "get_context_candidates", {"project_path": str(git_demo), "task": "debug database failure"}
                )))
                assert isinstance(cand["candidates"], list)
                for item in cand["candidates"]:
                    assert "path" in item and "rank" in item and "reasons" in item
                    assert "content" not in item

                pack = json.loads(_text(await session.call_tool(
                    "get_context_pack", {"project_path": str(git_demo), "task": "debug database failure"}
                )))
                assert set(pack) == {"pack_version", "task", "project_context", "skill_context"}

                candidates = json.loads(_text(await session.call_tool(
                    "suggest_projects", {
                        "task": "debug database failure",
                        "project_paths": [str(empty), str(git_demo)],
                        "limit": 2,
                    }
                )))
                assert candidates["auto_selected"] is False
                assert candidates["candidates"]

                changes = json.loads(_text(await session.call_tool(
                    "analyze_changes", {"project_path": str(git_demo)}
                )))
                assert changes["analysis_only"] is True
                assert changes["git_changes"]

        print("CONTEXT MCP SMOKE TEST: PASS")


if __name__ == "__main__":
    asyncio.run(main())
