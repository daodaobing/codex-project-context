"""上下文匹配器：以仓库自身文档内容为主，规则信号为辅。纯规则，无 LLM。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from scanners.document_family import (
    LOCALE_SEGMENTS,
    alias_normalize,
    alias_similar,
    canonical_family,
    document_role,
    locale_of,
    role_from_task,
    roles_from_task,
    token_overlap_ratio,
)


_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_LOCALE_SET = set(LOCALE_SEGMENTS)

# 只移除语法性/任务模板词，保留领域名词（如 request、configuration、server）。
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "before",
        "by",
        "can",
        "could",
        "for",
        "from",
        "how",
        "in",
        "into",
        "is",
        "it",
        "must",
        "need",
        "new",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "these",
        "this",
        "those",
        "to",
        "use",
        "using",
        "while",
        "with",
        "without",
        "after",
        "add",
        "change",
        "debug",
        "fix",
        "preserve",
        "support",
        "understand",
        "update",
        "existing",
        "current",
    }
)


class ContextMatcher:
    def __init__(self, settings: dict):
        self.settings = settings
        self.rules = settings.get("rules", {})
        idx = settings.get("index", {})
        # top_relevant_files remains a compatibility setting. V0.2 uses it as
        # the old floor and keeps a simple, explicit hard cap for dynamic output.
        self.top_files = int(idx.get("top_relevant_files", 6))
        self.max_files = int(idx.get("max_relevant_files", max(self.top_files, 8)))
        self.min_relevant_score = float(idx.get("min_relevant_score", 1.0))
        self.relative_score_floor = float(idx.get("relative_score_floor", 0.35))
        self.fallback_files = max(1, int(idx.get("fallback_relevant_files", 2)))
        self.diversity_penalty = float(idx.get("diversity_penalty", 0.35))
        self.family_hard_cap = int(idx.get("family_hard_cap", 8))
        self.role_boost = float(idx.get("role_boost", 1.0))
        self.soft_diversity_floor = float(idx.get("soft_diversity_floor", 0.6))
        self.role_coverage_floor = float(idx.get("role_coverage_floor", 2.0))
        self.top_modules = idx.get("top_recommended_modules", 4)
        self.max_summary_chars = idx.get("max_context_summary_chars", 1400)
        self.entry_bonus = float(idx.get("entry_file_bonus", 0.35))

    def match(self, project: dict, task: str, *, trace: bool = False) -> dict:
        """Return the routed context for a task.

        ``trace=True`` additionally returns a ``trace`` list describing every
        candidate document's score decomposition and selection decision.  The
        public MCP API never enables trace, so the response shape is unchanged
        for normal callers.
        """

        result = self.analyze(project, task)
        # 知识域/模块仍可补充 manifest 声明文档，但权重低于内容匹配。
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
                    for i, (old_score, doc, reasons) in enumerate(docs_scored):
                        if doc.get("path") == rel:
                            docs_scored[i] = (
                                old_score + score * weight,
                                doc,
                                reasons + [f"manifest:{item.get('name')}"],
                            )
                            break

        boost_docs(result["modules"][: self.top_modules], 0.75)
        boost_docs(result["knowledge"][:4], 1.0)
        docs_scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        top, selection_notes, trace_rows = self._select_documents(
            docs_scored, task, trace=trace
        )
        relevant_files = [d["path"] for _, d, _ in top]

        recommended_read = [
            m["path"] for s, m in result["modules"][: self.top_modules] if s > 0
        ]
        matched_knowledge = [
            kd["name"] for s, kd in result["knowledge"][:4] if s > 0
        ]
        # 知识域文档若仍未进入列表（如文档未被索引），追加兜底。
        for s, kd in result["knowledge"][:4]:
            if s <= 0:
                continue
            for rel in kd.get("documents", []):
                if rel not in relevant_files:
                    relevant_files.append(rel)

        out = {
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
            "selection_notes": selection_notes,
        }
        if trace:
            out["trace"] = trace_rows
        return out

    def trace_match(self, project: dict, task: str) -> dict:
        """Return the routed context plus a full per-document score trace."""

        return self.match(project, task, trace=True)

    def analyze(self, project: dict, task: str) -> dict:
        """分析任务与文档/模块的匹配，保留 score/reasons 供诊断使用。"""

        task_lower = (task or "").lower().strip()
        if not task_lower:
            raise ValueError("task 不能为空")

        rule_hits = self._collect_rule_hits(task_lower)
        task_tokens = self._tokenize(task_lower, drop_stopwords=True)

        scored_docs = []
        for doc in project.get("docs", []):
            score, reasons = self._score_doc(doc, rule_hits, task_tokens)
            scored_docs.append((score, doc, reasons))

        for entry in project.get("entry_files", []):
            doc = {**entry, "path": entry["path"]}
            score, reasons = self._score_doc(doc, rule_hits, task_tokens)
            score += self.entry_bonus
            reasons.append(f"entry fallback bonus: {self.entry_bonus:g}")
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

    def _select_documents(self, docs_scored, task: str, *, trace: bool = False):
        """V0.4 selection: hard dedup only for certain duplicates, then soft
        diversity penalty, role coverage, dynamic threshold, and max cap.

        Hard dedup is restricted to documents whose canonical family is
        identical AND whose locale-free path is identical (i.e. locale
        variants of the same document).  Parent/child documents such as
        ``docs/formatter.md`` and ``docs/formatter/black.md`` are never
        hard-deduped because they are different documents.  Near-duplicate
        topics only receive a bounded soft penalty.
        """

        notes: list[str] = []
        trace_rows: list[dict] = []
        if not docs_scored:
            return [], notes, trace_rows

        task_roles = roles_from_task(task) if task else []
        task_role = task_roles[0] if task_roles else ""
        positive = [item for item in docs_scored if item[0] >= self.min_relevant_score]
        if positive:
            best_score = positive[0][0]
            # V0.4: the relative-to-best floor saturates for very high best
            # scores so a single outlier document cannot push out strong but
            # slightly lower candidates (e.g. docs/formatter.md vs
            # docs/formatter/black.md).  The absolute floor still applies.
            relative_floor = self.relative_score_floor
            if best_score > 10:
                relative_floor = max(0.2, self.relative_score_floor * 10 / best_score)
            threshold = max(
                self.min_relevant_score,
                best_score * relative_floor,
            )
            # V0.4: a document whose generic role matches the task intent may
            # enter the candidate pool at a higher absolute floor even when the
            # relative-to-best threshold is higher.  This lets faq.md /
            # api.md / configuration.md reach selection for intent words such
            # as "why/fail/error" or "api/configure" without lowering the
            # threshold for everyone.
            candidates = []
            for item in docs_scored:
                score, doc, _ = item
                if score >= threshold:
                    candidates.append(item)
                    continue
                if task_roles and score >= self.min_relevant_score:
                    doc_role = document_role(
                        str(doc.get("path") or ""),
                        doc.get("title", ""),
                        doc.get("headings"),
                    )
                    if doc_role in task_roles:
                        candidates.append(item)
            # V0.4.1: a child document of an accepted candidate (same canonical
            # family prefix, e.g. docs/formatter.md vs docs/formatter/black.md)
            # may enter the pool at the absolute floor.  This is a generic
            # section/subpage structure signal, not a repository-specific rule.
            if candidates:
                accepted_families = {
                    canonical_family(str(doc.get("path") or ""))
                    for _, doc, _ in candidates
                }
                accepted_paths = {id(item) for item in candidates}
                for item in docs_scored:
                    if id(item) in accepted_paths or item[0] < self.min_relevant_score:
                        continue
                    family = canonical_family(str(item[1].get("path") or ""))
                    if any(
                        family.startswith(f"{accepted}/") or accepted.startswith(f"{family}/")
                        for accepted in accepted_families
                    ):
                        candidates.append(item)
            candidates.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        else:
            candidates = []

        if not candidates:
            # 没有明显命中时保留少量入口/最高分文档，避免返回全部文档。
            fallback = docs_scored[: min(self.fallback_files, self.max_files)]
            if trace:
                for score, doc, reasons in fallback:
                    trace_rows.append(
                        self._trace_row(
                            doc,
                            score,
                            score,
                            docs_scored.index((score, doc, reasons)),
                            None,
                            True,
                            task_role,
                            threshold,
                            reasons,
                            hard_duplicate=False,
                            hard_duplicate_of=None,
                            penalty=0.0,
                        )
                    )
            return fallback, notes, trace_rows

        # Pre-compute canonical family and locale-free path for every candidate.
        enriched: list[dict] = []
        for item in candidates:
            score, doc, reasons = item
            path = str(doc.get("path") or "")
            locale = locale_of(path)
            family = canonical_family(path)
            # Locale-free path: remove locale segments from the path.
            parts = [p for p in path.replace("\\", "/").split("/") if p and p not in _LOCALE_SET]
            locale_free = "/".join(parts)
            enriched.append(
                {
                    "score": score,
                    "doc": doc,
                    "reasons": reasons,
                    "path": path,
                    "locale": locale,
                    "family": family,
                    "locale_free": locale_free,
                    "rank_before": len(trace_rows) + 1,
                }
            )

        # 1. Remove exact/locale duplicates (hard dedup).  Only documents
        # whose locale-free path is identical are considered duplicates.
        best_by_locale_free: dict[str, dict] = {}
        dropped: list[tuple[dict, dict]] = []  # (kept, dropped)
        for row in enriched:
            key = row["locale_free"]
            existing = best_by_locale_free.get(key)
            if existing is None or row["score"] > existing["score"]:
                if existing is not None:
                    dropped.append((row, existing))
                best_by_locale_free[key] = row
            else:
                dropped.append((existing, row))
        deduped = list(best_by_locale_free.values())
        for kept, drop in dropped:
            notes.append(
                f"hard_dedup:{drop['path']}->{kept['path']}"
            )
            if trace:
                trace_rows.append(
                    self._trace_row(
                        drop["doc"],
                        drop["score"],
                        drop["score"],
                        drop["rank_before"],
                        None,
                        False,
                        task_role,
                        threshold,
                        list(drop["reasons"]) + [f"hard_duplicate_of:{kept['path']}"],
                        hard_duplicate=True,
                        hard_duplicate_of=kept["path"],
                        penalty=0.0,
                    )
                )

        # 2. Sort by relevance (raw score).
        deduped.sort(key=lambda r: (-r["score"], r["path"]))

        # 3. Iterative selection with soft diversity penalty.
        selected: list[tuple[float, dict, list[str]]] = []
        selected_roles: set[str] = set()
        for row in deduped:
            if len(selected) >= self.max_files:
                notes.append(f"max_files reached: {self.max_files}")
                break

            score = row["score"]
            doc = row["doc"]
            reasons = list(row["reasons"])
            path = row["path"]
            role = document_role(path, doc.get("title", ""), doc.get("headings"))

            # Role boost: a candidate whose role matches the task intent gets
            # a small, bounded boost (never enough to overturn a strong hit).
            role_hit = role in task_roles
            if role_hit:
                score += self.role_boost
                reasons.append(f"role boost: {role}")

            # Soft diversity penalty against already-selected documents.
            penalty = 0.0
            for _, selected_doc, _ in selected:
                candidate_tokens = self._document_fields(doc)["all"]
                selected_tokens = self._document_fields(selected_doc)["all"]
                overlap = token_overlap_ratio(candidate_tokens, selected_tokens)
                if overlap >= self.soft_diversity_floor:
                    penalty = max(penalty, self.diversity_penalty * overlap)
            final_score = score - penalty

            # Role coverage: if this candidate introduces a new role and is
            # above the absolute floor, keep it even if slightly below the
            # relative threshold.  The candidate pool already filtered weak
            # role matches with the role-coverage floor.
            coverage_exempt = (
                role not in selected_roles
                and role != "other"
                and final_score >= self.min_relevant_score
            )
            if coverage_exempt:
                threshold_used = self.min_relevant_score
            else:
                threshold_used = threshold

            if final_score < threshold_used and not coverage_exempt:
                if trace:
                    trace_rows.append(
                        self._trace_row(
                            doc, row["score"], final_score, row["rank_before"],
                            None, False, task_role, threshold_used, reasons,
                            hard_duplicate=False, hard_duplicate_of=None,
                            penalty=penalty,
                        )
                    )
                continue

            selected.append((final_score, doc, reasons))
            selected_roles.add(role)
            if penalty:
                reasons.append(f"diversity_penalty={penalty:g}")
                notes.append(f"diversity_penalty={penalty:g}:{path}")
            if role_hit:
                notes.append(f"role_boost={self.role_boost:g}:{path}")
            notes.append(f"canonical_family={row['family'] or '(root)'}")
            if trace:
                trace_rows.append(
                    self._trace_row(
                        doc, row["score"], final_score, row["rank_before"],
                        len(selected) - 1, True, task_role, threshold_used, reasons,
                        hard_duplicate=False, hard_duplicate_of=None,
                        penalty=penalty,
                    )
                )

        # Re-sort by effective score to honor the diversity-adjusted ranking.
        selected.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        return selected[: self.max_files], notes, trace_rows

    def _trace_row(
        self,
        doc: dict,
        raw_score: float,
        final_score: float,
        rank_before: int,
        rank_after: int | None,
        selected: bool,
        task_role: str,
        threshold: float,
        reasons: list[str],
        *,
        hard_duplicate: bool,
        hard_duplicate_of: str | None,
        penalty: float,
    ) -> dict:
        path = str(doc.get("path") or "")
        return {
            "path": path,
            "raw_score": round(raw_score, 4),
            "final_score": round(final_score, 4),
            "rank_before": rank_before,
            "rank_after": rank_after,
            "selected": selected,
            "canonical_family": canonical_family(path),
            "locale": locale_of(path),
            "role": document_role(path, doc.get("title", ""), doc.get("headings")),
            "task_role": task_role,
            "hard_duplicate": hard_duplicate,
            "hard_duplicate_of": hard_duplicate_of,
            "similarity_penalty": round(penalty, 4),
            "threshold": round(threshold, 4),
            "reasons": list(reasons),
        }

    def _collect_rule_hits(self, task_lower: str) -> dict:
        rule_hits = {}
        for name, rule in self.rules.items():
            hits = [kw for kw in rule.get("keywords", []) if kw in task_lower]
            if hits:
                rule_hits[name] = {"label": rule.get("label", name), "hits": hits}
        return rule_hits

    def _score_doc(
        self,
        doc: dict,
        rule_hits: dict,
        task_tokens: list[str],
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        doc_cats = set(doc.get("categories", []))
        fields = self._document_fields(doc)
        task_set = set(task_tokens)

        # V0.4: generic document role signal.  A task intent such as
        # "why does it fail" maps to the troubleshooting role and gives a
        # small, bounded boost to faq/troubleshooting docs.  No repository-
        # specific names are consulted.
        task_roles = roles_from_task(" ".join(task_tokens))
        if task_roles:
            doc_role = document_role(
                str(doc.get("path") or ""),
                doc.get("title", ""),
                doc.get("headings"),
            )
            if doc_role in task_roles:
                score += self.role_boost
                reasons.append(f"role match: {doc_role}")

        filename_hits = sorted(task_set & set(fields["filename"]))
        if filename_hits:
            score += len(filename_hits) * 3.5
            reasons.extend(f"filename token match: {token}" for token in filename_hits)

        directory_hits = sorted(task_set & set(fields["directory"]))
        if directory_hits:
            score += len(directory_hits) * 1.0
            reasons.extend(f"path token match: {token}" for token in directory_hits)

        title_hits = sorted(task_set & set(fields["title"]))
        if title_hits:
            score += len(title_hits) * 2.2
            reasons.extend(f"title token match: {token}" for token in title_hits)

        heading_hits = sorted(task_set & set(fields["headings"]))
        if heading_hits:
            score += min(len(heading_hits), 6) * 1.5
            reasons.extend(f"heading token match: {token}" for token in heading_hits[:6])

        summary_hits = sorted(task_set & set(fields["summary"]))
        if summary_hits:
            score += min(len(summary_hits), 6) * 0.35
            reasons.extend(f"summary token match: {token}" for token in summary_hits[:6])

        phrases = self._task_phrases(task_tokens)
        for phrase in self._phrase_hits(phrases, fields["path"]):
            score += 4.5
            reasons.append(f"phrase match: {' '.join(phrase)}")
        for phrase in self._phrase_hits(phrases, fields["title"]):
            score += 3.5
            reasons.append(f"title phrase match: {' '.join(phrase)}")
        for phrase in self._phrase_hits(phrases, fields["headings"]):
            score += 2.5
            reasons.append(f"heading phrase match: {' '.join(phrase)}")

        # Existing domain rules remain useful, but are intentionally secondary.
        for name, info in rule_hits.items():
            rule = self.rules[name]
            rule_cats = set(rule.get("categories", []))
            if rule_cats & doc_cats:
                score += min(len(info["hits"]), 3) * 0.45
                reasons.append(f"domain category assist: {name}")
            rule_tokens = set(
                self._tokenize(" ".join(str(keyword) for keyword in rule.get("keywords", [])))
            )
            topical = len(rule_tokens & set(fields["all"]))
            if topical:
                score += min(topical, 4) * 0.1
                reasons.append(f"domain text assist: {name}:{topical}")

        if rule_hits and (
            "plans" in doc_cats or any(category in ("plans", "changelog") for category in doc_cats)
        ):
            score += 0.1
            reasons.append("domain planning assist")
        return score, reasons

    @classmethod
    def _document_fields(cls, doc: dict) -> dict[str, list[str]]:
        path = str(doc.get("path") or "")
        filename = path.rsplit("/", 1)[-1]
        stem = PurePosixPath(filename).stem
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        title = str(doc.get("title") or "")
        headings = " ".join(str(value) for value in doc.get("headings", []) if value)
        summary = str(doc.get("summary") or "")
        return {
            "path": cls._tokenize(path),
            "directory": cls._tokenize(directory),
            "filename": cls._tokenize(stem),
            "title": cls._tokenize(title),
            "headings": cls._tokenize(headings),
            "summary": cls._tokenize(summary),
            "all": cls._tokenize(
                " ".join((path, title, headings, summary)),
            ),
        }

    @staticmethod
    def _light_stem(token: str) -> str:
        if len(token) > 5 and token.endswith("ies"):
            return token[:-3] + "y"
        if len(token) > 4 and token.endswith("es"):
            return token[:-2]
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            return token[:-1]
        return token

    @classmethod
    def _tokenize(cls, value: str, *, drop_stopwords: bool = False) -> list[str]:
        prepared = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", str(value or ""))
        tokens: list[str] = []
        for raw in _TOKEN_RE.findall(prepared.lower()):
            token = cls._light_stem(raw)
            if drop_stopwords and token in _STOPWORDS:
                continue
            tokens.append(token)
        return tokens

    @staticmethod
    def _task_phrases(tokens: list[str]) -> list[tuple[str, ...]]:
        phrases: list[tuple[str, ...]] = []
        max_size = min(4, len(tokens))
        for size in range(max_size, 1, -1):
            for start in range(0, len(tokens) - size + 1):
                phrase = tuple(tokens[start : start + size])
                if phrase not in phrases:
                    phrases.append(phrase)
        return phrases

    @staticmethod
    def _phrase_hits(
        phrases: list[tuple[str, ...]], field_tokens: list[str]
    ) -> list[tuple[str, ...]]:
        hits: list[tuple[str, ...]] = []
        for phrase in phrases:
            size = len(phrase)
            if any(field_tokens[i : i + size] == list(phrase) for i in range(len(field_tokens) - size + 1)):
                hits.append(phrase)
        return hits

    def _score_modules(
        self, modules: list[dict], rule_hits: dict, task_lower: str
    ) -> list[tuple[float, dict]]:
        scored = []
        task_tokens = set(self._tokenize(task_lower, drop_stopwords=True))
        for mod in modules:
            score = 0.0
            text = (
                mod.get("path", "")
                + "\n"
                + mod.get("name", "")
                + "\n"
                + mod.get("description", "")
            )
            mod_tokens = set(self._tokenize(text))
            keyword_hits = [
                keyword
                for keyword in (mod.get("keywords") or [])
                if self._tokenize(str(keyword), drop_stopwords=True)
                and self._tokenize(str(keyword), drop_stopwords=True)[0] in task_tokens
            ]
            score += len(keyword_hits) * 3.0
            name_hits = sorted(task_tokens & set(self._tokenize(mod.get("name", ""))))
            score += len(name_hits) * 2.0
            for name, info in rule_hits.items():
                rule = self.rules[name]
                if set(rule.get("categories", [])) & set(mod.get("categories", [])):
                    score += len(info["hits"]) * 0.75
                topical = len(
                    set(self._tokenize(" ".join(str(k) for k in rule.get("keywords", []))))
                    & mod_tokens
                )
                score += min(topical, 8) * 0.1
            scored.append((score, mod))
        scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        return scored

    def _score_knowledge(
        self, domains: list[dict], rule_hits: dict, task_lower: str
    ) -> list[tuple[float, dict]]:
        """知识域评分：manifest 关键词仍可参与匹配，但不主导文档路由。"""

        scored = []
        task_tokens = set(self._tokenize(task_lower, drop_stopwords=True))
        for domain in domains:
            score = 0.0
            keywords = domain.get("keywords") or []
            keyword_tokens = set(self._tokenize(" ".join(str(k) for k in keywords)))
            score += len(keyword_tokens & task_tokens) * 1.0
            domain_categories = set(domain.get("categories", []))
            domain_text = self._tokenize(
                domain.get("name", "") + " " + " ".join(str(k) for k in keywords)
            )
            for name, info in rule_hits.items():
                rule = self.rules[name]
                if set(rule.get("categories", [])) & domain_categories:
                    score += len(info["hits"]) * 0.75
                topical = len(
                    set(self._tokenize(" ".join(str(k) for k in rule.get("keywords", []))))
                    & set(domain_text)
                )
                score += min(topical, 6) * 0.1
            scored.append((score, domain))
        scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
        return scored

    def _search_text(self, doc: dict) -> str:
        return (
            str(doc.get("title") or "")
            + "\n"
            + " ".join(str(value) for value in doc.get("headings", []) if value)
            + "\n"
            + str(doc.get("summary") or "")
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
