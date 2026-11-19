# Cross-platform Windows PowerShell entry point for Evolving Profile 6.1+
# Uses `uv` to auto-provision Python 3.11+ without touching system Python (e.g. 3.8).
param(
    [switch]$DryRun,
    [switch]$SkipDeps,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Error "uv is required on Windows to auto-provision Python 3.11+. Install via: powershell -ExecutionPolicy ByPass -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    exit 1
}

$pyArgs = @("run", "--python", "3.11", (Join-Path $Root "scripts\install.py"), "--mode", "local")
if ($DryRun) { $pyArgs += "--dry-run" }
if ($SkipDeps) { $pyArgs += "--skip-deps" }
if ($NoLaunch) { $pyArgs += "--no-launch" }

& $uv.Source @pyArgs
exit $LASTEXITCODE
