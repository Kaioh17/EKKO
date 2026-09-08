<#
.SYNOPSIS
    Dev mode: opens a live, color-coded view of everything the listener
    is doing right now, in a new terminal tab.

.DESCRIPTION
    Doesn't start or stop the listener -- it only tails the logs it (or
    the background "EKKO Listener" scheduled task running it) already
    writes: system\logs\listener.out.log / .err.log,
    routing\logs\routing.jsonl, listener\captures\transcripts.jsonl. It
    doesn't matter whether the listener is running as the hidden
    scheduled task (system\start.ps1) or in the foreground (see
    system\README.md's "run once before backgrounding it" step) -- both
    write to the same files, this just reads them, so it's safe to run
    or leave open at any time, including while nothing is listening yet.

    The actual tailing/formatting is system\dev_tail.py (rich), run
    through pvenv -- same "PowerShell owns the window, Python owns the
    rendering" split scripts\lib\system_metrics.ps1's Open-ReportWindow
    uses for scripts\render_report.py, just live instead of one-shot.

.PARAMETER Lines
    How many existing lines per source to backfill on open (default 20).

.PARAMETER Interval
    Poll interval in seconds for new log lines (default 0.3).

.PARAMETER Only
    Comma-separated subset of sources to watch: listener,error,route,capture
    (default: all four). E.g. -Only route,capture to watch just what got
    said and how it routed, without the raw listener chatter.

.PARAMETER NoWindow
    Run in the current console instead of opening a new Windows Terminal
    tab. Use this if wt.exe isn't available, or you'd rather it not open
    a new window.
#>

param(
    [int]$Lines = 20,
    [double]$Interval = 0.3,
    [string]$Only = "",
    [switch]$NoWindow
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
$devTail = Join-Path $PSScriptRoot 'dev_tail.py'

if (-not (Test-Path $pythonExe)) {
    Write-Host "pvenv not found at $pythonExe -- run voice_auth\setup.ps1 first."
    exit 1
}
if (-not (Test-Path $devTail)) {
    Write-Host "system\dev_tail.py not found."
    exit 1
}

$pyArgs = @('--lines', $Lines, '--interval', $Interval)
if ($Only) { $pyArgs += @('--only', $Only) }

if ($NoWindow) {
    & $pythonExe $devTail @pyArgs
    exit $LASTEXITCODE
}

$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if (-not $wt) {
    Write-Host "wt.exe not found, running here instead (Ctrl+C to stop):"
    & $pythonExe $devTail @pyArgs
    exit $LASTEXITCODE
}

# Start-Process -ArgumentList, given an array, joins elements with plain
# spaces -- it does NOT quote an element that itself contains a space
# (unlike .NET's ProcessStartInfo.ArgumentList, which this cmdlet's name
# suggests but isn't). 'EKKO Dev' as one array element therefore arrived
# at wt.exe as two bare tokens, and wt's own command-line parser (which
# splits on unquoted whitespace to find tab/pane boundaries) read "EKKO"
# as the --title value and "Dev" as the start of a second, malformed
# command, corrupting everything after it -- the "cannot find the file
# specified" error was wt trying to launch a program literally named
# "Dev". Build one pre-quoted string instead and hand Start-Process that,
# so quoting is explicit rather than depending on a cmdlet behavior it
# doesn't actually have.
function Quote-Arg([string]$Value) {
    if ($Value -match '\s') { return '"' + $Value + '"' }
    return $Value
}
$wtArgs = @('new-tab', '--title', 'EKKO Dev', $pythonExe, $devTail) + $pyArgs
$argString = ($wtArgs | ForEach-Object { Quote-Arg $_ }) -join ' '

Start-Process -FilePath $wt.Source -ArgumentList $argString
Write-Host "Dev mode opened in a new Windows Terminal tab."
