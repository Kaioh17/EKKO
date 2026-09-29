<#
.SYNOPSIS
    Opens Windows Task Manager.

.DESCRIPTION
    Handler script for EKKO's (future) intent routing layer. Standalone
    on purpose: no dependency on the listener or intent matcher, so it
    can be tested and run directly.

    taskmgr.exe is a built-in Windows binary, always on PATH, so this
    needs no path discovery. If Task Manager is already open, Windows
    itself brings the existing window to the foreground rather than
    spawning a second instance, so no "already running" check is needed
    here (unlike open_ghelper.ps1).

.EXIT CODES
    0 - launched successfully
    1 - launch failed
#>

$ErrorActionPreference = 'Stop'

try {
    Start-Process -FilePath 'taskmgr.exe'
    Write-Output 'Task Manager opened.'
    exit 0
}
catch {
    Write-Error "Failed to open Task Manager: $_"
    exit 1
}
