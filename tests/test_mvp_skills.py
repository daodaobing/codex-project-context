r"""MVP S1 测试：Skill Registry（refresh_skill_registry / list_skills / get_skill），stdio 端到端。

用法（PowerShell）:
    $env:PYTHONIOENCODING='utf-8'
    & .\.venv\Scripts\python.exe tests\test_mvp_skills.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def _text(result) -> str:
    return "".join(part.text for part in result.content if hasattr(part, "text"))


def _make_fixture(root: Path) -> None:
    skills_dir = root / "skills"
    (skills_dir / "good-debug").mkdir(parents=True, exist_ok=True)
    (skills_dir / "good-debug" / "SKILL.md").write_text(
        "---\nname: good-debug\ndescription: Debug and diagnose failures, errors, exceptions.\n---\n",
        encoding="utf-8",
    )
    (skills_dir / "plain-skill").mkdir(parents=True, exist_ok=True)
    (skills_dir / "plain-skill" / "SKILL.md").write_text("# Plain\n\nno frontmatter\n", encoding="utf-8")
    (skills_dir / "old-skill.bak-en-20260723-134214").mkdir(parents=True, exist_ok=True)
    (skills_dir / "old-skill.bak-en-20260723-134214" / "SKILL.md").write_text(
        "---\nname: old-skill\ndescription: legacy copy\n---\n",
        encoding="utf-8",
    )
    (skills_dir / "officecli" / "skills" / "nested-skill").mkdir(parents=True, exist_ok=True)
    (skills_dir / "officecli" / "skills" / "nested-skill" / "SKILL.md").write_text(
        "---\nname: nested-skill\ndescription: nested example\n---\n",
        encoding="utf-8",
    )


def _hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("SKILL.md")):
        out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ctx-skill-test-"))
    fixture = tmp / "fixture"
    index_path = tmp / "skill-registry.json"
    _make_fixture(fixture)
    before = _hashes(fixture)

    env = dict(os.environ)
    env["CONTEXT_SERVER_SKILL_ROOTS"] = str(fixture / "skills")
    env["CONTEXT_SERVER_SKILL_INDEX"] = str(index_path)
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(ROOT), env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            for need in ("refresh_skill_registry", "list_skills", "get_skill", "get_project_context"):
                assert need in names, f"missing tool: {need}"
            print("TOOLS:", names)

            res = await session.call_tool("refresh_skill_registry", {"force": True})
            out = json.loads(_text(res))
            print("REFRESH:", json.dumps(out["summary"], ensure_ascii=False))
            assert out["summary"]["total"] == 4
            assert out["summary"]["deprecated"] == 1
            assert out["summary"]["general"] == 3
            assert index_path.is_file()

            res = await session.call_tool("list_skills", {})
            listed = json.loads(_text(res))
            print("LIST count:", listed["count"])
            assert listed["count"] == 4

            res = await session.call_tool("list_skills", {"category": "deprecated"})
            dep = json.loads(_text(res))
            print("LIST deprecated:", [s["name"] for s in dep["skills"]])
            assert dep["count"] == 1 and dep["skills"][0]["name"] == "old-skill"

            res = await session.call_tool("list_skills", {"category": "general"})
            gen = json.loads(_text(res))
            print("LIST general:", sorted(s["name"] for s in gen["skills"]))
            assert gen["count"] == 3

            res = await session.call_tool("get_skill", {"name": "good-debug"})
            got = json.loads(_text(res))
            print("GET:", got["name"], "| desc:", got["description"][:40], "| cat:", got["category"])
            assert Path(got["path"]).name == "SKILL.md" and "good-debug" in str(got["path"])
            assert got["category"] == "general"

            res = await session.call_tool("get_skill", {"name": "not-exist"})
            missing = json.loads(_text(res))
            print("GET missing:", missing.get("error", "")[:50])
            assert "error" in missing

            res = await session.call_tool("list_projects", {})
            v2 = json.loads(_text(res))
            print("V2 list_projects count:", len(v2.get("projects", [])))
            assert "projects" in v2

    after = _hashes(fixture)
    assert before == after, "Skill files were modified!"
    print("SKILL FILES UNCHANGED: True")

    shutil.rmtree(tmp, ignore_errors=True)
    print("MVP-S1 OK")


if __name__ == "__main__":
    asyncio.run(main())
