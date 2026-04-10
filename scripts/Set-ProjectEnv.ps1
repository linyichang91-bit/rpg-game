$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$nodeDir = Join-Path $repoRoot ".tools\node"
$pythonDir = Join-Path $repoRoot ".tools\python311"
$venvScripts = Join-Path $repoRoot ".venv\Scripts"

chcp 65001 > $null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NPM_CONFIG_CACHE = Join-Path $repoRoot ".npm-cache"
$env:Path = "$venvScripts;$pythonDir;$nodeDir;$env:Path"

Set-Location $repoRoot

Write-Host "Project environment loaded with UTF-8 output."
