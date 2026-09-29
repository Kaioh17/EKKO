<#
.SYNOPSIS
    Reports which processes are using the most memory right now.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    check_memory_activity intent (see routing/intents.yaml). Standalone
    on purpose, same reasoning as every other handler here
    (scripts/README.md): can be run and tested directly, no
    listener/matcher dependency.

    Different question from system_metric_query.ps1 -Metric ram: that
    one answers "how full is memory overall" (one percentage). This one
    answers "what's using it" -- a ranked list of processes. They share
    no code and no threshold, deliberately: lib\system_metrics.ps1's
    $RamPercentHigh is about the aggregate, and doesn't tell you which
    process to blame.

    The actual scan lives in scripts/check_memory_activity.py (psutil,
    not a handler itself, see that file's own header), run through
    pvenv\Scripts\python.exe the same way lib\system_metrics.ps1's
    Open-ReportWindow already shells out to scripts/render_report.py.
    PowerShell's Get-Process doesn't give the same per-process resilience
    against a process exiting mid-scan that psutil.process_iter()'s
    NoSuchProcess/AccessDenied handling does, so the query logic lives in
    Python rather than being reimplemented here.

.EXIT CODES
    0 - reported
    1 - the python query failed, was unparsable, or returned nothing

.OUTPUT CONTRACT
    Full per-process detail to stdout, then a final "SPEAK: <text>" line
    naming the top 3 by memory -- routing/execute.py's spoken_override()
    picks that up and vad_listener.py speaks it verbatim, same convention
    as system_diagnosis.ps1 and system_metric_query.ps1, see
    scripts/README.md's SPEAK: convention.

    Also opens the same rich-rendered visual companion in a new Windows
    Terminal tab those two scripts use (lib\system_metrics.ps1's
    Open-ReportWindow), listing every process check_memory_activity.py
    returned, flagged red past $MemoryHogMB -- additive, not part of the
    output contract above: a missing wt.exe or pvenv is logged and
    swallowed there, never fails this handler.
#>

$ErrorActionPreference = 'Stop'
# Only for Open-ReportWindow -- none of the Get-*Metric functions in here
# apply to a per-process report, this handler doesn't touch RAM/CPU/etc.
. (Join-Path $PSScriptRoot 'lib\system_metrics.ps1')

# A process above this gets named individually and pushes the flagged
# count in the spoken summary, not just colored in the visual table.
# Hardcoded common-sense default, not calibrated against real data, same
# caveat as lib\system_metrics.ps1's thresholds -- there's no enrollment
# set for "what counts as a memory hog" either. Adjust here if it turns
# out wrong in practice.
$MemoryHogMB = 1024

# Two levels up from scripts\windows -- check_memory_activity.py lives at
# scripts\ (shared across OS trees), not alongside this .ps1.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
$queryScript = Join-Path $repoRoot 'scripts\check_memory_activity.py'

if (-not (Test-Path $pythonExe) -or -not (Test-Path $queryScript)) {
    Write-Output "check_memory_activity: pvenv or check_memory_activity.py not found"
    exit 1
}

$raw = & $pythonExe $queryScript --limit 15
if ($LASTEXITCODE -ne 0 -or -not $raw) {
    Write-Output "check_memory_activity: python query failed (exit $LASTEXITCODE)"
    if ($raw) { Write-Output $raw }
    exit 1
}

try {
    $report = $raw | ConvertFrom-Json
} catch {
    Write-Output "check_memory_activity: couldn't parse python output ($_)"
    Write-Output $raw
    exit 1
}

# @(...) forces an array even when psutil only found one process, so
# .Count and Select-Object -First below behave the same at n=1 as at
# n=15 instead of PowerShell unwrapping a single result to a bare object.
$processes = @($report.processes)
if ($processes.Count -eq 0) {
    Write-Output "check_memory_activity: no processes reported"
    exit 1
}

foreach ($p in $processes) {
    Write-Output ("{0,-30} PID {1,-8} {2:N1} MB" -f $p.name, $p.pid, $p.mb)
}
Write-Output ("{0} processes scanned, {1:N1} MB total RSS" -f $report.count, $report.total_mb)

# --- Build the spoken summary: the top 3 by name, the "major
# disturbances" EKKO calls out, same shape as system_diagnosis.ps1's
# $flags list flagging the worst metrics rather than reciting all six. ---
$top3 = @($processes | Select-Object -First 3)
$named = $top3 | ForEach-Object { "$($_.name) at {0:N0} megabytes" -f $_.mb }
$summary = "Top memory users: " + ($named -join ", ") + "."

$hogs = @($processes | Where-Object { $_.mb -gt $MemoryHogMB })
if ($hogs.Count -gt 0) {
    $verb = if ($hogs.Count -eq 1) { "is" } else { "are" }
    $summary += " $($hogs.Count) process$(if ($hogs.Count -ne 1) { 'es' }) $verb over a gigabyte."
}

Write-Output "SPEAK: $summary"

$metricRows = $processes | ForEach-Object {
    @{ name = $_.name; detail = ("PID {0}, {1:N1} MB" -f $_.pid, $_.mb); flagged = ($_.mb -gt $MemoryHogMB) }
}
Open-ReportWindow -Summary $summary -Metrics $metricRows

exit 0
