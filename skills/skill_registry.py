"""Skill Registry: scan skill roots, read SKILL.md frontmatter, build read-only metadata index.

Constraints:
- Never moves or modifies any Skill file.
- category is display/filter only (deprecated / general); no complex taxonomy.
- Roots and index path can be overridden by env vars for test isolation:
  CONTEXT_SERVER_SKILL_ROOTS (os.pathsep separated), CONTEXT_SERVER_SKILL_INDEX.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
INDEX_FILE = "skill-registry.json"
DESCRIPTION_MAX = 400

_lock = threading.Lock()
_frontmatter_re = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _load_settings() -> dict:
    with open(ROOT / "config" / "settings.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _roots(settings: dict) -> list[Path]:
    env = os.environ.get("CONTEXT_SERVER_SKILL_ROOTS")
    if env:
        return [Path(p) for p in env.split(os.pathsep) if p.strip()]
    roots = (settings.get("skill") or {}).get("roots") or []
    return [Path(r).expanduser() for r in roots]


def _index_path() -> Path:
    env = os.environ.get("CONTEXT_SERVER_SKILL_INDEX")
    if env:
        return Path(env)
    return ROOT / "index" / INDEX_FILE


def _load_index() -> dict:
    p = _index_path()
    if not p.is_file():
        return {"version": 1, "generated_at": "", "roots": [], "summary": {}, "skills": []}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(index: dict) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _read_frontmatter(path: Path) -> dict:
    """Read SKILL.md YAML frontmatter (name/description); fall back to folder name."""
    name = path.parent.name
    description = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"name": name, "description": description}
    m = _frontmatter_re.match(text)
    if not m:
        return {"name": name, "description": description}
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    fm_name = meta.get("name")
    if isinstance(fm_name, str) and fm_name.strip():
        name = fm_name.strip()
    fm_desc = meta.get("description")
    if isinstance(fm_desc, str) and fm_desc.strip():
        description = fm_desc.strip()[:DESCRIPTION_MAX]
    return {"name": name, "description": description}


def _category(path: Path, deprecated_suffixes: list[str]) -> str:
    """Simple category for display/filter only: deprecated suffix in path -> deprecated, else general."""
    s = str(path).replace("\\", "/")
    return "deprecated" if any(suf in s for suf in deprecated_suffixes) else "general"


def scan(force: bool = True) -> dict:
    """Scan skill roots and (re)build skill-registry.json (read-only metadata)."""
    settings = _load_settings()
    skill_cfg = settings.get("skill") or {}
    deprecated_suffixes = skill_cfg.get("deprecated_suffixes") or [".bak-en-20260723-134214"]
    roots = _roots(settings)
    with _lock:
        skills: list[dict] = []
        seen: set[str] = set()
        missing: list[str] = []
        for root in roots:
            if not root.is_dir():
                missing.append(str(root))
                continue
            for p in sorted(root.rglob("SKILL.md")):
                key = str(p.resolve())
                if key in seen:
                    continue
                seen.add(key)
                meta = _read_frontmatter(p)
                skills.append({
                    "id": meta["name"],
                    "name": meta["name"],
                    "path": str(p),
                    "root": root.name,
                    "description": meta["description"],
                    "category": _category(p, deprecated_suffixes),
                })
        skills.sort(key=lambda s: s["name"].lower())
        summary = {
            "total": len(skills),
            "deprecated": sum(1 for s in skills if s["category"] == "deprecated"),
            "general": sum(1 for s in skills if s["category"] == "general"),
        }
        index = {
            "version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "roots": [str(p) for p in roots],
            "summary": summary,
            "skills": skills,
        }
        if force or not _index_path().is_file():
            _save_index(index)
        return {
            "registry_version": index["version"],
            "generated_at": index["generated_at"],
            "roots": index["roots"],
            "missing_roots": missing,
            "summary": summary,
        }


def list_skills(category: str | None = None, limit: int = 500) -> dict:
    """List registered skills; category filters deprecated / general only."""
    index = _load_index()
    skills = index.get("skills", [])
    if category:
        skills = [s for s in skills if s.get("category") == category]
    total = len(skills)
    try:
        cap = max(1, int(limit))
    except (TypeError, ValueError):
        cap = 500
    return {
        "count": total,
        "returned": min(total, cap),
        "category": category,
        "skills": skills[:cap],
    }


def get_skill(name: str) -> dict:
    """Look up one skill by name/id."""
    index = _load_index()
    for s in index.get("skills", []):
        if s.get("name") == name or s.get("id") == name:
            return s
    return {"error": f"skill not found: {name}", "available": len(index.get("skills", []))}
