r"""MVP S3 测试：Context Pack 纯聚合（get_context_pack），stdio 端到端。

用法（PowerShell）:
    $env:PYTHONIOENCODING='utf-8'
    & .\.venv\Scripts\python.exe tests\test_mvp_pack.py
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

FIXTURES = ROOT / "tests" / "fixtures"
GIT_DEMO = FIXTURES / "v2-git-demo"

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def _text(result) -> str:
    return "".join(part.text for part in result.content if hasattr(part, "text"))


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ctx-pack-test-"))
    fixture = tmp / "skills"
    index_path = tmp / "skill-registry.json"
    (fixture / "good-debug").mkdir(parents=True, exist_ok=True)
    (fixture / "good-debug" / "SKILL.md").write_text(
        "---\nname: good-debug\ndescription: Debug and diagnose failures, errors, exceptions.\n---\n",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["CONTEXT_SERVER_SKILL_ROOTS"] = str(fixture)
    env["CONTEXT_SERVER_SKILL_INDEX"] = str(index_path)
    params = StdioServerParameters(command=sys.executable, args=["server.py"], cwd=str(ROOT), env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            assert "get_context_pack" in names
            print("TOOLS:", names)

            await session.call_tool("refresh_skill_registry", {"force": True})

            # 1) 结构固定：pack_version/task/project_context/skill_context，无多余键
            res = await session.call_tool("get_context_pack", {
                "project_path": str(GIT_DEMO),
                "task": "debug database failure",
            })
            pack = json.loads(_text(res))
            print("PACK keys:", sorted(pack.keys()))
            assert set(pack.keys()) == {"pack_version", "task", "project_context", "skill_context"}
            assert pack["pack_version"] == 1
            assert pack["task"] == "debug database failure"
            assert isinstance(pack["project_context"], dict)
            assert isinstance(pack["skill_context"], dict)

            # 2) project_context 来自 V2 输出（含 relevant_files 等原字段）
            pc = pack["project_context"]
            print("project:", pc.get("project"), "| relevant:", pc.get("relevant_files"))
            assert "relevant_files" in pc and "matched_topics" in pc

            # 3) skill_context 来自 recommend_skills（debug 命中 good-debug）
            sc = pack["skill_context"]
            print("skill recommended:", [r["name"] for r in sc.get("recommended", [])])
            assert any(r["name"] == "good-debug" for r in sc.get("recommended", []))

            # 4) 宁缺毋滥：无关任务 skill 为空但 pack 结构完整
            res = await session.call_tool("get_context_pack", {
                "project_path": str(GIT_DEMO),
                "task": "修复小人消失问题",
            })
            pack2 = json.loads(_text(res))
            print("pack2 skill count:", pack2["skill_context"]["count"])
            assert pack2["skill_context"]["count"] == 0
            assert set(pack2.keys()) == {"pack_version", "task", "project_context", "skill_context"}

    shutil.rmtree(tmp, ignore_errors=True)
    print("MVP-S3 OK")


if __name__ == "__main__":
    asyncio.run(main())
