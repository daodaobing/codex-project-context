"""Project Bootstrap：为缺少 AGENTS.md/docs/.codex/workflow.md 的项目生成初始上下文。

只创建文档文件，不修改业务代码。创建前必须先返回预览。
"""

from __future__ import annotations

import json
from pathlib import Path

from .safe_paths import resolve_safe_path


class ProjectBootstrap:
    def __init__(self, settings: dict):
        self.settings = settings
        b = settings.get("bootstrap", {})
        self.max_readme = int(b.get("max_readme_chars", 600))
        self.max_src_entries = int(b.get("max_src_entries", 30))
        self.max_pkg_deps = int(b.get("max_pkg_deps", 15))
        self.max_pkg_scripts = int(b.get("max_pkg_scripts", 5))
        self.max_configs = int(b.get("max_configs", 10))

    def preview(self, project_path: str) -> dict:
        root = Path(project_path).resolve()
        existing = self._check_existing(root)
        info = self._probe(root)
        files = self._plan_files(root, info)
        return {
            "project_path": str(root),
            "project_name": info["name"],
            "probe": info["probe"],
            "existing": existing,
            "files": files,
            "dry_run": True,
            "message": "预览完成。确认后调用 initialize_project_context(project_path, dry_run=false) 创建；已存在的文件会跳过。",
        }

    def create(self, project_path: str) -> dict:
        root = Path(project_path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        preview = self.preview(str(root))
        created: list[str] = []
        skipped: list[str] = []
        for f in preview["files"]:
            if f["action"] == "skip":
                skipped.append(f["path"])
                continue
            target = resolve_safe_path(root, f["path"])
            if target is None:
                skipped.append(f["path"])
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f["content"], encoding="utf-8", newline="\n")
            created.append(f["path"])
        return {
            **preview,
            "dry_run": False,
            "created": created,
            "skipped": skipped,
        }

    # ---------- 探测 ----------

    def _check_existing(self, root: Path) -> dict:
        targets = [
            "AGENTS.md",
            "docs",
            "docs/architecture.md",
            "docs/modules.md",
            "docs/design-system.md",
            "docs/decisions.md",
            "docs/changelog.md",
            ".codex/workflow.md",
            "context-manifest.yaml",
        ]
        return {
            t: (target is not None and target.exists())
            for t in targets
            for target in [resolve_safe_path(root, t)]
        }

    def _probe(self, root: Path) -> dict:
        name = root.name
        readme = self._read_first(root, "README.md") or self._read_first(root, "README.zh.md")
        pkg: dict = {}
        pkg_path = resolve_safe_path(root, "package.json")
        if pkg_path is not None and pkg_path.is_file():
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:  # noqa: BLE001
                pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        tree = self._dir_tree(root)
        configs = self._config_files(root)
        return {
            "name": name,
            "probe": {
                "readme_preview": readme[: self.max_readme],
                "package_name": pkg.get("name"),
                "package_description": pkg.get("description"),
                "dependencies": list(deps.keys())[: self.max_pkg_deps],
                "scripts": list((pkg.get("scripts") or {}).keys())[: self.max_pkg_scripts],
                "src_tree": tree,
                "config_files": configs,
            },
        }

    def _read_first(self, root: Path, rel: str, limit: int | None = None) -> str:
        limit = limit or self.max_readme
        p = resolve_safe_path(root, rel)
        if p is None or not p.is_file():
            return ""
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        return "\n".join(lines)[: limit]

    def _dir_tree(self, root: Path) -> list[list[str]]:
        candidates = [root / "src", root / "app", root / "pages", root / "electron"]
        base = next(
            (
                safe
                for c in candidates
                if (safe := resolve_safe_path(root, c.relative_to(root))) is not None
                and safe.is_dir()
            ),
            None,
        )
        if base is None:
            base = root
        skip = set(self.settings.get("scan", {}).get("skip_dir_names", []))
        entries: list[list[str]] = []
        try:
            children = sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError:
            return entries
        for child in children:
            if child.name in skip:
                continue
            try:
                rel = child.relative_to(root).as_posix()
            except ValueError:
                continue
            if resolve_safe_path(root, rel) is None:
                continue
            entries.append([rel, "目录" if child.is_dir() else "文件"])
            if len(entries) >= self.max_src_entries:
                break
        return entries

    def _config_files(self, root: Path) -> list[str]:
        skip_names = {
            "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "node_modules",
            "AGENTS.md", "README.md", "README.zh.md", "context-manifest.yaml",
        }
        exts = (".json", ".toml", ".yaml", ".yml", ".properties")
        out = []
        try:
            for child in sorted(root.iterdir(), key=lambda x: x.name.lower()):
                try:
                    rel = child.relative_to(root).as_posix()
                except ValueError:
                    continue
                if (
                    resolve_safe_path(root, rel) is not None
                    and child.is_file()
                    and child.suffix.lower() in exts
                    and child.name not in skip_names
                ):
                    out.append(child.name)
                if len(out) >= self.max_configs:
                    break
        except OSError:
            pass
        return out

    # ---------- 模板生成 ----------

    def _plan_files(self, root: Path, info: dict) -> list[dict]:
        name = info["name"]
        probe = info["probe"]
        summary = probe["readme_preview"] or "（待补充：从 README / 业务需求整理）"
        pkg_line = probe["package_description"] or probe["package_name"] or "（待补充）"
        deps_line = "、".join(probe["dependencies"]) or "（待补充）"
        scripts_line = "、".join(probe["scripts"]) or "（待补充）"
        configs_line = "、".join(probe["config_files"]) or "（无）"

        tree_rows = "\n".join(
            f"| `{rel}` | {kind} |" for rel, kind in probe["src_tree"]
        ) or "| （待补充） | |"

        agents = (
            f"# {name} — 项目速览\n\n"
            "> 本文件由 Codex Context Server V2 初始化生成，内容为基于仓库现状的初始摘要；请随项目演进人工维护。\n\n"
            "## 目标与边界\n\n"
            f"{summary}\n\n"
            "## 技术栈\n\n"
            f"- 项目描述：{pkg_line}\n"
            f"- 主要依赖：{deps_line}\n"
            f"- 常用脚本：{scripts_line}\n\n"
            "## 目录与核心模块\n\n"
            "| 路径 | 类型 |\n| --- | --- |\n"
            f"{tree_rows}\n\n"
            "## 文档索引\n\n"
            "| 文档 | 作用 |\n| --- | --- |\n"
            "| docs/architecture.md | 系统架构与运行期结构 |\n"
            "| docs/modules.md | 模块清单与职责边界 |\n"
            "| docs/design-system.md | UI 规范与设计系统 |\n"
            "| docs/decisions.md | 重大技术决策记录 |\n"
            "| docs/changelog.md | 变更日志 |\n\n"
            "## Codex 工作方式\n\n"
            "1. 开始任务先读本文件与 `.codex/workflow.md`。\n"
            "2. 使用符号索引定位模块与调用关系，不要全仓扫描。\n"
            "3. 业务变更检查数据模型、权限、部署等相邻影响。\n"
            "4. 重大技术决策记录到 docs/decisions.md。\n"
        )

        architecture = (
            f"# {name} 系统架构\n\n"
            "> 本文件由 Codex Context Server V2 初始化，当前为占位，请补充运行期整体结构。\n\n"
            "## 一、目标与边界\n\n（待补充）\n\n"
            "## 二、运行期整体结构\n\n（待补充）\n\n"
            "## 三、关键数据流\n\n（待补充）\n"
        )

        modules = (
            "# 核心模块\n\n"
            "> 仅列当前文件清单与职责边界；本文件以导航为主。\n\n"
            "## 模块一览\n\n"
            "| 路径 | 类型 |\n| --- | --- |\n"
            f"{tree_rows}\n\n"
            "（初始化器从仓库结构生成初始行，请人工补全职责）\n"
        )

        design = (
            "# 设计系统 / UI 规范\n\n"
            "> 本文件由 Codex Context Server V2 初始化，当前为占位。\n\n"
            "## 原则\n\n（待补充）\n\n"
            "## 组件与样式\n\n（待补充）\n\n"
            "## 页面规范\n\n（待补充）\n"
        )

        decisions = (
            "# 重大技术决策记录\n\n"
            "## 模板\n\n"
            "### 决策：<标题>\n"
            "- **日期**：YYYY-MM-DD\n"
            "- **背景**：\n"
            "- **选择方案**：\n"
            "- **为什么放弃其他方案**：\n\n"
            "## 决策记录\n\n（暂无，按模板追加）\n"
        )

        changelog = (
            "# 变更日志\n\n"
            "> 每次发版或重要行为变更追加一条记录，最新记录放在最上方。\n\n"
            "## 模板\n\n"
            "### [YYYY-MM-DD] <版本/标题>\n"
            "- 类别：feature | fix | refactor | docs\n"
            "- 变更摘要：\n"
            "- 影响范围：\n"
            "- 备注：\n"
        )

        workflow = (
            "# 工作流\n\n"
            "> 由 Codex Context Server V2 初始化，团队可按需调整。\n\n"
            "## 1. 开始\n"
            "- 先读 AGENTS.md 与本文件。\n"
            "- 用符号查询定位模块，不做全仓扫描。\n\n"
            "## 2. 修改\n"
            "- 最小影响文件，复用已有组件与状态流。\n"
            "- 明确每个变更的验证方式。\n\n"
            "## 3. 验证\n"
            "- 运行相关测试与构建。\n"
            "- 回归相邻模块。\n\n"
            "## 4. 记录\n"
            "- 重大技术决策追加到 docs/decisions.md。\n"
            "- 行为变更更新 docs/changelog.md。\n"
        )

        plan = [
            ("AGENTS.md", agents),
            ("docs/architecture.md", architecture),
            ("docs/modules.md", modules),
            ("docs/design-system.md", design),
            ("docs/decisions.md", decisions),
            ("docs/changelog.md", changelog),
            (".codex/workflow.md", workflow),
        ]
        files = []
        for rel, content in plan:
            target = resolve_safe_path(root, rel)
            # 不安全的现有 symlink 视为 skip，create() 不得覆盖其目标。
            exists = target is None or target.exists()
            files.append({
                "path": rel,
                "action": "skip" if exists else "create",
                "content_preview": content[:300],
                "content": content,
            })
        return files
