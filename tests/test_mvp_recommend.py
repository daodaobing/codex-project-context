"""MVP S2 测试：Skill 推荐（name/description 关键词匹配，宁缺毋滥），stdio 端到端。

用法（PowerShell）:
    $env:PYTHONIOENCODING='utf-8'
    & .\.venv\Scripts\python.exe tests\test_mvp_recommend.py
"""

from __future__ import annotations

import asyncio
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
    (skills_dir / "code-review").mkdir(parents=True, exist_ok=True)
    (skills_dir / "code-review" / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review code changes for quality and correctness.\n---\n",
        encoding="utf-8",
    )
    (skills_dir / "plain-skill").mkdir(parents=True, exist_ok=True)
    (skills_dir / "plain-skill" / "SKILL.md").write_text("# Plain\n\nno frontmatter\n", encoding="utf-8")


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ctx-recommend-test-"))
    fixture = tmp / "fixture"
    index_path = tmp / "skill-registry.json"
    _make_fixture(fixture)

    env = dict(os.environ)
    env["CONTEXT_SERVER_SKILL_ROOTS"] = str(fixture / "skills")
    env["CONTEXT_SERVER_SKILL_INDEX"] = str(index_path)
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(ROOT), env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert "recommend_skills" in names
            print("TOOLS:", names)

            await session.call_tool("refresh_skill_registry", {"force": True})

            # 1) 英文 name 匹配
            res = await session.call_tool("recommend_skills", {"task": "debug login failure"})
            out = json.loads(_text(res))
            print("T1:", [(r["name"], r["score"]) for r in out["recommended"]])
            assert any(r["name"] == "good-debug" for r in out["recommended"])
            assert all(r["reasons"] for r in out["recommended"])

            # 2) 英文 name 匹配（review）
            res = await session.call_tool("recommend_skills", {"task": "review this code"})
            out = json.loads(_text(res))
            print("T2:", [(r["name"], r["score"]) for r in out["recommended"]])
            assert any(r["name"] == "code-review" for r in out["recommended"])

            # 3) 宁缺毋滥：无关任务不强行推荐
            res = await session.call_tool("recommend_skills", {"task": "修复小人消失问题"})
            out = json.loads(_text(res))
            print("T3:", out["recommended"])
            assert out["count"] == 0

            # 4) 默认最多 5 条（用宽松任务验证上限逻辑）
            res = await session.call_tool("recommend_skills", {"task": "code debug review error failure"})
            out = json.loads(_text(res))
            print("T4 count:", out["count"])
            assert out["count"] <= 5

            # 5) V2 工具仍可用
            res = await session.call_tool("list_projects", {})
            assert "projects" in json.loads(_text(res))

    shutil.rmtree(tmp, ignore_errors=True)
    print("MVP-S2 OK")


if __name__ == "__main__":
    asyncio.run(main())
