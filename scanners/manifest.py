"""context-manifest.yaml 解析：让项目主动声明模块/文档与领域的映射。

存在时扫描器优先使用；不存在时继续自动扫描。
"""

from __future__ import annotations

from pathlib import Path

import yaml

MANIFEST_FILENAME = "context-manifest.yaml"


def load_manifest(root: Path) -> dict | None:
    p = root / MANIFEST_FILENAME
    if not p.is_file():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception as exc:  # noqa: BLE001 - 解析失败降级为自动扫描
        return {
            "path": MANIFEST_FILENAME,
            "error": f"YAML 解析失败: {exc}",
            "project": {},
            "modules": {},
            "knowledge_domains": {},
            "documents": {},
        }
    if not isinstance(raw, dict):
        return {
            "path": MANIFEST_FILENAME,
            "error": "manifest 顶层必须是映射",
            "project": {},
            "modules": {},
            "knowledge_domains": {},
            "documents": {},
        }

    def parse_group(group) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for name, info in (group or {}).items():
            if not isinstance(info, dict):
                continue
            out[str(name)] = {
                "name": str(name),
                "paths": [str(x) for x in (info.get("paths") or [])],
                "documents": [str(x) for x in (info.get("documents") or [])],
                "decisions": [str(x) for x in (info.get("decisions") or [])],
                "keywords": [str(x) for x in (info.get("keywords") or [])],
            }
        return out

    modules = parse_group(raw.get("modules"))
    knowledge_domains = parse_group(raw.get("knowledge_domains"))

    project_raw = raw.get("project")
    project: dict = {}
    if isinstance(project_raw, str):
        project["name"] = project_raw
    elif isinstance(project_raw, dict):
        if isinstance(project_raw.get("name"), str):
            project["name"] = project_raw["name"]
        if isinstance(project_raw.get("type"), str):
            project["type"] = project_raw["type"]

    documents: dict[str, list[str]] = {}
    for cat, paths in (raw.get("documents") or {}).items():
        if isinstance(paths, list):
            documents[str(cat)] = [str(x) for x in paths]

    return {
        "path": MANIFEST_FILENAME,
        "project": project,
        "modules": modules,
        "knowledge_domains": knowledge_domains,
        "documents": documents,
        "error": None,
    }


def declared_doc_paths(manifest: dict | None) -> list[str]:
    """manifest 中声明的全部文档相对路径（documents 分类 + 模块/知识域关联文档）。"""
    if not manifest:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(paths) -> None:
        for rel in paths or []:
            rel = str(rel).replace("\\", "/")
            if rel not in seen:
                seen.add(rel)
                out.append(rel)

    for paths in (manifest.get("documents") or {}).values():
        add(paths)
    for group in ("modules", "knowledge_domains"):
        for item in (manifest.get(group) or {}).values():
            add(item.get("documents"))
            add(item.get("decisions"))
    return out
