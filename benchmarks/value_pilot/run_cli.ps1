param(
    [Parameter(Mandatory=$true)][string]$TaskId,
    [Parameter(Mandatory=$true)][string]$Arm
)

# Runs one Pilot run via `codex exec` as a detached background process.
# Output and metadata are written to the run workspace so the parent agent can
# poll progress without owning the process lifetime.

$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$ws = Join-Path (Join-Path $repo "benchmarks\results\pilot-runs") "$TaskId\$Arm"
$start = Get-Date
$promptPath = Join-Path $ws "prompt.txt"
$metaPath = Join-Path $ws "run_meta_cli.txt"
$procPid = $PID

"[meta] start=$($start.ToString('o')) pid=$procPid" | Out-File -FilePath $metaPath -Encoding utf8

$p = Get-Content -Raw -LiteralPath $promptPath
$output = codex exec -C $ws -s danger-full-access $p 2>&1
$exit = $LASTEXITCODE
$end = Get-Date

Add-Content -Path $metaPath -Value "[meta] end=$($end.ToString('o')) exit=$exit wall_sec=$([math]::Round(($end-$start).TotalSeconds,1))" -Encoding utf8
$output | Out-File -FilePath (Join-Path $ws "run.log") -Encoding utf8
