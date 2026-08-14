# Context MCP 项目上下文规则

在执行项目开发、排障、审查或文档任务前，先判断是否真的需要项目知识；不需要时直接工作，不要机械调用 Context MCP。

## 需要项目知识时（candidate-first，默认）

1. 确认项目根目录的绝对路径；不要把 `src`、`app` 等子目录当作根。
2. 任务可能依赖项目知识（陌生 repo、架构/约定、跨多文档的规则、恢复设计决策），但不知道具体该读哪些文档时，先调用 `get_context_candidates(project_path, task)`，只取 metadata（path、title、summary、role、rank、reasons）。
3. 根据 task 与候选 metadata，只读当前任务真正需要的候选文档，读一两份正常，不必读完所有候选；metadata 已足够时直接继续。
4. 用 Serena 或 Trae 自身的代码导航和源码/测试验证行为；Context MCP 索引只用于缩小范围，不当作源码。

## 直接工作（不需要 Context MCP）

- 用户给出确切文件路径或函数名；
- 局部小修或明确函数级 bug；
- 唯一相关文件已知；
- 简单命令/用法问题；
- 与本仓库文档无关的通用任务。

## Legacy 全量路径（仅兼容）

`get_project_context` 与 `get_context_pack` 仍受支持，但不再是默认首步。只有完整 pack 确实必需、旧工具链依赖 legacy 接口、或 `get_context_candidates` 不可用/报错时才使用。

## 其他场景

- 任务可能对应多个项目：先调用 `suggest_projects`，返回候选和原因，不自动选择。
- 项目刚新增或修改 `context-manifest.yaml`：先调用 `scan_project(force=true)`；索引过期同理。
- 普通上下文加载只读，不创建文档、不修改业务代码；Context MCP 不可用时继续用项目正常 AGENTS.md/docs 流程，不虚构结果。
- 只有用户明确确认后，才调用 `initialize_project_context(dry_run=false)`；初始化前必须先展示 `dry_run=true` 预览。
- 完成代码变更后，需要影响分析时调用 `analyze_changes`，只分析，不自动更新文档。
