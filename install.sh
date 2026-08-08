#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
INSTALL_ROOT="$CODEX_HOME/context-server"
SKILL_ROOT="$CODEX_HOME/skills"
VENV_ROOT="$INSTALL_ROOT/.venv"

mkdir -p "$INSTALL_ROOT" "$SKILL_ROOT"
for item in server.py requirements.txt config context scanners skills; do
  cp -R "$SOURCE_ROOT/$item" "$INSTALL_ROOT/"
done
rm -rf "$SKILL_ROOT/project-context"
cp -R "$SOURCE_ROOT/skill/project-context" "$SKILL_ROOT/project-context"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || command -v python || true)}"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "找不到 Python 3.10+，请设置 PYTHON_BIN。" >&2
  exit 1
fi

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_ROOT"
fi
"$VENV_ROOT/bin/python" -m pip install -r "$SOURCE_ROOT/requirements.txt"

CONFIG_PATH="$CODEX_HOME/config.toml"
mkdir -p "$CODEX_HOME"
touch "$CONFIG_PATH"
if ! grep -q '^\[mcp_servers\.context_server\]' "$CONFIG_PATH"; then
  cat >> "$CONFIG_PATH" <<EOF

[mcp_servers.context_server]
command = '$VENV_ROOT/bin/python'
args = ['$INSTALL_ROOT/server.py']
startup_timeout_sec = 60.0
EOF
fi

echo "Context MCP 安装完成：$INSTALL_ROOT/server.py"
echo "Skill 安装完成：$SKILL_ROOT/project-context/SKILL.md"
echo "请重启 Codex；Trae 请手动复制 mcp-configs 中的配置。"
