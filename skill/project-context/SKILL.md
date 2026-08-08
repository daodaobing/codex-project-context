---
name: project-context
description: "Route Codex through the global Context MCP before work on an existing or new project. Use when a task names a project path, asks to load project context, needs relevant AGENTS.md/docs/modules, needs cross-project candidate selection, needs bootstrap preview, or needs change-impact analysis."
---

# Project Context

Use the globally registered Context MCP Server to select task-relevant project context before coding. Keep the workflow lightweight and confirmation-gated: read/index first, preview before any bootstrap write, and never replace AGENTS.md, docs, or Serena.

## Standard workflow

1. Resolve the project root. Require an absolute `project_path`; do not pass `src`, `app`, or another subdirectory as the project root.
2. For a normal task, call `get_context_pack(project_path, task)` when skill recommendations are useful; otherwise call `get_project_context(project_path, task)`. Use the returned files, summaries, and recommended modules to decide what to read next, then use Serena for symbol-level code navigation.
3. Before editing, report the matched context, likely impact files, and a minimal plan when the user has requested a plan or confirmation gate.
4. Do not create or modify project files during ordinary context loading. The context and analysis tools are read/index operations.

## Existing projects

If the project already has AGENTS.md/docs, call `get_project_context` or `get_context_pack`. On the first path access the server scans automatically. If AGENTS.md, docs, or a manifest changed after indexing, call `scan_project(project_path, force=true)` first.

## New-project bootstrap

Only bootstrap when the user explicitly asks to initialize or create missing context files.

1. Call `initialize_project_context(project_path, dry_run=true)` and show the preview.
2. Stop and wait for explicit confirmation before writing.
3. After confirmation, call `initialize_project_context(project_path, dry_run=false)`.
4. Call `scan_project(project_path, force=true)` after creation.

The bootstrap may create missing `AGENTS.md`, `docs/architecture.md`, `docs/modules.md`, `docs/design-system.md`, `docs/decisions.md`, `docs/changelog.md`, and `.codex/workflow.md`. It skips existing files, does not overwrite them, and does not create `context-manifest.yaml` automatically.

## Manifest-aware projects

If the project root contains `context-manifest.yaml`, prefer its declared `project.type`, `modules`, `knowledge_domains`, `documents`, `decisions`, `paths`, and `keywords`. Keep `modules` for real code modules and use `knowledge_domains` for document-led business areas. After adding or changing a manifest, refresh with `scan_project(force=true)`.

## Multiple projects

When a task may match more than one project, call `suggest_projects(task, project_paths, limit)` first. Return candidates and reasons; do not automatically choose a project. After the user or task context identifies the target, call `get_project_context` for that project.

## Change analysis

When asked what a change affects, call `analyze_changes(project_path)`. Treat its output as a read-only suggestion: it maps changed files to modules and related docs but never updates them automatically.

## Guardrails

- Do not read every document by default; load the returned relevant files and summaries first.
- Do not treat the Context MCP index as source code; use Serena and the project files for implementation truth.
- Do not modify business code, AGENTS.md, docs, or manifests unless the user explicitly authorizes that change.
- If the MCP server is unavailable, report that and continue with the project's normal AGENTS.md/docs workflow; do not invent Context MCP results.
