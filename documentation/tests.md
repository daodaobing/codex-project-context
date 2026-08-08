# Test coverage

## Existing coverage

`tests/test_server.py` runs a portable stdio smoke test using only repository fixtures. It checks:

- MCP initialization and core Tool registration;
- bootstrap preview and confirmed creation in a temporary project;
- manifest-aware project context lookup;
- Context Pack structure;
- multi-project candidate output with `auto_selected=false`;
- git change analysis without document writes.

## Proposed tests

- Automated: run the smoke test on Windows, macOS, and Linux in CI.
- Automated: validate the installer in a disposable home directory.
- Manual: configure the generated MCP entry in Trae and call `list_projects`.

## Gaps

- No live test can verify every host client's skill discovery convention.
- No test runs against a user's private projects; those must remain local and outside the repository.
