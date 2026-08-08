# Permissions and boundaries

| Operation | Default behavior |
| --- | --- |
| Read project metadata | Allowed for the path supplied by the client |
| Write server index | Allowed under the installed Context MCP directory |
| Write project context files | Only explicit bootstrap with `dry_run=false` |
| Modify business code | Not performed by this server |
| Git commit/push | Not performed by this server |
| Network/API credentials | Not required by the server |

The MCP client, not the server, owns the final authorization decision for tool calls. Installers modify the local Codex configuration only on the machine where the installer is run.
