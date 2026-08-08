"""上下文匹配器：任务 -> 主题 -> 文档/模块评分。纯规则，无 LLM。"""

from __future__ import annotations


class ContextMatcher:
    def __init__(self, settings: dict):
        self.settings = settings
        self.rules = settings.get("rules", {})
        idx = settings.get("index", {})
        self.top_files = idx.get("top_relevant_files", 6)
        self.top_modules = idx.get("top_recommended_modules", 4)
        self.max_summary_chars = idx.get("max_context_summary_chars", 1400)

    def match(self, project: dict, task: str) -> dict:
        result = self.analyze(project, task)
        # 知识域命中 -> 其关联文档加权进入候选
        docs_scored = list(result["docs"])
        boosted: set[tuple[str, str]] = set()

        def boost_docs(items, weight: float) -> None:
            for score, item in items:
                if score <= 0:
                    continue
                for rel in item.get("documents", []):
                    key = (item.get("name", ""), rel)
                    if key in boosted:
                        continue
                    boosted.add(key)
                    for i, (s, d, reasons) in enumerate(docs_scored):
                        if d.get("path") == rel:
                            docs_scored[i] = (
                                s + score * weight,
                                d,
                                reasons + [f"manifest:{item.get('name')}"],
                            )
                            break

        boost_docs(result["modules"][: self.top_modules], 1.5)
        boost_docs(result["knowledge"][:4], 2.0)
        docs_scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        top = docs_scored[: self.top_files]
        relevant_files = [d["path"] for _, d, _ in top]

        recommended_read = [
            m["path"] for s, m in result["modules"][: self.top_modules] if s > 0
        ]
        matched_knowledge = [
            kd["name"] for s, kd in result["knowledge"][:4] if s > 0
        ]
        # 知识域文档若仍未进入列表（如文档未被索引），追加兜底
        for s, kd in result["knowledge"][:4]:
            if s <= 0:
                continue
            for rel in kd.get("documents", []):
                if rel not in relevant_files:
                    relevant_files.append(rel)

        return {
            "project": project.get("project", ""),
            "project_path": project.get("project_path", ""),
            "project_type": project.get("project_type"),
            "task": task,
            "relevant_files": relevant_files,
            "context_summary": self._build_summary(project, top),
            "recommended_read": recommended_read,
            "matched_topics": [v["label"] for v in result["rule_hits"].values()],
            "matched_knowledge": matched_knowledge,
            "indexed_at": project.get("scanned_at", ""),
        }

    def analyze(self, project: dict, task: str) -> dict:
        """对单个项目做完整评分，返回规则命中、文档与模块评分明细。"""
        task_lower = (task or "").lower().strip()
        if not task_lower:
            raise ValueError("task 不能为空")

        rule_hits = self._collect_rule_hits(task_lower)

        scored_docs = []
        for doc in project.get("docs", []):
            score, reasons = self._score_doc(doc, rule_hits)
            scored_docs.append((score, doc, reasons))

        for entry in project.get("entry_files", []):
            doc = {**entry, "path": entry["path"]}
            score, reasons = self._score_doc(doc, rule_hits)
            score += 1.2  # 入口文件基础权重
            scored_docs.append((score, doc, reasons))

        scored_docs.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        scored_modules = self._score_modules(
            project.get("modules", []), rule_hits, task_lower
        )
        scored_knowledge = self._score_knowledge(
            project.get("knowledge_domains", []), rule_hits, task_lower
        )
        return {
            "rule_hits": rule_hits,
            "docs": scored_docs,
            "modules": scored_modules,
            "knowledge": scored_knowledge,
        }

    def score_project(self, project: dict, task: str) -> tuple[float, dict]:
        """项目级聚合分，供跨项目候选排序使用。"""
        result = self.analyze(project, task)
        doc_score = sum(s for s, _, _ in result["docs"])
        mod_score = sum(s for s, _ in result["modules"])
        knowledge_score = sum(s for s, _ in result["knowledge"])
        total = (
            doc_score
            + mod_score * 1.5
            + knowledge_score * 2.0
            + len(result["rule_hits"]) * 2.0
        )
        return total, result

    def _collect_rule_hits(self, task_lower: str) -> dict:
        rule_hits = {}
        for name, rule in self.rules.items():
            hits = [kw for kw in rule.get("keywords", []) if kw in task_lower]
            if hits:
                rule_hits[name] = {"label": rule.get("label", name), "hits": hits}
        return rule_hits

    def _score_doc(self, doc: dict, rule_hits: dict) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        doc_cats = set(doc.get("categories", []))
        search_text = self._search_text(doc).lower()
        for name, info in rule_hits.items():
            rule = self.rules[name]
            rule_cats = set(rule.get("categories", []))
            if rule_cats & doc_cats:
                score += len(info["hits"]) * 2.5
                reasons.append(f"{name}:cat")
            topical = sum(1 for kw in rule["keywords"] if kw in search_text)
            score += min(topical, 8) * 0.5
            if topical:
                reasons.append(f"{name}:text{topical}")
        if rule_hits and ("plans" in doc_cats or any(c in ("plans", "changelog") for c in doc_cats)):
            score += 0.5
        return score, reasons

    def _score_modules(
        self, modules: list[dict], rule_hits: dict, task_lower: str
    ) -> list[tuple[float, dict]]:
        scored = []
        for mod in modules:
            score = 0.0
            text = (
                mod.get("path", "") + "\n" + mod.get("name", "") + "\n" + mod.get("description", "")
            ).lower()
            mod_cats = set(mod.get("categories", []))
            # manifest 关键词直接命中任务
            keyword_hits = [
                kw for kw in (mod.get("keywords") or []) if kw in task_lower
            ]
            score += len(keyword_hits) * 3.0
            # 模块名 token 直接命中任务（如 erp_query -> "erp"）
            for token in (mod.get("name") or "").lower().replace("_", " ").split():
                if len(token) >= 2 and token in task_lower:
                    score += 2.0
            for name, info in rule_hits.items():
                rule = self.rules[name]
                if set(rule.get("categories", [])) & mod_cats:
                    score += len(info["hits"]) * 2.0
                topical = sum(1 for kw in rule["keywords"] if kw in text)
                score += min(topical, 8) * 0.5
            scored.append((score, mod))
        scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        return scored

    def _score_knowledge(
        self, domains: list[dict], rule_hits: dict, task_lower: str
    ) -> list[tuple[float, dict]]:
        """知识域评分：manifest 声明的 keywords 直接命中任务文本（无代码也可匹配）。"""
        scored = []
        for d in domains:
            score = 0.0
            keywords = d.get("keywords") or []
            hits = [kw for kw in keywords if kw in task_lower]
            score += len(hits) * 3.0
            d_cats = set(d.get("categories", []))
            domain_text = (d.get("name", "") + " " + " ".join(keywords)).lower()
            for name, info in rule_hits.items():
                rule = self.rules[name]
                if set(rule.get("categories", [])) & d_cats:
                    score += len(info["hits"]) * 2.0
                topical = sum(1 for kw in rule["keywords"] if kw in domain_text)
                score += min(topical, 6) * 0.5
            scored.append((score, d))
        scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
        return scored

    def _search_text(self, doc: dict) -> str:
        return (
            doc.get("title", "")
            + "\n"
            + " ".join(doc.get("headings", []))
            + "\n"
            + doc.get("summary", "")
        )

    def _build_summary(self, project: dict, top: list[tuple[float, dict, list[str]]]) -> str:
        parts: list[str] = []
        for entry in project.get("entry_files", []):
            if entry.get("summary"):
                parts.append(entry["summary"][:400])
                break
        for _, doc, _ in top[:3]:
            if doc.get("summary"):
                parts.append(doc["summary"][:300])
        summary = "\n\n".join(p for p in parts if p)
        return summary[: self.max_summary_chars]
