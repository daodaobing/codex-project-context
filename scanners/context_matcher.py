"""上下文匹配器：以仓库自身文档内容为主，规则信号为辅。纯规则，无 LLM。

V0.5 只优化 Ranking（Repository-Aware Ranking：compound normalization +
BM25-like + IDF + field weights + query coverage）。Selection 层（hard dedup /
soft diversity / role coverage / threshold / max cap）保持 V0.4 冻结不变。
"""

from __future__ import annotations

import math
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

# 极小通用 compound 表：只包含跨项目通用的英文复合词（setup/teardown 等），
# 并把它们展开成词组参与匹配，使 "SetupAndTeardown" 与 "set up and tear down"
# 在 token 层面完全一致。禁止加入项目专用词或 Benchmark 文件名。
_COMPOUND_PHRASES: dict[str, str] = {
    "setup": "set up",
    "teardown": "tear down",
    "codebase": "code base",
    "commandline": "command line",
}

# V0.5 BM25-like 评分：按字段加权，filename 最高、title 高、heading 中高、
# path 中、summary 低。权重与 V0.4 结构分权重保持同一量级。
_BM25_FIELD_WEIGHTS: dict[str, float] = {
    "filename": 3.0,
    "title": 2.0,
    "headings": 1.2,
    "path": 0.8,
    "summary": 0.3,
}

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
    _RANKING_MODES = {
        "structural",
        "bm25",          # compatibility alias for raw_bm25
        "raw_bm25",
        "full",          # first-round V0.5 additive + coverage baseline
        "bounded_bm25",
        "rrf",
    }

    def __init__(self, settings: dict, *, ranking_mode: str | None = None):
        idx = settings.get("index", {})
        ranking_mode = ranking_mode or idx.get("ranking_mode", "full")
        if ranking_mode not in self._RANKING_MODES:
            raise ValueError(
                "ranking_mode must be one of: structural, bm25, raw_bm25, "
                "full, bounded_bm25, rrf"
            )
        self.settings = settings
        self.ranking_mode = ranking_mode
        self.rules = settings.get("rules", {})
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
        # V0.5 ranking-only 参数（Selection 层不读这些值）。
        self.bm25_weight = float(idx.get("bm25_weight", 1.0))
        self.bm25_k1 = float(idx.get("bm25_k1", 1.2))
        self.bm25_b = float(idx.get("bm25_b", 0.75))
        self.coverage_bonus = float(idx.get("coverage_bonus", 2.0))
        self.bounded_bm25_k = float(idx.get("bounded_bm25_k", 10.0))
        self.bounded_bm25_max_boost = float(
            idx.get("bounded_bm25_max_boost", 4.0)
        )
        self.rrf_structural_weight = float(
            idx.get("rrf_structural_weight", 80.0)
        )
        self.rrf_bm25_weight = float(idx.get("rrf_bm25_weight", 40.0))
        self.rrf_k = float(idx.get("rrf_k", 10.0))
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
        docs_scored = self._ranked_candidates(result, task)
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
            out["raw_query_tokens"] = self._tokenize(task, drop_stopwords=False)
            query_tokens = self._tokenize(task, drop_stopwords=True)
            out["query_tokens"] = query_tokens
            out["important_query_tokens"] = self._important_query_tokens(
                self._build_corpus(project), query_tokens
            )
        return out

    def _ranked_candidates(
        self, result: dict, task: str
    ) -> list[tuple[float, dict, list[str]]]:
        """Apply manifest boosts and the selected fusion, then sort.

        This is the raw/full ranking used by both the Selection path (``match``)
        and the candidate retrieval tool.  It runs *before* hard dedup, soft
        diversity, role coverage, threshold, or the max cap, so candidate
        retrieval never reintroduces Selection Recall Loss.
        """

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
                            structural = self._trace_components(reasons)["structural_score"]
                            docs_scored[i] = (
                                old_score + score * weight,
                                doc,
                                reasons
                                + [
                                    f"manifest:{item.get('name')}",
                                    f"component:structural={structural + score * weight:.6f}",
                                ],
                            )
                            break

        boost_docs(result["modules"][: self.top_modules], 0.75)
        boost_docs(result["knowledge"][:4], 1.0)
        # Manifest boosts are structural signals. Re-apply the selected fusion
        # after those boosts so bounded/RRF modes rank the final candidate set.
        task_tokens = self._tokenize(task, drop_stopwords=True)
        self._apply_fusion(docs_scored, task_tokens)
        docs_scored.sort(key=lambda x: (-x[0], x[1].get("path", "")))
        return docs_scored

    def candidate_ranking(
        self, project: dict, task: str, limit: int = 10
    ) -> list[tuple[float, dict, list[str]]]:
        """Return the raw RRF ranking before Selection, deduped by path.

        ``limit`` bounds the returned list.  This is the single ranking source
        used by ``get_context_candidates``; ``match`` ranks through the same
        ``_ranked_candidates`` path so candidate parity is structural.
        """

        result = self.analyze(project, task)
        ranked = self._ranked_candidates(result, task)
        seen: set[str] = set()
        deduped: list[tuple[float, dict, list[str]]] = []
        for score, doc, reasons in ranked:
            path = str(doc.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            if score <= 0:
                continue
            deduped.append((score, doc, reasons))
        return deduped[:limit]

    @staticmethod
    def candidate_reasons(reasons: list[str], *, limit: int = 8) -> list[str]:
        """Map internal scoring reasons to concise, deterministic labels.

        Agents need to answer "why is this document relevant?", not "how did
        RRF add up?".  Numeric internals (component:/fusion:/bm25=) are dropped.
        """

        order = [
            ("filename token match", "filename match"),
            ("title token match", "title match"),
            ("title phrase match", "phrase match"),
            ("heading token match", "heading match"),
            ("heading phrase match", "phrase match"),
            ("path token match", "path match"),
            ("phrase match", "phrase match"),
            ("summary token match", "summary match"),
            ("role match", "role match"),
            ("manifest:", "manifest match"),
            ("domain category assist", "domain match"),
            ("domain text assist", "domain match"),
            ("domain planning assist", "domain match"),
            ("entry fallback bonus", "entry fallback"),
        ]
        labels: list[str] = []
        seen: set[str] = set()
        for reason in reasons:
            label = None
            for prefix, candidate in order:
                if reason.startswith(prefix):
                    label = candidate
                    break
            if label and label not in seen:
                seen.add(label)
                labels.append(label)
            if len(labels) >= limit:
                break
        return labels

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
        corpus = self._build_corpus(project)

        scored_docs = []
        for doc in project.get("docs", []):
            score, reasons = self._score_doc(
                doc, rule_hits, task_tokens, corpus=corpus
            )
            scored_docs.append((score, doc, reasons))

        for entry in project.get("entry_files", []):
            doc = {**entry, "path": entry["path"]}
            score, reasons = self._score_doc(
                doc, rule_hits, task_tokens, corpus=corpus
            )
            score += self.entry_bonus
            reasons.append(f"entry fallback bonus: {self.entry_bonus:g}")
            structural = self._trace_components(reasons)["structural_score"]
            reasons.append(
                f"component:structural={structural + self.entry_bonus:.6f}"
            )
            scored_docs.append((score, doc, reasons))

        self._apply_fusion(scored_docs, task_tokens)

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

    def _apply_fusion(
        self, scored_docs: list[tuple[float, dict, list[str]]], task_tokens: list[str]
    ) -> None:
        """Apply only the selected V0.5 fusion layer; Selection stays frozen."""

        if not scored_docs:
            return
        mode = "raw_bm25" if self.ranking_mode == "bm25" else self.ranking_mode

        structural_order = sorted(
            scored_docs,
            key=lambda item: (
                -self._trace_components(item[2])["structural_score"],
                item[1].get("path", ""),
            ),
        )
        bm25_order = sorted(
            scored_docs,
            key=lambda item: (
                -self._trace_components(item[2])["bm25_score"],
                item[1].get("path", ""),
            ),
        )
        structural_rank = {
            str(item[1].get("path") or ""): rank
            for rank, item in enumerate(structural_order, start=1)
        }
        bm25_rank = {
            str(item[1].get("path") or ""): rank
            for rank, item in enumerate(bm25_order, start=1)
        }
        for index, (old_score, doc, reasons) in enumerate(scored_docs):
            path = str(doc.get("path") or "")
            components = self._trace_components(reasons)
            structural = components["structural_score"]
            bm25 = components["bm25_score"]
            srank = structural_rank[path]
            brank = bm25_rank[path]
            reasons.extend(
                [
                    f"fusion:structural_rank={srank}",
                    f"fusion:bm25_rank={brank}",
                ]
            )
            if mode == "bounded_bm25":
                normalized = bm25 / (bm25 + self.bounded_bm25_k) if bm25 > 0 else 0.0
                boost = normalized * self.bounded_bm25_max_boost
                fused = structural + boost
                reasons.extend(
                    [
                        f"component:structural={structural:.6f}",
                        f"component:bm25={bm25:.6f}",
                        f"fusion:score={fused:.6f}",
                        f"fusion:bounded_bm25_raw={bm25:.6f}",
                        f"fusion:bounded_bm25_normalized={normalized:.6f}",
                        f"fusion:bounded_bm25_boost={boost:.6f}",
                        f"fusion:bounded_bm25_cap={self.bounded_bm25_max_boost:.6f}",
                    ]
                )
            elif mode == "rrf":
                fused = (
                    self.rrf_structural_weight / (self.rrf_k + srank)
                    + self.rrf_bm25_weight / (self.rrf_k + brank)
                )
                reasons.extend(
                    [
                        f"fusion:structural_rank={srank}",
                        f"fusion:bm25_rank={brank}",
                        f"fusion:rrf_score={fused:.6f}",
                        f"fusion:score={fused:.6f}",
                        f"fusion:rrf_structural_weight={self.rrf_structural_weight:.6f}",
                        f"fusion:rrf_bm25_weight={self.rrf_bm25_weight:.6f}",
                        f"fusion:rrf_k={self.rrf_k:.6f}",
                    ]
                )
            else:
                # Structural, raw BM25, and the historical full mode retain
                # their existing additive score; the rank annotations above
                # are diagnostics only.
                continue
            scored_docs[index] = (fused, doc, reasons)

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

        # ``trace`` is a ranking diagnostic, so its raw rank must refer to the
        # complete score ordering before thresholding, role coverage, or hard
        # deduplication.  The selection loop below intentionally works on a
        # filtered candidate set, but omitted documents still need a trace row
        # for benchmark rank metrics and post-hoc debugging.
        raw_rank_by_path: dict[str, int] = {}
        for raw_rank, (_, doc, _) in enumerate(docs_scored, start=1):
            raw_rank_by_path.setdefault(str(doc.get("path") or ""), raw_rank)

        task_roles = roles_from_task(task) if task else []
        task_role = task_roles[0] if task_roles else ""
        # Keep the trace schema total even when no document reaches the
        # positive-score pool and fallback selection is used.
        threshold = self.min_relevant_score
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
                            raw_rank_by_path.get(str(doc.get("path") or ""), 1),
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
                traced_ranks = {row["rank_before"] for row in trace_rows}
                for raw_rank, (score, doc, reasons) in enumerate(docs_scored, start=1):
                    if raw_rank in traced_ranks:
                        continue
                    trace_rows.append(
                        self._trace_row(
                            doc,
                            score,
                            score,
                            raw_rank,
                            None,
                            False,
                            task_role,
                            threshold,
                            reasons,
                            hard_duplicate=False,
                            hard_duplicate_of=None,
                            penalty=0.0,
                        )
                    )
                trace_rows.sort(key=lambda row: (row["rank_before"], row["path"]))
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
                    "rank_before": raw_rank_by_path.get(path, 1),
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
                # Keep V0.4 selection semantics frozen: compound expansion is
                # a ranking signal only and must not change soft-diversity
                # overlap decisions.
                candidate_tokens = self._selection_document_tokens(doc)
                selected_tokens = self._selection_document_tokens(selected_doc)
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
        if trace:
            traced_ranks = {row["rank_before"] for row in trace_rows}
            for raw_rank, (score, doc, reasons) in enumerate(docs_scored, start=1):
                if raw_rank in traced_ranks:
                    continue
                trace_rows.append(
                    self._trace_row(
                        doc,
                        score,
                        score,
                        raw_rank,
                        None,
                        False,
                        task_role,
                        threshold,
                        reasons,
                        hard_duplicate=False,
                        hard_duplicate_of=None,
                        penalty=0.0,
                    )
                )
            trace_rows.sort(key=lambda row: (row["rank_before"], row["path"]))
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
        components = self._trace_components(reasons)
        fusion = self._trace_fusion(reasons)
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
            **components,
            "query_coverage_score": components["coverage_score"],
            "final_raw_score": round(raw_score, 4),
            **fusion,
            "document_tokens": self._document_fields(doc)["all"],
        }

    @staticmethod
    def _trace_components(reasons: list[str]) -> dict[str, float]:
        components = {
            "structural_score": 0.0,
            "bm25_score": 0.0,
            "coverage_score": 0.0,
            "role_score": 0.0,
            "alias_score": 0.0,
            "phrase_score": 0.0,
        }
        names = {
            "structural": "structural_score",
            "bm25": "bm25_score",
            "coverage": "coverage_score",
            "role": "role_score",
            "alias": "alias_score",
            "phrase": "phrase_score",
        }
        for reason in reasons:
            if not reason.startswith("component:") or "=" not in reason:
                continue
            name, value = reason[len("component:") :].split("=", 1)
            target = names.get(name)
            if target is None:
                continue
            try:
                components[target] = round(float(value), 6)
            except ValueError:
                continue
        return components

    @staticmethod
    def _trace_fusion(reasons: list[str]) -> dict[str, float | int | list[str]]:
        values: dict[str, float | int | list[str]] = {
            "structural_rank": 0,
            "bm25_rank": 0,
            "fusion_score": 0.0,
            "bm25_raw": 0.0,
            "bm25_normalized": 0.0,
            "bm25_boost": 0.0,
            "bm25_boost_cap": 0.0,
            "fusion_reasons": [],
        }
        prefixes = {
            "fusion:structural_rank=": "structural_rank",
            "fusion:bm25_rank=": "bm25_rank",
            "fusion:rrf_score=": "fusion_score",
            "fusion:bounded_bm25_raw=": "bm25_raw",
            "fusion:bounded_bm25_normalized=": "bm25_normalized",
            "fusion:bounded_bm25_boost=": "bm25_boost",
            "fusion:bounded_bm25_cap=": "bm25_boost_cap",
            "fusion:score=": "fusion_score",
        }
        explanations: list[str] = []
        for reason in reasons:
            for prefix, key in prefixes.items():
                if reason.startswith(prefix):
                    raw = reason[len(prefix) :]
                    try:
                        values[key] = int(raw) if key.endswith("_rank") else round(float(raw), 6)
                    except ValueError:
                        pass
                    break
            if reason.startswith("fusion:"):
                explanations.append(reason)
        values["fusion_reasons"] = explanations
        return values

    @classmethod
    def _important_query_tokens(
        cls, corpus: dict, query_tokens: list[str], *, limit: int = 8
    ) -> list[str]:
        """Rank query tokens by repository-local IDF for trace diagnostics."""

        unique = list(dict.fromkeys(query_tokens))
        scores: dict[str, float] = {}
        for token in unique:
            field_scores: list[float] = []
            for stat in corpus.values():
                n = int(stat.get("n", 0))
                df = int(stat.get("df", {}).get(token, 0))
                if n and df:
                    field_scores.append(math.log(1.0 + (n - df + 0.5) / (df + 0.5)))
            scores[token] = max(field_scores, default=0.0)
        return sorted(unique, key=lambda token: (-scores[token], token))[:limit]

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
        *,
        corpus: dict | None = None,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        bm25_score = 0.0
        coverage_score = 0.0
        role_score = 0.0
        phrase_score = 0.0
        alias_score = 0.0
        doc_cats = set(doc.get("categories", []))
        fields = self._document_fields(doc)
        task_set = set(task_tokens)

        # V0.5: BM25-like relevance over the repo-local corpus.  IDF is computed
        # per repository (rare terms win), and each field is weighted.  This is
        # additive to the V0.4 structural score so already-strong signals are
        # preserved; the Selection layer is untouched.
        if self.ranking_mode != "structural" and corpus is not None and task_set:
            bm25 = self._bm25_for_doc(corpus, fields, task_set)
            if bm25 > 0:
                bm25_score = self.bm25_weight * bm25
                score += bm25_score
                reasons.append(f"bm25={bm25:.3f}")
            if self.ranking_mode == "full":
                coverage = self._query_coverage(fields, task_set)
                if coverage > 0:
                    coverage_score = self.coverage_bonus * coverage
                    score += coverage_score
                    reasons.append(f"coverage={coverage:.3f}")

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
                role_score = self.role_boost
                score += role_score
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
            phrase_score += 4.5
            reasons.append(f"phrase match: {' '.join(phrase)}")
        for phrase in self._phrase_hits(phrases, fields["title"]):
            score += 3.5
            phrase_score += 3.5
            reasons.append(f"title phrase match: {' '.join(phrase)}")
        for phrase in self._phrase_hits(phrases, fields["headings"]):
            score += 2.5
            phrase_score += 2.5
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
        structural_score = score - bm25_score - coverage_score
        reasons.extend(
            [
                f"component:structural={structural_score:.6f}",
                f"component:bm25={bm25_score:.6f}",
                f"component:coverage={coverage_score:.6f}",
                f"component:role={role_score:.6f}",
                f"component:alias={alias_score:.6f}",
                f"component:phrase={phrase_score:.6f}",
            ]
        )
        return score, reasons

    @classmethod
    def _build_corpus(cls, project: dict) -> dict:
        """Build repo-local per-field token lists plus DF/avgdl statistics.

        The corpus covers only the project's own indexed docs, so IDF reflects
        within-repository rarity (e.g. "jsdom" is rare in Jest docs while
        "mock" is common).  No external language model or embedding is used.
        """

        stats: dict[str, dict] = {}
        for field in _BM25_FIELD_WEIGHTS:
            token_lists: list[list[str]] = []
            stats[field] = {"lists": token_lists, "n": 0, "df": {}, "avgdl": 0.0}
        # Entry files (AGENTS.md/README.md) are part of the scanned context
        # corpus too.  Excluding them makes repository-local IDF depend on the
        # scanner's storage split rather than on the repository's actual docs.
        seen_paths: set[str] = set()
        corpus_docs = [
            *project.get("docs", []),
            *project.get("entry_files", []),
        ]
        for doc in corpus_docs:
            if not isinstance(doc, dict):
                continue
            path = str(doc.get("path") or "")
            if path in seen_paths:
                continue
            seen_paths.add(path)
            fields = cls._document_fields(doc)
            for field in _BM25_FIELD_WEIGHTS:
                stats[field]["lists"].append(fields[field])
        for field in _BM25_FIELD_WEIGHTS:
            token_lists = stats[field]["lists"]
            n = len(token_lists)
            df: dict[str, int] = {}
            for tokens in token_lists:
                for token in set(tokens):
                    df[token] = df.get(token, 0) + 1
            avgdl = sum(len(tokens) for tokens in token_lists) / n if n else 0.0
            stats[field] = {"lists": token_lists, "n": n, "df": df, "avgdl": avgdl}
        return stats

    def _bm25_for_doc(
        self,
        corpus: dict,
        doc_fields: dict[str, list[str]],
        task_set: set[str],
    ) -> float:
        """BM25-like score of one document against the task tokens.

        k1 = 1.2 / b = 0.75 (classic BM25).  Terms absent from the repository
        corpus contribute nothing (no evidence).  Each field is scored
        separately with its own length normalization and multiplied by a fixed
        field weight.
        """

        total = 0.0
        for field, weight in _BM25_FIELD_WEIGHTS.items():
            stat = corpus.get(field)
            if not stat or stat["n"] == 0 or stat["avgdl"] <= 0:
                continue
            tokens = doc_fields[field]
            if not tokens:
                continue
            dl = len(tokens)
            df = stat["df"]
            n = stat["n"]
            avgdl = stat["avgdl"]
            field_score = 0.0
            for token in task_set:
                token_df = df.get(token, 0)
                if token_df <= 0:
                    continue
                freq = tokens.count(token)
                if freq <= 0:
                    continue
                idf = math.log(1.0 + (n - token_df + 0.5) / (token_df + 0.5))
                denominator = freq + self.bm25_k1 * (
                    1.0 - self.bm25_b + self.bm25_b * dl / avgdl
                )
                field_score += idf * (freq * (self.bm25_k1 + 1.0)) / denominator
            total += weight * field_score
        return total

    @staticmethod
    def _query_coverage(
        doc_fields: dict[str, list[str]],
        task_set: set[str],
    ) -> float:
        """Fraction of meaningful task tokens covered by the document."""

        if not task_set:
            return 0.0
        doc_set = set(doc_fields["all"])
        return len(task_set & doc_set) / len(task_set)
    @classmethod
    def _document_fields(
        cls, doc: dict, *, expand_compounds: bool = True
    ) -> dict[str, list[str]]:
        path = str(doc.get("path") or "")
        filename = path.rsplit("/", 1)[-1]
        stem = PurePosixPath(filename).stem
        directory = path.rsplit("/", 1)[0] if "/" in path else ""
        title = str(doc.get("title") or "")
        headings = " ".join(str(value) for value in doc.get("headings", []) if value)
        summary = str(doc.get("summary") or "")
        return {
            "path": cls._tokenize(path, expand_compounds=expand_compounds),
            "directory": cls._tokenize(directory, expand_compounds=expand_compounds),
            "filename": cls._tokenize(stem, expand_compounds=expand_compounds),
            "title": cls._tokenize(title, expand_compounds=expand_compounds),
            "headings": cls._tokenize(headings, expand_compounds=expand_compounds),
            "summary": cls._tokenize(summary, expand_compounds=expand_compounds),
            "all": cls._tokenize(
                " ".join((path, title, headings, summary)),
                expand_compounds=expand_compounds,
            ),
        }

    @classmethod
    def _selection_document_tokens(cls, doc: dict) -> list[str]:
        """Return the pre-V0.5 token stream used by soft-diversity selection."""

        return cls._document_fields(doc, expand_compounds=False)["all"]

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
    def _tokenize(
        cls,
        value: str,
        *,
        drop_stopwords: bool = False,
        expand_compounds: bool = True,
    ) -> list[str]:
        prepared = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", str(value or ""))
        tokens: list[str] = []
        for raw in _TOKEN_RE.findall(prepared.lower()):
            token = cls._light_stem(raw)
            expanded = _COMPOUND_PHRASES.get(token)
            if expand_compounds and expanded:
                # Compound expansion happens before stopword filtering so the
                # phrase parts (set/up/tear/down) survive drop_stopwords.
                tokens.extend(expanded.split())
                continue
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
