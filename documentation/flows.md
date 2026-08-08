# Flows

## Context lookup

Actor: Codex/Trae. Input: absolute project root and task. The server may read project metadata and write only its own local index. It returns context; the client decides what files to read and whether to edit code.

## Bootstrap

`initialize_project_context(dry_run=true)` reads a bounded set of project files and returns a preview. Only an explicit `dry_run=false` call creates missing AGENTS/docs/workflow files. Existing files are skipped.

## Change analysis

`analyze_changes` reads `git status --porcelain` and maps changed paths to modules and documents. It does not update documents.

## Multi-project suggestion

`suggest_projects` scores supplied or indexed projects and returns candidates with reasons. It never auto-selects a project.
