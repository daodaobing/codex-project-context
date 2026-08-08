[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$CodexHome = "",
    [switch]$SkipPip,
    [switch]$SkipConfig
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    $CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
}
$InstallRoot = Join-Path $CodexHome "context-server"
$SkillRoot = Join-Path $CodexHome "skills"
$VenvRoot = Join-Path $InstallRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"

New-Item -ItemType Directory -Path $CodexHome, $InstallRoot, $SkillRoot -Force | Out-Null

foreach ($item in @("server.py", "requirements.txt", "config", "context", "scanners", "skills")) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot $item) -Destination $InstallRoot -Recurse -Force
}

$SkillSource = Join-Path $SourceRoot "skill\project-context"
$SkillTarget = Join-Path $SkillRoot "project-context"
New-Item -ItemType Directory -Path $SkillTarget -Force | Out-Null
Copy-Item -LiteralPath $SkillSource -Destination $SkillRoot -Recurse -Force

if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $Python = $pythonCommand.Source }
}
if ([string]::IsNullOrWhiteSpace($Python) -or -not (Test-Path -LiteralPath $Python)) {
    throw "找不到 Python。请安装 Python 3.10+，或使用 -Python 指定 python.exe。"
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -m venv $VenvRoot
    if ($LASTEXITCODE -ne 0) { throw "创建 Python 虚拟环境失败。" }
}
if (-not $SkipPip) {
    & $VenvPython -m pip install -r (Join-Path $SourceRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "安装 Context MCP 依赖失败。" }
}

if (-not $SkipConfig) {
    $ConfigPath = Join-Path $CodexHome "config.toml"
    $serverPath = Join-Path $InstallRoot "server.py"
    $commandPath = $VenvPython
    if ($commandPath.Contains("'") -or $serverPath.Contains("'")) {
        throw "路径包含单引号，无法安全写入 TOML；请改用不含单引号的安装路径。"
    }
    $entry = @"
[mcp_servers.context_server]
command = '$commandPath'
args = ['$serverPath']
startup_timeout_sec = 60.0
"@.Trim()

    if (Test-Path -LiteralPath $ConfigPath) {
        $config = Get-Content -LiteralPath $ConfigPath -Raw
    } else {
        $config = ""
    }
    $pattern = '(?ms)^\[mcp_servers\.context_server\]\r?\n.*?(?=^\[|\z)'
    if ([regex]::IsMatch($config, $pattern)) {
        $config = [regex]::Replace($config, $pattern, $entry + "`r`n")
    } else {
        if ($config.Length -gt 0 -and -not $config.EndsWith("`n")) { $config += "`r`n" }
        $config += "`r`n" + $entry + "`r`n"
    }
    Set-Content -LiteralPath $ConfigPath -Value $config -Encoding utf8
}

Write-Host "Context MCP 安装完成。"
Write-Host "Server: $InstallRoot\server.py"
Write-Host "Skill:  $SkillTarget\SKILL.md"
Write-Host "请重启 Codex；Trae 请将 mcp-configs\trae.windows.json 中的路径复制到 MCP 配置。"
