"""项目扫描器：AGENTS.md 入口 + docs 文档索引 + 模块线索提取。

只保存：路径、标题、摘要、类别、模块关系。不保存源码。
"""

from __future__ import annotations

import fnmatch
import re
import time
from pathlib import Path

from .categories import categories_for
from .categories import doc_role
from .manifest import load_manifest
from .manifest import declared_doc_paths
from .safe_paths import resolve_safe_path
from .safe_paths import safe_relative_path

_FILE_EXT_RE = re.compile(
    r"\.(?:kt|kts|py|ts|tsx|js|jsx|java|go|rs|cs|swift|vue|svelte|md|markdown|sql|json)$", re.I
)
_FILE_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_./\\-]+\.(?:kt|kts|py|ts|tsx|js|jsx|java|go|rs|cs|swift|vue|svelte|md|markdown|sql|json)",
    re.I,
)
_DIR_PREFIX_RE = re.compile(
    r"(?:src|app|pages|modules|tools|lib|core|electron|drizzle|scripts|components|routes)/"
    r"[A-Za-z0-9_./\\-]*"
)
_CODE_EXT_RE = re.compile(
    r"\.(?:kt|kts|py|ts|tsx|js|jsx|java|go|rs|cs|swift|vue|svelte|sql|json)$", re.I
)

# 文档角色 -> 补充类别（role 覆盖后追加）
_ROLE_EXTRA_CATEGORIES = {
    "architecture": ["architecture"],
    "modules": ["modules"],
    "design": ["ui", "design"],
    "decisions": ["plans"],
    "contracts": ["api", "contract"],
    "operations": ["operations"],
}


def _norm_path_token(token: str) -> str:
    return token.strip().strip("`").replace("\\", "/").rstrip("/")


class ProjectScanner:
    def __init__(self, settings: dict):
        self.settings = settings
        scan = settings.get("scan", {})
        idx = settings.get("index", {})
        self.docs_dir = scan.get("docs_dir", "docs")
        self.entry_files = scan.get("entry_files", ["AGENTS.md"])
        self.extra_entry_files = scan.get("extra_entry_files", ["README.md"])
        self.doc_extensions = scan.get("doc_extensions", [".md", ".markdown"])
        self.skip_dir_names = scan.get("skip_dir_names", [])
        self.skip_dir_contains = scan.get("skip_dir_contains", [])
        self.max_file_bytes = scan.get("max_file_bytes", 100_000)
        self.max_docs = idx.get("max_docs_per_project", 200)
        self.max_summary_chars = idx.get("max_summary_chars", 500)
        self.max_headings = idx.get("max_headings", 40)
        self.max_entry_chars = idx.get("max_entry_chars", 4000)

    def scan(self, project_path: str) -> dict:
        root = Path(project_path).resolve()
        if not root.is_dir():
            raise ValueError(f"项目路径不存在或不是目录: {project_path}")

        entries, entry_text = self._read_entry_files(root)
        name = self._project_name(root, entries)
        manifest = load_manifest(root)
        manifest_project = (manifest or {}).get("project") or {}
        if manifest_project.get("name"):
            name = str(manifest_project["name"])[:60]
        project_type = manifest_project.get("type")

        role_overrides = {}
        for role, paths in (manifest or {}).get("documents", {}).items():
            for rel in paths:
                safe = safe_relative_path(root, rel)
                if safe is not None:
                    role_overrides[safe] = str(role)

        docs, doc_texts = self._scan_docs(root, role_overrides, manifest)
        modules = self._collect_modules(root, entry_text, doc_texts, manifest)
        knowledge_domains = self._manifest_knowledge_domains(root, manifest)

        return {
            "project": name,
            "project_path": str(root),
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "has_agents": any(e["path"] == "AGENTS.md" for e in entries),
            "manifest": manifest,
            "project_type": project_type,
            "entry_files": entries,
            "docs": docs,
            "modules": modules,
            "knowledge_domains": knowledge_domains,
            "counts": {
                "entry_files": len(entries),
                "docs": len(docs),
                "modules": len(modules),
                "knowledge_domains": len(knowledge_domains),
            },
        }

    def _read_entry_files(self, root: Path) -> tuple[list[dict], str]:
        entries: list[dict] = []
        texts: list[str] = []
        for fname in self.entry_files + self.extra_entry_files:
            rel = safe_relative_path(root, fname)
            if rel is None:
                continue
            p = resolve_safe_path(root, rel)
            if p is None or not p.is_file():
                continue
            text = self._read_text(p)
            if not text:
                continue
            title, headings = self._extract_headings(text)
            summary = self._make_summary(text, self.max_entry_chars)
            search = title + "\n" + " ".join(headings) + "\n" + summary
            entries.append({
                "path": rel,
                "title": title or rel,
                "headings": headings,
                "summary": summary,
                "categories": categories_for(rel, search),
            })
            texts.append(text)
        return entries, "\n\n".join(texts)

    def _project_name(self, root: Path, entries: list[dict]) -> str:
        for e in entries:
            if e["path"] == "AGENTS.md" and e.get("title"):
                name = re.split(r"[—\-|]", e["title"])[0].strip(" #")
                if (
                    name
                    and name.lower() not in ("agents.md", "readme.md", "readme")
                    and len(name) <= 60
                ):
                    return name
        return root.name

    def _scan_docs(
        self,
        root: Path,
        role_overrides: dict[str, str] | None = None,
        manifest: dict | None = None,
    ) -> tuple[list[dict], dict[str, str]]:
        role_overrides = role_overrides or {}
        docs: list[dict] = []
        doc_texts: dict[str, str] = {}
        seen: set[str] = set()
        docs_dir = resolve_safe_path(root, self.docs_dir)
        if docs_dir is not None and docs_dir.is_dir():
            for p in self._walk(root, docs_dir):
                if p.suffix.lower() not in self.doc_extensions:
                    continue
                text = self._read_text(p)
                if not text:
                    continue
                rel = p.relative_to(root).as_posix()
                seen.add(rel)
                title, headings = self._extract_headings(text)
                summary = self._make_summary(text, self.max_summary_chars)
                search = title + "\n" + " ".join(headings) + "\n" + summary
                role = role_overrides.get(rel) or doc_role(rel)
                categories = categories_for(rel, search)
                for extra in _ROLE_EXTRA_CATEGORIES.get(role, []):
                    if extra not in categories:
                        categories.append(extra)
                docs.append({
                    "path": rel,
                    "title": title or rel,
                    "headings": headings,
                    "summary": summary,
                    "categories": categories,
                    "role": role,
                    "size": p.stat().st_size,
                })
                doc_texts[rel] = text
                if len(docs) >= self.max_docs:
                    break

        # manifest 声明的文档（可能位于 docs/ 之外，如项目根目录）补扫
        entry_names = {
            safe
            for fname in self.entry_files + self.extra_entry_files
            if (safe := safe_relative_path(root, fname)) is not None
        }
        for rel in declared_doc_paths(manifest):
            safe = safe_relative_path(root, rel)
            if safe is None or safe in seen or safe in entry_names or len(docs) >= self.max_docs:
                continue
            p = resolve_safe_path(root, safe)
            if p is None or not p.is_file() or p.suffix.lower() not in self.doc_extensions:
                continue
            text = self._read_text(p)
            if not text:
                continue
            seen.add(safe)
            title, headings = self._extract_headings(text)
            summary = self._make_summary(text, self.max_summary_chars)
            search = title + "\n" + " ".join(headings) + "\n" + summary
            role = role_overrides.get(safe) or doc_role(safe)
            categories = categories_for(rel, search)
            for extra in _ROLE_EXTRA_CATEGORIES.get(role, []):
                if extra not in categories:
                    categories.append(extra)
            docs.append({
                "path": safe,
                "title": title or safe,
                "headings": headings,
                "summary": summary,
                "categories": categories,
                "role": role,
                "size": p.stat().st_size,
                "declared": True,
            })
            doc_texts[safe] = text

        preferred = self.settings.get("scan", {}).get("preferred_docs", [])
        docs.sort(key=lambda d: (
            preferred.index(d["path"].rsplit("/", 1)[-1])
            if d["path"].rsplit("/", 1)[-1] in preferred
            else 99,
            d["path"],
        ))
        return docs, doc_texts

    def _walk(self, root: Path, start: Path | None = None):
        """带剪枝的递归遍历，只产出文件；跳过重型/生成目录。"""
        stack = [start or root]
        visited_dirs: set[Path] = set()
        while stack:
            d = stack.pop()
            try:
                real_dir = d.resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if real_dir in visited_dirs:
                continue
            visited_dirs.add(real_dir)
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for p in entries:
                try:
                    try:
                        rel = p.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    if resolve_safe_path(root, rel) is None:
                        continue
                    if p.is_dir():
                        name = p.name
                        if name in self.skip_dir_names:
                            continue
                        if any(tok in name for tok in self.skip_dir_contains):
                            continue
                        if any(fnmatch.fnmatch(name, tok) for tok in self.skip_dir_names if "*" in tok):
                            continue
                        stack.append(p)
                    else:
                        yield p
                except OSError:
                    continue

    def _collect_modules(
        self,
        root: Path,
        entry_text: str,
        doc_texts: dict[str, str],
        manifest: dict | None = None,
    ) -> list[dict]:
        if manifest and manifest.get("modules"):
            modules = self._manifest_modules(root, manifest)
            if modules:
                return modules

        hints: dict[str, dict] = {}
        basename_index = self._build_basename_index(root)

        def add_hint(token: str, desc: str = "") -> None:
            path = _norm_path_token(token)
            if not path or path.startswith("http") or " " in path:
                return
            if not _FILE_EXT_RE.search(path) and "/" not in path:
                return
            safe = safe_relative_path(root, path)
            if safe is None:
                return
            target = resolve_safe_path(root, safe)
            if target is None or not target.exists():
                # 短文件名（无目录前缀）尝试按文件名在整个项目树中解析
                if "/" not in path and _FILE_EXT_RE.search(path):
                    matches = basename_index.get(path.lower())
                    if not matches:
                        return
                    path = matches[0]
                else:
                    return
            else:
                path = safe
            key = path.lower()
            if key not in hints:
                hints[key] = {"path": path, "desc": ""}
            if desc and not hints[key]["desc"]:
                hints[key]["desc"] = desc[:200]

        self._extract_path_hints(entry_text, add_hint)
        for text in doc_texts.values():
            self._extract_path_hints(text, add_hint)

        modules = []
        for h in hints.values():
            name = h["path"].split("/")[-1]
            if _FILE_EXT_RE.search(name):
                name = name.rsplit(".", 1)[0]
            modules.append({
                "name": name,
                "path": h["path"],
                "description": h["desc"],
                "categories": categories_for(h["path"], h["desc"]),
                "source": "auto",
            })
        modules.sort(key=lambda m: (m["path"].lower()))
        return modules

    def _manifest_modules(self, root: Path, manifest: dict) -> list[dict]:
        """按 context-manifest.yaml 生成模块（优先于自动扫描；paths 可选）。"""
        modules: list[dict] = []
        for m in manifest.get("modules", {}).values():
            keywords = m.get("keywords") or []
            declared_documents = self._safe_manifest_paths(root, m.get("documents"))
            decisions = self._safe_manifest_paths(root, m.get("decisions"))
            documents = self._safe_manifest_paths(
                root, declared_documents + decisions
            )
            paths = m.get("paths") or []
            if not paths:
                # 文档型模块：无代码路径，按关键词与关联文档参与匹配
                text = m["name"] + "\n" + " ".join(keywords)
                modules.append({
                    "name": m["name"],
                    "path": "",
                    "description": " / ".join(keywords)[:200] if keywords else "",
                    "categories": categories_for("", text),
                    "keywords": keywords,
                    "documents": documents,
                    "decisions": decisions,
                    "source": "manifest",
                })
                continue
            for path in paths:
                normalized = safe_relative_path(root, _norm_path_token(path))
                if not normalized:
                    continue
                text = normalized + "\n" + m["name"] + "\n" + " ".join(keywords)
                modules.append({
                    "name": m["name"],
                    "path": normalized,
                    "description": " / ".join(keywords)[:200] if keywords else "",
                    "categories": categories_for(normalized, text),
                    "keywords": keywords,
                    "documents": documents,
                    "decisions": decisions,
                    "source": "manifest",
                })
        modules.sort(key=lambda m: (m["path"].lower(), m["name"].lower()))
        return modules

    def _manifest_knowledge_domains(self, root: Path, manifest: dict | None) -> list[dict]:
        """按 context-manifest.yaml 的 knowledge_domains 生成知识域（业务知识而非代码模块）。"""
        if not manifest:
            return []
        domains: list[dict] = []
        for d in manifest.get("knowledge_domains", {}).values():
            paths = self._safe_manifest_paths(root, d.get("paths") or [])
            keywords = d.get("keywords") or []
            declared_documents = self._safe_manifest_paths(root, d.get("documents"))
            decisions = self._safe_manifest_paths(root, d.get("decisions"))
            documents = self._safe_manifest_paths(
                root, declared_documents + decisions
            )
            text = " ".join(paths) + "\n" + d["name"] + "\n" + " ".join(keywords)
            domains.append({
                "name": d["name"],
                "paths": paths,
                "path": paths[0] if paths else "",
                "documents": documents,
                "decisions": decisions,
                "keywords": keywords,
                "categories": categories_for("", text),
                "source": "manifest",
            })
        domains.sort(key=lambda d: d["name"].lower())
        return domains

    @staticmethod
    def _safe_manifest_paths(root: Path, paths) -> list[str]:
        out: list[str] = []
        for path in paths or []:
            safe = safe_relative_path(root, path)
            if safe is not None and safe not in out:
                out.append(safe)
        return out

    def _extract_path_hints(self, text: str, add_hint) -> None:
        if not text:
            return
        for line in text.splitlines():
            if "|" not in line:
                continue
            cells = [c.strip().strip("`") for c in line.split("|")]
            for i, cell in enumerate(cells):
                if not cell:
                    continue
                tokens = [t for t in _FILE_TOKEN_RE.findall(cell)]
                is_path = "/" in cell or "\\" in cell or bool(tokens)
                if is_path:
                    desc = " ".join(c for c in cells[i + 1:] if c)[:200]
                    for token in tokens:
                        add_hint(token, desc)
                    # 纯目录路径（无扩展名），如 app/src/main/res/drawable-nodpi/
                    if not tokens and _DIR_PREFIX_RE.search(cell):
                        add_hint(_DIR_PREFIX_RE.search(cell).group(0), desc)
        for m in _DIR_PREFIX_RE.finditer(text):
            add_hint(m.group(0))

    def _build_basename_index(self, root: Path) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for p in self._walk(root):
            if not _CODE_EXT_RE.search(p.name):
                continue
            rel = p.relative_to(root).as_posix()
            key = p.name.lower()
            index.setdefault(key, []).append(rel)
        for paths in index.values():
            paths.sort(key=lambda x: (x.count("/"), x))
        return index

    def _read_text(self, p: Path) -> str:
        try:
            data = p.read_bytes()
        except OSError:
            return ""
        if not data or len(data) > self.max_file_bytes:
            return ""
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _extract_headings(self, text: str) -> tuple[str | None, list[str]]:
        title = None
        headings: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                h = re.sub(r"^#+\s*", "", s).strip()
                if h:
                    if title is None:
                        title = h
                    headings.append(h)
            if len(headings) >= self.max_headings:
                break
        return title, headings

    def _make_summary(self, text: str, limit: int) -> str:
        parts: list[str] = []
        total = 0
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if s.startswith("|") and s.count("|") > 1:
                continue
            parts.append(s)
            total += len(s)
            if total >= limit:
                break
        return "\n".join(parts)[:limit]
