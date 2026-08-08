# Variables and secrets

| Variable/config | Used by | Source | Secret | Notes |
| --- | --- | --- | --- | --- |
| `CODEX_HOME` | `install.ps1`, `install.sh` | Environment, optional | No | Overrides the default `~/.codex` location. |
| `PYTHON_BIN` | `install.sh` | Environment, optional | No | Selects the Python executable. |
| `CONTEXT_SERVER_SKILL_ROOTS` | Skill registry | Environment, optional | No | Test or custom skill roots; otherwise portable settings are used. |
| `CONTEXT_SERVER_SKILL_INDEX` | Skill registry | Environment, optional | No | Test or custom registry path; otherwise local `index/`. |

The server does not require API keys, OAuth tokens, database passwords, SSH credentials, or signing keys. Do not place project secrets in `context-manifest.yaml` or indexed documents.
