"""Skill 推荐 v1：基于 SKILL.md name/description 的关键词匹配。

- 无 embedding / 向量 / AI 评分 / 学习机制
- 主匹配：任务词与 name/description 词重叠（英文词 + 中文二元组）
- 补充：settings.skill_rules 扁平映射（可选，命中才追加，不做复杂规则体系）
- 宁缺毋滥：无真实命中不强行推荐；deprecated（bak 残留）不参与推荐；默认最多 5 条
"""

from __future__ import annotations

import re

from skills.skill_registry import _load_index, _load_settings

_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
DEFAULT_LIMIT = 5


def _tokens(text: str) -> set[str]:
    """任务/技能文本 -> 词集合：英文小写词 + 中文二元组。"""
    text = (text or "").lower()
    toks: set[str] = set(_WORD_RE.findall(text))
    for seg in _CJK_RE.findall(text):
        if len(seg) == 1:
            toks.add(seg)
            continue
        for i in range(len(seg) - 1):
            toks.add(seg[i : i + 2])
    return toks


def _skill_tokens(skill: dict) -> tuple[set[str], set[str]]:
    name = (skill.get("name") or "").replace("-", " ").replace("_", " ")
    return _tokens(name), _tokens(skill.get("description") or "")


def _cap_limit(limit: int | None, settings: dict) -> int:
    max_rec = (settings.get("skill") or {}).get("max_recommendations", DEFAULT_LIMIT)
    try:
        requested = max(1, int(limit))
    except (TypeError, ValueError):
        requested = DEFAULT_LIMIT
    return min(requested, max_rec)


def recommend(task: str, limit: int | None = None) -> dict:
    settings = _load_settings()
    cap = _cap_limit(limit, settings)
    task_toks = _tokens(task)
    if not task_toks:
        return {"task": task, "recommended": [], "count": 0, "total_matches": 0}

    index = _load_index()
    results: list[dict] = []

    # 主匹配：name/description 词重叠（跳过 deprecated 残留）
    for s in index.get("skills", []):
        if s.get("category") == "deprecated":
            continue
        name_toks, desc_toks = _skill_tokens(s)
        name_hits = sorted(task_toks & name_toks)
        desc_hits = sorted(task_toks & desc_toks)
        if not name_hits and not desc_hits:
            continue
        # 宁缺毋滥：至少 1 个 name 命中，或 >=2 个 description 命中
        if not name_hits and len(desc_hits) < 2:
            continue
        reasons = []
        if name_hits:
            reasons.append("name 命中: " + ", ".join(name_hits))
        if desc_hits:
            reasons.append("description 命中: " + ", ".join(desc_hits[:4]))
        score = (2 * len(name_hits) + len(desc_hits)) / max(1, len(task_toks))
        results.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "path": s.get("path"),
            "score": round(min(1.0, score), 3),
            "reasons": reasons,
        })

    # 补充：扁平 skill_rules（命中才追加，且只加 registry 中存在的技能）
    existing_ids = {r["id"] for r in results}
    rules = settings.get("skill_rules") or {}
    task_text = (task or "").lower()
    for rule in rules.values():
        if not isinstance(rule, dict):
            continue
        kws = [str(k).lower() for k in rule.get("keywords", [])]
        if not any(k in task_text for k in kws):
            continue
        label = rule.get("label") or "规则"
        for sid in rule.get("skills", []):
            if sid in existing_ids:
                continue
            for s in index.get("skills", []):
                if s.get("category") == "deprecated":
                    continue
                if s.get("id") == sid:
                    results.append({
                        "id": s.get("id"),
                        "name": s.get("name"),
                        "path": s.get("path"),
                        "score": 0.6,
                        "reasons": [f"规则命中: {label}"],
                    })
                    existing_ids.add(sid)
                    break

    results.sort(key=lambda r: -r["score"])
    out = results[:cap]
    return {
        "task": task,
        "recommended": out,
        "count": len(out),
        "total_matches": len(results),
    }
