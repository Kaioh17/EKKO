# Cycles the listener: stop.ps1 then start.ps1, with a popup
# (restart_ui.py) shown for the duration and every attempt logged to
# system\logs\restart.log. Exists because the listener has been observed
# to work best fresh -- this is the scheduled, unattended version of
# "turn it off and on again," wired up by install_restart_task.ps1 to
# run every few hours rather than needing a manual system\stop.ps1 +
# system\start.ps1.
#
# Safe to run manually too: system\restart.ps1

$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$systemDir  = $PSScriptRoot
$logDir     = Join-Path $systemDir "logs"
$restartLog = Join-Path $logDir "restart.log"
$pythonw    = Join-Path $repoRoot "pvenv\Scripts\pythonw.exe"
$ui         = Join-Path $systemDir "restart_ui.py"
$pidFile    = Join-Path $logDir "listener.pid"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Restart-Log($message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
    Add-Content -Path $restartLog -Value $line
    Write-Host $line
}

Write-Restart-Log "Restart sequence starting."

$uiProc = $null
if (Test-Path $pythonw) {
    try {
        $uiProc = Start-Process -FilePath $pythonw -ArgumentList "`"$ui`"", "sequential restart..." -PassThru
    } catch {
        Write-Restart-Log ("Popup failed to launch (non-fatal): {0}" -f $_.Exception.Message)
    }
} else {
    Write-Restart-Log "pythonw.exe not found, skipping popup."
}

try {
    & (Join-Path $systemDir "stop.ps1") | Out-Null
    Write-Restart-Log "Stopped."

    Start-Sleep -Seconds 2

    & (Join-Path $systemDir "start.ps1") | Out-Null
    Write-Restart-Log "Start requested."

    # Give the wrapper a few seconds to spin the process back up, then
    # confirm from the PID file rather than just trusting the request
    # succeeded -- same check status.ps1 does.
    Start-Sleep -Seconds 5
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq "python") {
            Write-Restart-Log ("Confirmed running, PID {0}." -f $procId)
        } else {
            Write-Restart-Log "WARNING: PID file present but process not alive after restart."
        }
    } else {
        Write-Restart-Log "WARNING: no PID file after restart -- listener may not have come back up."
    }
} finally {
    if ($uiProc -and -not $uiProc.HasExited) {
        Stop-Process -Id $uiProc.Id -Force -ErrorAction SilentlyContinue
    }
}

Write-Restart-Log "Restart sequence complete."
