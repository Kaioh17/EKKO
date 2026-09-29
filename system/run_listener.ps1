# Wrapper that keeps the ekko backend running headless at logon; the
# backend in turn supervises the voice listener (backend\supervisor.py),
# so the desktop app attaches to this one instead of starting a second.
# Resolves every path off its own location (so it works regardless of cwd
# or how Task Scheduler invokes it), logs to system\logs\, and restarts the
# process with exponential backoff if it ever exits unexpectedly.
#
# Runs the installed app's bundled ekko-backend.exe when given -Exe (data in
# %APPDATA%\io.usemaison.ekko, same as the app), else the repo's pvenv
# (data in the repo). Listener settings come from the app's settings DB;
# the old listener.flags.txt is no longer read. This loop is
# infinite by design -- the only way it stops is the whole process tree
# being killed from outside (system\stop.ps1), not an internal exit path.
#
# Not meant to be run directly day-to-day; system\install_task.ps1 wires
# this into Task Scheduler, system\start.ps1 / stop.ps1 control it.

# -Exe: the installed app's ekko-backend.exe (passed by install_task.ps1,
# which the app runs). Without it, the repo's pvenv is used (development).
param([string]$Exe = "")

$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$systemDir  = $PSScriptRoot
$logDir     = Join-Path $systemDir "logs"
$pythonExe  = Join-Path $repoRoot "pvenv\Scripts\python.exe"

$outLog = Join-Path $logDir "listener.out.log"
$errLog = Join-Path $logDir "listener.err.log"
$wrapperLog = Join-Path $logDir "listener.wrapper.log"
$pidFile = Join-Path $logDir "listener.pid"

$maxLogBytes = 10MB

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Rotate-LogIfLarge($path) {
    if ((Test-Path $path) -and (Get-Item $path).Length -gt $maxLogBytes) {
        $old = "$path.old"
        Move-Item -Force -Path $path -Destination $old
    }
}

function Write-Wrapper-Log($message) {
    # Written to its own file, never $outLog/$errLog -- those are held
    # under an exclusive lock by Start-Process's redirection for the
    # lifetime of the child process, so Add-Content against them would
    # intermittently fail with GetContentWriterIOError.
    $line = "[{0}] [wrapper] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
    Add-Content -Path $wrapperLog -Value $line
}

Rotate-LogIfLarge $outLog
Rotate-LogIfLarge $errLog
Rotate-LogIfLarge $wrapperLog

if ($Exe -and (Test-Path $Exe)) {
    $exe = $Exe
    $baseArgs = @()
    $env:EKKO_DATA_DIR = Join-Path $env:APPDATA "io.usemaison.ekko"
} elseif (Test-Path $pythonExe) {
    $exe = $pythonExe
    $baseArgs = @('-u', '-m', 'backend')
} else {
    Write-Wrapper-Log "FATAL: no backend found (-Exe '$Exe', $pythonExe). Reinstall ekko, or run .\dev.ps1 once."
    exit 1
}

Write-Wrapper-Log "Starting $exe"

$backoffSeconds = 5
$maxBackoffSeconds = 300

while ($true) {
    $startedAt = Get-Date

    $proc = Start-Process -FilePath $exe -ArgumentList $baseArgs `
        -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog

    Set-Content -Path $pidFile -Value $proc.Id
    Write-Wrapper-Log ("Backend started, PID {0}" -f $proc.Id)

    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    $ranFor = (Get-Date) - $startedAt

    Write-Wrapper-Log ("Backend exited with code {0} after {1:N0}s" -f $exitCode, $ranFor.TotalSeconds)
    Remove-Item -Path $pidFile -ErrorAction SilentlyContinue

    Rotate-LogIfLarge $outLog
    Rotate-LogIfLarge $errLog

    # A run that survived a while counts as healthy -- don't punish it
    # with a backoff built up from earlier, unrelated crash-looping.
    if ($ranFor.TotalSeconds -gt 60) {
        $backoffSeconds = 5
    }

    Write-Wrapper-Log ("Restarting in {0}s" -f $backoffSeconds)
    Start-Sleep -Seconds $backoffSeconds
    $backoffSeconds = [Math]::Min($backoffSeconds * 2, $maxBackoffSeconds)
}
