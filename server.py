#!/usr/bin/env python3
"""Codex Project Context MCP Server（全局，stdio 传输）。

职责：项目识别 + 文档索引 + 规则匹配，为 Codex 返回当前任务需要加载的上下文。
不替代 AGENTS.md / docs / Serena。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
SETTINGS_PATH = ROOT / "config" / "settings.json"
INDEX_PATH = ROOT / "index" / "project-index.json"

mcp = FastMCP("codex-project-context")
_lock = threading.Lock()


def _load_settings() -> dict:
    with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_index() -> dict:
    if not INDEX_PATH.is_file():
        return {"version": 1, "projects": {}}
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(index: dict) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INDEX_PATH)


def _key(project_path: str) -> str:
    return os.path.normcase(os.path.abspath(project_path))


def _ensure_project(project_path: str, force: bool = False) -> dict:
    from scanners.project_scanner import ProjectScanner

    key = _key(project_path)
    with _lock:
        index = _load_index()
        if not force and key in index.get("projects", {}):
            return index["projects"][key]
        scanner = ProjectScanner(_load_settings())
        entry = scanner.scan(project_path)
        index.setdefault("projects", {})[key] = entry
        _save_index(index)
        return entry


@mcp.tool()
def get_project_context(project_path: str, task: str) -> dict:
    """根据项目路径与任务返回需要加载的上下文（相关文档、摘要、模块路径）。

    Args:
        project_path: 项目根目录绝对路径（首次访问会自动扫描并建立索引）。
        task: 用户当前需求描述（中文/英文均可）。
    """
    from scanners.context_matcher import ContextMatcher

    project = _ensure_project(project_path, force=False)
    matcher = ContextMatcher(_load_settings())
    return matcher.match(project, task)


@mcp.tool()
def get_context_candidates(
    project_path: str,
    task: str,
    limit: int = 10,
) -> dict:
    """返回任务相关的候选文档（metadata-only，不返回全文）。

    候选来自最终 Selection 之前的 raw RRF 排名，用于把仓库文档缩小成一个
    较小的候选集，再由 Agent 做最终语义选择并只读取真正需要的文档。

    Args:
        project_path: 项目根目录绝对路径（首次访问会自动扫描并建立索引）。
        task: 用户当前需求描述（中文/英文均可）。
        limit: 返回候选数量，默认 10，范围 1-20。
    """
    from scanners import candidate_retriever

    started = time.perf_counter()
    project = _ensure_project(project_path, force=False)
    norm_limit = candidate_retriever.validate_limit(limit)
    if norm_limit is None:
        return {
            "project": project.get("project", ""),
            "project_path": project.get("project_path", ""),
            "error": "invalid_limit",
            "message": f"limit 必须为 1-{candidate_retriever.MAX_LIMIT} 之间的整数",
            "candidates": [],
        }

    try:
        pack = candidate_retriever.build_candidates(project, task, norm_limit)
        latency_ms = (time.perf_counter() - started) * 1000
        _record_candidate_event(
            project, task, norm_limit, pack, latency_ms, fallback=None, error_type=None
        )
        return pack
    except Exception as exc:  # fail-open: never crash the MCP server
        latency_ms = (time.perf_counter() - started) * 1000
        _record_candidate_event(
            project,
            task,
            norm_limit,
            {},
            latency_ms,
            fallback=True,
            error_type=type(exc).__name__,
        )
        return {
            "project": project.get("project", ""),
            "project_path": project.get("project_path", ""),
            "task": task,
            "error": "candidate_retrieval_failed",
            "message": "候选检索失败，可回退到 get_project_context 或直接读取项目文档",
            "candidate_count": 0,
            "candidates": [],
        }


def _record_candidate_event(
    project: dict,
    task: str,
    limit: int,
    pack: dict,
    latency_ms: float,
    *,
    fallback: bool | None,
    error_type: str | None,
) -> None:
    """Emit an opt-in local instrumentation event (default off)."""

    from scanners import instrumentation

    if not instrumentation.enabled():
        return
    candidates = pack.get("candidates", [])
    metadata_chars = sum(
        len(str(c.get("path") or ""))
        + len(str(c.get("title") or ""))
        + len(str(c.get("role") or ""))
        + len(json.dumps(c.get("reasons", []), ensure_ascii=False))
        for c in candidates
    )
    instrumentation.record_event(
        {
            "run_id": os.environ.get(instrumentation.ENV_RUN_ID, ""),
            "task_id": os.environ.get(instrumentation.ENV_TASK_ID, ""),
            "tool_name": "get_context_candidates",
            "called": True,
            "project": project.get("project", ""),
            "project_path": project.get("project_path", ""),
            "candidate_limit": limit,
            "candidate_count": len(candidates),
            "candidate_paths": [c.get("path") for c in candidates],
            "metadata_chars": metadata_chars,
            "latency_ms": round(latency_ms, 6),
            "fallback": fallback,
            "error_type": error_type,
        }
    )


@mcp.tool()
def scan_project(project_path: str, force: bool = False) -> dict:
    """扫描项目并写入/更新索引（get_project_context 首次调用也会自动扫描）。

    Args:
        project_path: 项目根目录绝对路径。
        force: 为 true 时强制重新扫描。
    """
    project = _ensure_project(project_path, force=force)
    return {
        "project": project["project"],
        "project_path": project["project_path"],
        "project_type": project.get("project_type"),
        "has_agents": project.get("has_agents", False),
        "counts": project.get("counts", {}),
        "scanned_at": project.get("scanned_at", ""),
    }


@mcp.tool()
def list_projects() -> dict:
    """列出当前已索引的项目（名称、路径、文档数）。"""
    index = _load_index()
    out = []
    for entry in index.get("projects", {}).values():
        out.append({
            "project": entry.get("project", ""),
            "project_path": entry.get("project_path", ""),
            "project_type": entry.get("project_type"),
            "has_agents": entry.get("has_agents", False),
            "counts": entry.get("counts", {}),
            "scanned_at": entry.get("scanned_at", ""),
        })
    out.sort(key=lambda x: x["project"].lower())
    return {"projects": out}


@mcp.tool()
def get_project_index(project_path: str) -> dict:
    """返回项目索引详情（调试 / 验证用）。"""
    return _ensure_project(project_path, force=False)


@mcp.tool()
def initialize_project_context(project_path: str, dry_run: bool = True) -> dict:
    """初始化项目上下文：自动创建 AGENTS.md + docs/（architecture/modules/design-system/decisions/changelog）+ .codex/workflow.md。

    基于 README / package.json / src 结构 / 配置文件生成初始摘要；不修改业务代码，已存在文件会跳过。

    Args:
        project_path: 项目根目录。
        dry_run: 默认 True 只返回预览；确认后传 dry_run=false 才实际创建。
    """
    from scanners.bootstrap import ProjectBootstrap

    bootstrap = ProjectBootstrap(_load_settings())
    if dry_run:
        return bootstrap.preview(project_path)
    return bootstrap.create(project_path)


@mcp.tool()
def suggest_projects(
    task: str,
    project_paths: list[str] | None = None,
    limit: int = 3,
) -> dict:
    """根据任务推荐候选项目（存在多个候选时不自动选择，返回候选与原因）。

    Args:
        task: 任务描述。
        project_paths: 候选项目路径列表；缺省使用已索引项目。
        limit: 最多返回候选数量。
    """
    from scanners.project_suggester import ProjectSuggester

    projects = []
    if project_paths:
        for p in project_paths:
            projects.append(_ensure_project(p))
    else:
        index = _load_index()
        projects = list(index.get("projects", {}).values())
    if not projects:
        return {
            "task": task,
            "candidates": [],
            "auto_selected": False,
            "note": "没有可用项目：请先调用 scan_project，或通过 project_paths 传入候选路径。",
        }
    suggester = ProjectSuggester(_load_settings())
    return suggester.suggest(projects, task, limit=limit)


@mcp.tool()
def analyze_changes(project_path: str) -> dict:
    """只读分析项目 git 工作区变更：变更文件 -> 影响模块 -> 关联文档 -> 更新建议。

    只做分析，不修改任何文档或代码。

    Args:
        project_path: 项目根目录。
    """
    from scanners.change_analyzer import ChangeAnalyzer

    project = _ensure_project(project_path, force=False)
    analyzer = ChangeAnalyzer(_load_settings())
    return analyzer.analyze(project, Path(project["project_path"]))


@mcp.tool()
def refresh_skill_registry(force: bool = True) -> dict:
    """重新扫描技能根目录并生成/更新 skill-registry.json（只读元数据，不移动/修改 Skill 文件）。

    Args:
        force: 为 false 且索引已存在时直接返回现有摘要。
    """
    from skills.skill_registry import scan

    return scan(force=force)


@mcp.tool()
def list_skills(category: str | None = None, limit: int = 500) -> dict:
    """列出已登记技能元数据（支持 category 过滤：general / deprecated）。

    Args:
        category: 可选过滤（general / deprecated），仅用于展示与筛选。
        limit: 最多返回条数。
    """
    from skills.skill_registry import list_skills as _list_skills

    return _list_skills(category=category, limit=limit)


@mcp.tool()
def get_skill(name: str) -> dict:
    """按名称查询单个技能元数据（路径、描述、分类）。

    Args:
        name: 技能名称。
    """
    from skills.skill_registry import get_skill as _get_skill

    return _get_skill(name)


@mcp.tool()
def recommend_skills(task: str, limit: int = 5) -> dict:
    """根据任务关键词推荐技能（name/description 匹配为主，skill_rules 扁平映射补充）。

    宁缺毋滥：无真实命中时不强行推荐；默认最多返回 5 条。

    Args:
        task: 任务描述。
        limit: 最多返回条数（不超过配置 max_recommendations）。
    """
    from skills.skill_recommender import recommend

    return recommend(task, limit=limit)


@mcp.tool()
def get_context_pack(project_path: str, task: str) -> dict:
    """聚合返回项目上下文与技能推荐（纯聚合，内部调用 get_project_context + recommend_skills）。

    Args:
        project_path: 项目根目录绝对路径。
        task: 用户任务描述。
    """
    from context.pack_builder import build

    return build(project_path, task)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
