# AGENTS.md

Repo: Context MCP server source, the `project-context` skill distribution, and its benchmark suite.

Project-knowledge tasks — unfamiliar modules, architecture or configuration rules, design decisions spread across docs — route through the `project-context` skill first, so `get_context_candidates` narrows which docs to read before coding. Work direct when the task is self-contained: an exact file or function is given, the fix is localized, the one relevant file is known, or it is a simple command question. Follow the skill's two-stage workflow once triggered.
