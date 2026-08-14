---
name: project-context
description: "Route Codex through the Context MCP for project-knowledge tasks. Prefer get_context_candidates: discover a small metadata-only candidate set, then read only the needed docs. Use when a task names a project path or needs project docs/AGENTS.md before coding."
---

# Project Context

Use the globally registered Context MCP Server to restore task-relevant project knowledge before coding. Keep the workflow lightweight: discover candidates first, read only what the task needs, then verify against source. Never replace AGENTS.md, docs, or Serena.

## When to use this skill

Consider it when the task is likely to depend on project-specific knowledge:

- unfamiliar project structure, or a repository you have not worked in yet;
- architecture, configuration, or naming conventions that must be respected;
- rules or design decisions spread across multiple docs;
- a request that is too vague to locate the right code directly;
- a change that should follow an existing design before editing.

Skip it and work directly when the task is self-contained:

- the user gives an exact file path or function name;
- a small, localized fix or a clear function-level bug;
- the unique relevant file is already known;
- a simple command or usage question;
- a general task unrelated to this repository's documentation.

## Two-stage workflow

### Stage 1 — Candidate discovery

When project knowledge may be needed but it is unclear which documents matter, call `get_context_candidates(project_path, task)`. It returns metadata only (path, title, summary, role, rank, reasons) for a small set of candidate documents. This call narrows the search space; it is not a full context load and not a document read.

### Stage 2 — Selective reading

Read only the candidate documents the task actually needs, judged from the task plus each candidate's path, title, summary, role, and reasons. Reading one or two documents is normal; reading every candidate is not required. If the metadata already gives enough direction, continue straight to source verification.

Source and tests remain the final truth. Documents narrow the search and restore design context, but always confirm behavior against the current code.

## Decision model

1. Do I already have enough context? (exact file or function given, small change in known code) -> work directly.
2. Does the task depend on project-specific knowledge rather than pure code logic? No -> work directly.
3. Do I already know exactly which document or file to read? Yes -> read it directly.
4. Otherwise -> `get_context_candidates(project_path, task)`, read only the chosen candidates, then verify in source and tests.

## Full context (legacy path)

`get_project_context(project_path, task)` returns the full matched context (relevant docs, summaries, module paths). `get_context_pack(project_path, task)` aggregates that result with skill recommendations. Both remain supported for compatibility, but they are no longer the default first step. Use them only when:

- the workflow genuinely needs the complete context pack at once;
- existing tooling already depends on the legacy interface;
- `get_context_candidates` is unavailable or returns an error.

Resolve the project root first. Require an absolute `project_path`; do not pass `src`, `app`, or another subdirectory as the project root. On first access of a path the server scans automatically. If AGENTS.md, docs, or a manifest changed after indexing, call `scan_project(project_path, force=true)` first.

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

When a task may match more than one project, call `suggest_projects(task, project_paths, limit)` first. Return candidates and reasons; do not automatically choose a project. After the user or task context identifies the target, use the two-stage workflow for that project.

## Change analysis

When asked what a change affects, call `analyze_changes(project_path)`. Treat its output as a read-only suggestion: it maps changed files to modules and related docs but never updates them automatically.

## Guardrails

- Do not call the context tools for simple, localized, or already-scoped tasks.
- A candidate pack is for narrowing only; do not mechanically read every candidate.
- Do not treat the Context MCP index as source code; verify against the actual files, configuration, and tests.
- Do not modify business code, AGENTS.md, docs, or manifests unless the user explicitly authorizes that change.
- If the MCP server is unavailable, continue with the project's normal AGENTS.md/docs workflow; do not invent Context MCP results.
