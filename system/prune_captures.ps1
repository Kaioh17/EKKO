# Runs listener\prune_captures.py once and logs the result. What the
# scheduled "EKKO Prune Captures" task actually invokes; also fine to run
# by hand.
#
# Unlike run_listener.ps1 this isn't a long-lived process -- one prune,
# one log entry, then exit. No PID file, no retry loop: a single failed
# run just means captures/ is one run larger than it should be until the
# next trigger fires, not a broken listener.

$ErrorActionPreference = "Stop"

$repoRoot  = Split-Path -Parent $PSScriptRoot
$systemDir = $PSScriptRoot
$logDir    = Join-Path $systemDir "logs"
$pythonExe = Join-Path $repoRoot "pvenv\Scripts\python.exe"
$pruneScript = Join-Path $repoRoot "listener\prune_captures.py"
$flagsFile = Join-Path $systemDir "prune_captures.flags.txt"
$log       = Join-Path $logDir "prune.log"

$maxLogBytes = 2MB

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ((Test-Path $log) -and (Get-Item $log).Length -gt $maxLogBytes) {
    Move-Item -Force -Path $log -Destination "$log.old"
}

if (-not (Test-Path $pythonExe)) {
    Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] FATAL: $pythonExe not found. Run voice_auth\setup.ps1 first."
    exit 1
}

$extraArgs = @()
if (Test-Path $flagsFile) {
    $extraArgs = Get-Content $flagsFile |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") } |
        ForEach-Object { $_ -split '\s+' }
}

Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Running prune_captures.py $($extraArgs -join ' ')"

$output = & $pythonExe $pruneScript @extraArgs 2>&1
$exitCode = $LASTEXITCODE

$output | ForEach-Object { Add-Content -Path $log -Value "  $_" }
Add-Content -Path $log -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Exit code $exitCode"

exit $exitCode
