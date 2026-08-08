# Architecture

## Purpose

Codex Project Context MCP is a local stdio MCP Server. It routes a task to a small set of relevant project documents and module paths.

## Components

- `server.py`: FastMCP tool registration and index lifecycle.
- `scanners/`: project discovery, manifest parsing, keyword matching, bootstrap preview/create, project suggestion, and git change analysis.
- `context/`: Context Pack aggregation.
- `skills/`: read-only metadata index and task-based skill recommendation.
- `config/settings.json`: portable scan limits and matching rules.
- `index/`: local generated metadata; never committed.

## Data flow

```text
project_path + task
  -> scan or load local index
  -> manifest-first matching, then automatic scan fallback
  -> relevant documents + summaries + module paths
  -> Codex/Trae reads selected files and uses its code navigator
```

The server does not store source code, call an LLM, use embeddings, or modify business code during ordinary context lookup.

## Related documents

- `documentation/flows.md`: tool flows and side effects.
- `documentation/permissions.md`: local filesystem and write boundaries.
- `documentation/variables.md`: configuration and secrets.
- `documentation/tests.md`: verification map.
