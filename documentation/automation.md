# Automation boundary

This package exposes deterministic MCP Tools. It does not contain an autonomous agent, background watcher, scheduled task, embedding service, or automatic code/document modification loop.

The `project-context` skill is an instruction layer for compatible clients. It asks the client to call Context MCP before coding and preserves the preview/confirmation gate for bootstrap writes. The MCP server itself remains the source of tool behavior and safety boundaries.
