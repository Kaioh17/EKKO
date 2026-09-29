<#
.SYNOPSIS
    Reports how the stock watchlist is doing right now -- price and %
    change for every ticker in config\daily_briefing.yaml.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    watchlist_report intent (see routing\intents.yaml). Standalone on
    purpose, same reasoning as every other handler here
    (scripts\README.md): can be run and tested directly, no
    listener/matcher dependency.

    Different command from daily_briefing.ps1: that one is the whole
    "start my day" report (system health + tech news + stocks + LLM
    commentary), and pushes the slow half into a detached window off the
    voice pipeline's critical path. This one is ONLY the watchlist -- no
    news, no Gemini call, no second TTS pass -- so it stays fast enough to
    finish inside routing\execute.py's handler timeout and answer out loud
    with real numbers, no window handoff needed.

    The quote fetch lives in scripts\watchlist_report.py (pvenv,
    yfinance), which reuses daily_briefing.py's own stock functions so
    this command and the daily briefing's Stocks section can't drift.
    Same .ps1 -> .py split as check_memory_activity.ps1, and for the same
    reason: the fetch/degrade logic is easier to keep resilient in Python.

.EXIT CODES
    0 - reported (individual tickers may still be "unavailable")
    1 - the python query failed, was unparsable, or returned no tickers
        (nothing configured in config\daily_briefing.yaml)

.OUTPUT CONTRACT
    Per-ticker detail to stdout, then a final "SPEAK: <text>" line naming
    each ticker's move -- routing\execute.py's spoken_override() picks
    that up and vad_listener.py speaks it verbatim, same convention as
    system_diagnosis.ps1 / check_memory_activity.ps1 (see
    scripts\README.md's SPEAK: convention).

    Also opens the shared rich-rendered visual companion in a new Windows
    Terminal tab (lib\system_metrics.ps1's Open-ReportWindow), one row per
    ticker -- additive, not part of the output contract above: a missing
    wt.exe or pvenv is logged and swallowed there, never fails this
    handler.
#>

$ErrorActionPreference = 'Stop'
# Only for Open-ReportWindow -- none of the Get-*Metric functions in here
# apply to a stock report.
. (Join-Path $PSScriptRoot 'lib\system_metrics.ps1')

# Two levels up from scripts\windows -- watchlist_report.py lives at
# scripts\ (shared across OS trees), not alongside this .ps1.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
$queryScript = Join-Path $repoRoot 'scripts\watchlist_report.py'

if (-not (Test-Path $pythonExe) -or -not (Test-Path $queryScript)) {
    Write-Output "watchlist_report: pvenv or watchlist_report.py not found"
    exit 1
}

$raw = & $pythonExe $queryScript --timeout 8
if ($LASTEXITCODE -ne 0 -or -not $raw) {
    Write-Output "watchlist_report: python query failed (exit $LASTEXITCODE)"
    if ($raw) { Write-Output $raw }
    exit 1
}

try {
    $report = $raw | ConvertFrom-Json
} catch {
    Write-Output "watchlist_report: couldn't parse python output ($_)"
    Write-Output $raw
    exit 1
}

# @(...) forces an array even for a single ticker, so .Count and the
# foreach below behave the same at n=1 as at n=3.
$stocks = @($report.stocks)
if ([int]$report.count -lt 1) {
    Write-Output "watchlist_report: no tickers configured (config\daily_briefing.yaml)"
    exit 1
}

# Friendly name (config\daily_briefing.yaml's ticker_names) as the label,
# raw symbol kept alongside when the two differ so stdout still shows what
# was actually priced.
function Get-Label($s) {
    if ($s.name -and $s.name -ne $s.ticker) { "$($s.name) ($($s.ticker))" } else { $s.ticker }
}

foreach ($s in $stocks) {
    if ($s.error -or $null -eq $s.price) {
        Write-Output ("{0,-18} unavailable" -f (Get-Label $s))
    } else {
        $chgText = if ($null -eq $s.change_pct) { "n/a" } else { "{0:+0.00;-0.00;0.00}%" -f $s.change_pct }
        Write-Output ("{0,-18} `${1:N2}  {2}" -f (Get-Label $s), $s.price, $chgText)
    }
}

# --- Spoken summary: one short clause per ticker (direction + percent),
# same "call out each one briefly" shape as check_memory_activity's
# top-3 list. Uses the friendly name so TTS says "gold", not "G C
# equals F". ---
$parts = @()
foreach ($s in $stocks) {
    if ($s.error -or $null -eq $s.price) {
        $parts += "$($s.name) is unavailable"
    } elseif ($null -eq $s.change_pct) {
        $parts += ("{0} is at {1:N2}" -f $s.name, $s.price)
    } else {
        $dir = if ($s.change_pct -ge 0) { "up" } else { "down" }
        $parts += ("{0} is {1} {2:N1} percent" -f $s.name, $dir, [math]::Abs([double]$s.change_pct))
    }
}
$summary = "Your watchlist: " + ($parts -join ", ") + "."
Write-Output "SPEAK: $summary"

$metricRows = $stocks | ForEach-Object {
    if ($_.error -or $null -eq $_.price) {
        @{ name = (Get-Label $_); detail = "unavailable"; flagged = $true }
    } else {
        $chgText = if ($null -eq $_.change_pct) { "" } else { " ({0:+0.0;-0.0;0.0}%)" -f $_.change_pct }
        @{
            name    = (Get-Label $_)
            detail  = ("`${0:N2}{1}" -f $_.price, $chgText)
            # Flag a red row on a hard drop, same idea as a flagged metric
            # in the system report -- purely cosmetic in the window.
            flagged = ($null -ne $_.change_pct -and [double]$_.change_pct -le -3)
        }
    }
}
Open-ReportWindow -Summary $summary -Metrics $metricRows

exit 0
