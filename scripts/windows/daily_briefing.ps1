<#
.SYNOPSIS
    "What's happening today" -- a system-health check plus a terminal
    window covering tech news and a stock watchlist.

.DESCRIPTION
    Handler script for EKKO's intent routing layer. Standalone on purpose,
    same as system_diagnosis.ps1: no dependency on the listener or intent
    matcher, so it can be tested and run directly (see scripts/README.md).

    The system-health half of this is system_diagnosis.ps1's logic, reused
    verbatim (both dot-source the same scripts/lib/system_metrics.ps1)
    rather than duplicated -- same metrics, same thresholds, same spoken
    summary shape. What's different is what happens after: instead of
    Open-ReportWindow's "render the table I already computed," this calls
    Open-BriefingWindow, which launches scripts/daily_briefing.py -- a
    separate Python process that does real work of its own after launch
    (HTTP fetches for tech news, yfinance for stock quotes, a claude -p
    call for commentary, then a second, later TTS call for the top news
    stories).

    That split is deliberate and is the whole latency story for this
    command: routing/execute.py waits on THIS script, and this script only
    ever does fast, local work (query six metrics, write a small JSON
    file, fire off a detached process) before exiting -- the slow,
    genuinely-external part happens entirely off the voice pipeline's
    critical path, in a process nothing here waits on. So this command's
    spoken response lands exactly as fast as system_diagnosis.ps1's does
    today; news/stocks/commentary simply appear in the window a few
    seconds later.

.EXIT CODES
    0 - ran (individual metrics may still be "unavailable")
    1 - unexpected failure, nothing could be queried at all

.OUTPUT CONTRACT
    Same SPEAK: convention as system_diagnosis.ps1: full detail on every
    metric to stdout, then a final "SPEAK: <text>" line
    routing/execute.py's spoken_override() picks up and
    listener/vad_listener.py speaks verbatim. Also opens the briefing
    window via Open-BriefingWindow -- additive, never part of the output
    contract above, a failure there is logged and swallowed the same way
    Open-ReportWindow's failures are.
#>

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\system_metrics.ps1')

$flagged = @{ cpu = $false; ram = $false; disk = $false; battery = $false; gpu = $false; network = $false }

$cpu     = Get-CpuMetric
$ram     = Get-RamMetric
$disk    = Get-DiskMetric
$battery = Get-BatteryMetric
$gpu     = Get-GpuMetric
$network = Get-NetworkMetric

Write-Output $cpu.Detail
Write-Output $ram.Detail
Write-Output $disk.Detail
Write-Output $battery.Detail
Write-Output $gpu.Detail
Write-Output $network.Detail

$flags = @()
if ($cpu.Load -ne $null -and $cpu.Load -gt $CpuPercentHigh) {
    $flags += "CPU is under heavy load, $($cpu.Load) percent"
    $flagged.cpu = $true
}
if ($ram.UsedPercent -ne $null -and $ram.UsedPercent -gt $RamPercentHigh) {
    $flags += "memory is nearly full, $($ram.UsedPercent) percent used"
    $flagged.ram = $true
}
if ($disk.FreePercent -ne $null -and $disk.FreePercent -lt $DiskFreePercentLow) {
    $flags += ("disk space is low, {0:N0} GB free" -f $disk.FreeGB)
    $flagged.disk = $true
}
if ($battery.Percent -ne $null -and $battery.Percent -lt $BatteryPercentLow -and -not $battery.Charging) {
    $flags += "battery is low at $($battery.Percent) percent and unplugged"
    $flagged.battery = $true
}
if ($network.Connected -eq $false) {
    $flags += "there's no network connection"
    $flagged.network = $true
}

# --- Build the spoken summary ---
# Deliberately NOT the same shape as system_diagnosis.ps1's summary: that
# command's whole point is the numbers, so it reads every metric out loud.
# This one's job is to get to news/stocks quickly, so the spoken part only
# calls out what's actually flagged (the red ones) and otherwise just says
# "System's fine" and moves on -- the full per-metric numbers still render
# in the window below (see $metricRows), they're just not read aloud here.
if ($flags.Count -gt 0) {
    $summary = "Heads up, " + ($flags -join "; and ") + ". Pulling up today's news and your stocks now."
} else {
    $summary = "System's fine. Pulling up today's news and your stocks now."
}

Write-Output "SPEAK: $summary"

$metricRows = @(
    @{ name = 'CPU';     detail = $cpu.Detail;     flagged = $flagged.cpu }
    @{ name = 'Memory';  detail = $ram.Detail;     flagged = $flagged.ram }
    @{ name = 'Disk C:'; detail = $disk.Detail;    flagged = $flagged.disk }
    @{ name = 'Battery'; detail = $battery.Detail; flagged = $flagged.battery }
    @{ name = 'GPU';     detail = $gpu.Detail;     flagged = $flagged.gpu }
    @{ name = 'Network'; detail = $network.Detail; flagged = $flagged.network }
)
Open-BriefingWindow -Summary $summary -Metrics $metricRows

exit 0
