# Codex Project Context MCP

一个可迁移的全局 MCP Server + `project-context` 技能包。

它根据“项目路径 + 当前任务”选择需要读取的 `AGENTS.md`、`docs`、模块路径和决策记录，减少每次任务重复加载整套项目文档。

## 包含内容

```text
server.py                         MCP stdio 入口
config/settings.json              规则与扫描配置
scanners/                         项目扫描、manifest、匹配、变更分析
context/                          Context Pack 聚合
skills/                           MCP 内置技能索引与推荐实现
skill/project-context/            Codex/兼容客户端的触发技能
install.ps1                       Windows 安装器
install.sh                        macOS/Linux 安装器
mcp-configs/                      Trae/Codex 配置示例
tests/                            无业务项目依赖的端到端测试
documentation/                    架构、变量、权限和测试说明
```

不包含：项目源码、项目索引、技能索引、虚拟环境、密钥、令牌和本机路径。

## Windows 安装

```powershell
git clone https://github.com/daodaobing/codex-project-context.git
cd codex-project-context
.\install.ps1
```

安装器会：

1. 在 `~/.codex/context-server` 安装 MCP 运行时；
2. 创建独立 Python 虚拟环境并安装 `requirements.txt`；
3. 将 `project-context` 安装到 `~/.codex/skills/project-context`；
4. 在 `~/.codex/config.toml` 注册 `context_server`；
5. 保留运行时生成的项目索引，不把索引上传到 Git。

需要指定 Python 时：

```powershell
.\install.ps1 -Python "C:\Path\To\python.exe"
```

如果只想安装文件、不自动更新 Codex 配置：

```powershell
.\install.ps1 -SkipConfig
```

安装后重启 Codex。验证：

```powershell
codex mcp
& "$env:USERPROFILE\.codex\context-server\.venv\Scripts\python.exe" `
  "$env:USERPROFILE\.codex\context-server\tests\test_server.py"
```

## macOS/Linux 安装

```bash
git clone https://github.com/daodaobing/codex-project-context.git
cd codex-project-context
./install.sh
```

也可以通过 `CODEX_HOME` 指定 Codex 配置根目录：

```bash
CODEX_HOME="$HOME/.codex" ./install.sh
```

## Trae 使用

MCP Server 是跨客户端的稳定部分。把下面配置复制到 Trae 的 MCP 管理面板，并将 `YOUR_USER` 替换为当前用户名：

```json
{
  "mcpServers": {
    "context_server": {
      "command": "C:\\Users\\YOUR_USER\\.codex\\context-server\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\YOUR_USER\\.codex\\context-server\\server.py"
      ]
    }
  }
}
```

完整示例见 `mcp-configs/trae.windows.json`。Trae 不同版本的配置入口和 `SKILL.md` 自动发现方式可能不同；如果版本支持项目级技能，将 `skill/project-context` 复制到项目的 `.trae/skills/project-context`，或通过 Trae 的技能/规则界面导入。若当前版本不识别 `SKILL.md`，将 `trae/project-context.rules.md` 复制到 Trae 的项目规则中即可。即使不安装技能，只配置 MCP 也可以直接使用所有 Context Tool。

## 使用方式

普通项目任务：

```text
$project-context

项目路径：C:\Projects\MyProject

任务：修改登录页面并修复 token 过期问题。
```

技能会要求 Codex/兼容客户端先调用 `get_project_context` 或 `get_context_pack`，再使用 Serena 或客户端自身的代码导航能力定位实现。

新项目初始化：

```text
$project-context

项目路径：C:\Projects\NewProject

请先预览初始化上下文文件，只读，不写入。
```

只有得到明确确认后，才调用 `initialize_project_context(..., dry_run=false)`。普通上下文加载不会创建或修改项目文件。

## `context-manifest.yaml`

在项目根目录声明项目主动维护的上下文：

```yaml
project:
  name: My Project
  type: code

modules:
  auth:
    paths:
      - src/core/auth
    documents:
      - docs/contracts/auth.md
    decisions:
      - docs/decisions.md
    keywords:
      - auth
      - login

knowledge_domains:
  billing_rules:
    documents:
      - docs/billing-prd.md
    keywords:
      - billing
      - 计费
```

`modules` 只放真实代码模块；没有代码路径的业务知识放 `knowledge_domains`。新增或修改 manifest 后，调用 `scan_project(project_path, force=true)` 刷新索引。

## Tool

核心 Tool：

- `get_project_context`
- `get_context_pack`
- `scan_project`
- `list_projects`
- `get_project_index`
- `initialize_project_context`
- `suggest_projects`
- `analyze_changes`

技能推荐辅助 Tool：

- `refresh_skill_registry`
- `list_skills`
- `get_skill`
- `recommend_skills`

## 设计边界

- 不替代 `AGENTS.md`、项目 docs 或 Serena；
- 第一版规则匹配，不依赖向量数据库、Embedding 或 Agent 框架；
- 索引只存路径、摘要、分类和模块关系，不存源码；
- `analyze_changes` 只分析，不自动更新文档；
- `initialize_project_context` 默认 dry-run，写入必须经过确认；
- Server 通过 MCP stdio 运行，不需要公网服务或 API Key。

## 开发验证

```powershell
& .\.venv\Scripts\python.exe tests\test_server.py
```

更详细的架构、变量、权限边界和测试证据见 `documentation/`。

## License

Apache-2.0，详见 [LICENSE](LICENSE)。
