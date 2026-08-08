"""跨项目候选：任务 -> 多个项目候选 + 原因。不自动选择。"""

from __future__ import annotations

from .context_matcher import ContextMatcher


class ProjectSuggester:
    def __init__(self, settings: dict):
        self.matcher = ContextMatcher(settings)
        self.max_candidates = int(
            settings.get("suggest", {}).get("max_candidates", 3)
        )

    def suggest(
        self, projects: list[dict], task: str, limit: int | None = None
    ) -> dict:
        limit = limit or self.max_candidates
        scored = []
        for project in projects:
            total, detail = self.matcher.score_project(project, task)
            top_docs = [d["path"] for _, d, _ in detail["docs"][:4]]
            top_mods = [m["path"] for s, m in detail["modules"][:4] if s > 0]
            top_knowledge = [
                kd["name"] for s, kd in detail["knowledge"][:3] if s > 0
            ]
            topics = [v["label"] for v in detail["rule_hits"].values()]
            scored.append({
                "project": project.get("project", ""),
                "project_path": project.get("project_path", ""),
                "project_type": project.get("project_type"),
                "score": round(total, 2),
                "matched_topics": topics,
                "matched_knowledge": top_knowledge,
                "reason": self._reason(topics, top_mods, top_docs, top_knowledge),
                "top_documents": top_docs,
                "top_modules": top_mods,
            })
        scored.sort(key=lambda x: -x["score"])
        return {
            "task": task,
            "candidates": scored[:limit],
            "auto_selected": False,
            "note": "存在多个候选时不做自动选择；请结合原因人工确认目标项目。",
        }

    @staticmethod
    def _reason(
        topics: list[str],
        top_mods: list[str],
        top_docs: list[str],
        top_knowledge: list[str],
    ) -> str:
        parts = []
        if topics:
            parts.append("命中主题：" + "、".join(topics))
        if top_mods:
            parts.append("相关模块：" + "、".join(top_mods[:3]))
        if top_knowledge:
            parts.append("命中知识域：" + "、".join(top_knowledge[:3]))
        if top_docs:
            parts.append("相关文档：" + "、".join(top_docs[:3]))
        return "；".join(parts) if parts else "已索引但未命中任务关键词"
