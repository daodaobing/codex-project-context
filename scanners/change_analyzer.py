"""变更智能分析：git diff -> 影响模块 -> 关联文档 -> 建议。只分析，不修改。"""

from __future__ import annotations

import subprocess
from pathlib import Path


class ChangeAnalyzer:
    def __init__(self, settings: dict):
        self.settings = settings
        change_cfg = settings.get("change", {})
        self.max_related_docs = int(change_cfg.get("max_related_docs", 8))
        self.max_recommendations = int(change_cfg.get("max_recommendations", 6))

    def analyze(self, project: dict, root: Path) -> dict:
        changes = self._git_changes(root)
        if changes is None:
            return {
                "project": project.get("project", ""),
                "project_path": str(root),
                "error": "不是 git 仓库或 git 不可用（只读分析未执行）",
                "git_changes": [],
                "impacted_modules": [],
                "related_documents": [],
                "recommendations": [],
                "analysis_only": True,
            }

        candidates = list(project.get("modules", [])) + [
            {**kd, "kind": "knowledge"} for kd in project.get("knowledge_domains", [])
        ]
        results = []
        impacted: set[str] = set()
        for file, status in changes:
            module, reasons = self._match_module(file, candidates)
            if module:
                impacted.add(module["name"])
            results.append({
                "file": file,
                "status": status,
                "module": module.get("path") if module else None,
                "module_name": module.get("name") if module else None,
                "kind": module.get("kind", "module") if module else None,
                "reasons": reasons,
            })

        related_docs = self._related_docs(results, project)
        recommendations = self._recommendations(results, project.get("docs", []))
        return {
            "project": project.get("project", ""),
            "project_path": str(root),
            "git_changes": results,
            "impacted_modules": sorted(impacted),
            "related_documents": related_docs,
            "recommendations": recommendations,
            "analysis_only": True,
            "note": "本工具只做只读分析，不会修改任何文档或代码。",
        }

    def _git_changes(self, root: Path) -> list[tuple[str, str]] | None:
        try:
            r = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:  # noqa: BLE001
            return None
        if r.returncode != 0:
            return None
        out: list[tuple[str, str]] = []
        for line in r.stdout.splitlines():
            if len(line) < 4:
                continue
            status = (line[:2].strip() or "?")
            path = line[3:].strip()
            if " -> " in path:  # 重命名：old -> new
                path = path.split(" -> ")[-1]
            out.append((path, status))
        return out

    def _match_module(self, file: str, modules: list[dict]) -> tuple[dict | None, list[str]]:
        f = file.replace("\\", "/")
        best: dict | None = None
        best_len = -1
        reasons: list[str] = []
        for m in modules:
            mp = m.get("path", "").replace("\\", "/").rstrip("/")
            if not mp:
                continue
            if f == mp or f.startswith(mp + "/"):
                if len(mp) > best_len:
                    best = m
                    best_len = len(mp)
                    reasons = [f"路径前缀匹配 {mp}"]
            elif "/" not in mp and f.rsplit("/", 1)[-1] == mp:
                if len(mp) > best_len:
                    best = m
                    best_len = len(mp)
                    reasons = [f"文件名匹配 {mp}"]
        if best is None:
            for m in modules:
                kws = m.get("keywords") or []
                if any(kw and kw.lower() in f.lower() for kw in kws):
                    best = m
                    reasons = [f"manifest 关键词匹配 {','.join(kws)}"]
                    break
        return best, reasons

    def _related_docs(self, results: list[dict], project: dict) -> list[str]:
        docs = project.get("docs", [])
        candidates = list(project.get("modules", [])) + list(
            project.get("knowledge_domains", [])
        )
        modules_by_path = {m.get("path"): m for m in candidates if m.get("path")}
        related: set[str] = set()
        for r in results:
            m = modules_by_path.get(r.get("module"))
            if not m:
                continue
            for doc_path in (m.get("documents") or []) + (m.get("decisions") or []):
                related.add(doc_path)
            m_cats = set(m.get("categories", []))
            for d in docs:
                if set(d.get("categories", [])) & m_cats:
                    related.add(d["path"])

        order = {
            "architecture": 0, "modules": 1, "decisions": 2, "design": 3,
            "contracts": 4, "operations": 5, "changelog": 6,
        }
        by_path = {d.get("path"): d.get("role", "other") for d in docs}
        return sorted(
            related,
            key=lambda p: order.get(by_path.get(p, "other"), 9),
        )[: self.max_related_docs]

    def _recommendations(self, results: list[dict], docs: list[dict]) -> list[str]:
        roles = {d.get("role") for d in docs}
        recs: list[str] = []
        if any(r.get("module") for r in results):
            recs.append("变更涉及模块，建议检查 docs/modules.md 中的模块职责描述是否需要同步更新。")
        if "decisions" in roles:
            recs.append("若本次变更属于重大技术决策，建议在 docs/decisions.md 追加一条记录（本工具不会自动写入）。")
        if "design" in roles:
            recs.append("若变更涉及页面/UI，建议核对 docs/design-system.md 或 docs/design/ 中的规范。")
        if "architecture" in roles:
            recs.append("若变更影响整体结构，建议核对 docs/architecture.md。")
        if "contracts" in roles:
            recs.append("若变更涉及接口/契约，建议检查 docs/contracts/ 是否需要同步。")
        if "operations" in roles:
            recs.append("若变更影响部署/运维流程，建议检查 docs/operations/。")
        recs.append("如需记录行为变化，建议更新 docs/changelog.md。")
        return recs[: self.max_recommendations]
