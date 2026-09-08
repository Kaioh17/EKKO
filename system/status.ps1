# Quick health check: task state, whether the listener process is
# actually alive, the current execution mode, and a tail of the logs.

$ErrorActionPreference = "SilentlyContinue"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile  = Join-Path $repoRoot "system\logs\listener.pid"
$outLog   = Join-Path $repoRoot "system\logs\listener.out.log"
$errLog   = Join-Path $repoRoot "system\logs\listener.err.log"
$flagsFile = Join-Path $repoRoot "system\listener.flags.txt"

Write-Host "=== Scheduled task ==="
$task = Get-ScheduledTask -TaskName "EKKO Listener"
if (-not $task) {
    Write-Host "Not registered. Run system\install_task.ps1 first."
} else {
    $info = Get-ScheduledTaskInfo -TaskName "EKKO Listener"
    Write-Host ("State: {0}" -f $task.State)
    Write-Host ("Last run: {0}   Last result: {1}" -f $info.LastRunTime, $info.LastTaskResult)
    Write-Host ("Next run: {0}" -f $info.NextRunTime)
}

Write-Host ""
Write-Host "=== Listener process ==="
if (Test-Path $pidFile) {
    $procId = Get-Content $pidFile
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq "python") {
        Write-Host ("Running, PID {0}, started {1}, CPU {2:N1}s" -f $procId, $proc.StartTime, $proc.CPU)
    } else {
        Write-Host ("PID file points at {0} but that's not a live python process (stale)." -f $procId)
    }
} else {
    Write-Host "No PID file -- not running (or wrapper hasn't started it yet)."
}

Write-Host ""
Write-Host "=== Execution mode (system\listener.flags.txt) ==="
if (Test-Path $flagsFile) {
    Get-Content $flagsFile | Where-Object { $_ -and -not $_.StartsWith("#") }
} else {
    Write-Host "(no flags file -- running with defaults)"
}

Write-Host ""
Write-Host "=== Last 20 lines: listener.out.log ==="
if (Test-Path $outLog) { Get-Content $outLog -Tail 20 } else { Write-Host "(no log yet)" }

if ((Test-Path $errLog) -and (Get-Item $errLog).Length -gt 0) {
    Write-Host ""
    Write-Host "=== Last 20 lines: listener.err.log ==="
    Get-Content $errLog -Tail 20
}

Write-Host ""
Write-Host "=== Prune task ==="
$pruneTask = Get-ScheduledTask -TaskName "EKKO Prune Captures"
if (-not $pruneTask) {
    Write-Host "Not registered. Run system\install_prune_task.ps1 to prune listener\captures\ automatically."
} else {
    $pruneInfo = Get-ScheduledTaskInfo -TaskName "EKKO Prune Captures"
    Write-Host ("State: {0}   Last run: {1}   Last result: {2}   Next run: {3}" -f `
        $pruneTask.State, $pruneInfo.LastRunTime, $pruneInfo.LastTaskResult, $pruneInfo.NextRunTime)
    $pruneLog = Join-Path $repoRoot "system\logs\prune.log"
    if (Test-Path $pruneLog) {
        Write-Host "Last prune.log entries:"
        Get-Content $pruneLog -Tail 6
    }
}

Write-Host ""
Write-Host "=== Restart task ==="
$restartTask = Get-ScheduledTask -TaskName "EKKO Restart"
if (-not $restartTask) {
    Write-Host "Not registered. Run system\install_restart_task.ps1 to restart the listener every 3 hours."
} else {
    $restartInfo = Get-ScheduledTaskInfo -TaskName "EKKO Restart"
    Write-Host ("State: {0}   Last run: {1}   Last result: {2}   Next run: {3}" -f `
        $restartTask.State, $restartInfo.LastRunTime, $restartInfo.LastTaskResult, $restartInfo.NextRunTime)
    $restartLog = Join-Path $repoRoot "system\logs\restart.log"
    if (Test-Path $restartLog) {
        Write-Host "Last restart.log entries:"
        Get-Content $restartLog -Tail 6
    }
}

Write-Host ""
Write-Host "For a live, continuously updating view instead of this snapshot: system\dev.ps1"
