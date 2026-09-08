# Stops the running listener. Stop-ScheduledTask kills the whole action's
# process tree (wrapper + child python) via its Job Object, which is the
# normal path; the PID-file kill below is just a safety net in case a
# python process is orphaned outside that job for any reason.

$ErrorActionPreference = "SilentlyContinue"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile  = Join-Path $repoRoot "system\logs\listener.pid"

Stop-ScheduledTask -TaskName "EKKO Listener"

if (Test-Path $pidFile) {
    $procId = Get-Content $pidFile
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "python") {
        Write-Host "Found lingering python process (PID $procId), stopping it."
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Path $pidFile -ErrorAction SilentlyContinue
}

Write-Host "Stopped. system\status.ps1 to confirm."
