# Context MCP 项目上下文规则

执行任何项目开发、排障、审查或文档任务前：

1. 确认项目根目录的绝对路径。
2. 先调用 Context MCP 的 `get_project_context` 或 `get_context_pack`，只加载当前任务相关的 AGENTS.md、docs、模块和决策记录。
3. 如果任务可能对应多个项目，先调用 `suggest_projects`，返回候选和原因，不自动选择。
4. 如果项目刚新增或修改 `context-manifest.yaml`，先调用 `scan_project(force=true)`。
5. 使用 Serena 或 Trae 自身的代码导航定位符号；不要把 Context MCP 索引当作源码。
6. 普通上下文加载只读，不创建文档、不修改业务代码。
7. 只有用户明确确认后，才调用 `initialize_project_context(dry_run=false)`；初始化前必须先展示 `dry_run=true` 预览。
8. 完成代码变更后，需要影响分析时调用 `analyze_changes`，只分析，不自动更新文档。
