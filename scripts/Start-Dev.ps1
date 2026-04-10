$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendLog = Join-Path $repoRoot "backend.log"
$frontendLog = Join-Path $repoRoot "frontend.log"
$envScript = Join-Path $PSScriptRoot "Set-ProjectEnv.ps1"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$nodeExe = Join-Path $repoRoot ".tools\node\node.exe"
$nextDirectScript = Join-Path $PSScriptRoot "start-next-direct.cjs"

foreach ($logFile in @($backendLog, $frontendLog)) {
  if (Test-Path $logFile) {
    Remove-Item -LiteralPath $logFile -Force
  }
}

$backendCommand = "& '$envScript'; & '$venvPython' -m uvicorn server.api.app:app --host 127.0.0.1 --port 8000 *>> '$backendLog'"
$frontendCommand = "& '$envScript'; & '$nodeExe' '$nextDirectScript' *>> '$frontendLog'"

Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoLogo",
  "-ExecutionPolicy", "Bypass",
  "-Command", $backendCommand
) -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null

Start-Sleep -Seconds 2

Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoLogo",
  "-ExecutionPolicy", "Bypass",
  "-Command", $frontendCommand
) -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null

Write-Host "Backend log: $backendLog"
Write-Host "Frontend log: $frontendLog"
Write-Host "Frontend URL: http://127.0.0.1:3000"
Write-Host "Backend health: http://127.0.0.1:8000/health"
