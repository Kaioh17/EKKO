# Wrapper that keeps listener\vad_listener.py running: resolves every
# path off its own location (so it works regardless of cwd or how Task
# Scheduler invokes it), logs to system\logs\, and restarts the process
# with exponential backoff if it ever exits unexpectedly. This loop is
# infinite by design -- the only way it stops is the whole process tree
# being killed from outside (system\stop.ps1), not an internal exit path.
#
# Not meant to be run directly day-to-day; system\install_task.ps1 wires
# this into Task Scheduler, system\start.ps1 / stop.ps1 control it.

$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$systemDir  = $PSScriptRoot
$logDir     = Join-Path $systemDir "logs"
$pythonExe  = Join-Path $repoRoot "pvenv\Scripts\python.exe"
$listener   = Join-Path $repoRoot "listener\vad_listener.py"
$flagsFile  = Join-Path $systemDir "listener.flags.txt"

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

if (-not (Test-Path $pythonExe)) {
    Write-Wrapper-Log "FATAL: $pythonExe not found. Run voice_auth\setup.ps1 first."
    exit 1
}

# Read extra flags once at startup (default: --no-execute). Blank lines
# and #-comments are ignored. See listener.flags.txt for how to flip modes.
$extraArgs = @()
if (Test-Path $flagsFile) {
    $extraArgs = Get-Content $flagsFile |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") } |
        ForEach-Object { $_ -split '\s+' }
}

Write-Wrapper-Log ("Starting. Flags: " + ($(if ($extraArgs) { $extraArgs -join ' ' } else { '(none)' })))

$backoffSeconds = 5
$maxBackoffSeconds = 300

while ($true) {
    $argList = @('-u', $listener) + $extraArgs
    $startedAt = Get-Date

    $proc = Start-Process -FilePath $pythonExe -ArgumentList $argList `
        -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog

    Set-Content -Path $pidFile -Value $proc.Id
    Write-Wrapper-Log ("Listener started, PID {0}" -f $proc.Id)

    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    $ranFor = (Get-Date) - $startedAt

    Write-Wrapper-Log ("Listener exited with code {0} after {1:N0}s" -f $exitCode, $ranFor.TotalSeconds)
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
