<#
.SYNOPSIS
    Reports on the laptop's own health: CPU, RAM, disk, battery, GPU,
    network.

.DESCRIPTION
    Handler script for EKKO's intent routing layer. Standalone on purpose:
    no dependency on the listener or intent matcher, so it can be tested
    and run directly (see scripts/README.md).

    This is a different concern from system\status.ps1, which reports on
    EKKO's own listener process (is it alive, task state, log tails).
    This script reports on the machine EKKO is running on. It's also
    different from scripts/system_metric_query.ps1, which answers a
    question about ONE metric ("what's my battery percent") -- this one
    always reports all of them. Both dot-source the same
    scripts/lib/system_metrics.ps1 rather than duplicating each metric's
    query logic twice.

    Thresholds below are hardcoded common-sense defaults, not calibrated
    against real data the way voice_auth's and routing's thresholds were
    (see readme.md) -- there's no equivalent of an enrollment set for
    "what counts as low disk space." Adjust here if they turn out wrong
    in practice.

.EXIT CODES
    0 - ran (individual metrics may still be "unavailable")
    1 - unexpected failure, nothing could be queried at all

.OUTPUT CONTRACT
    Full detail on every metric goes to stdout, for the routing log same
    as any other handler. The LAST stdout line is always "SPEAK: <text>",
    a one/two-sentence summary. routing/execute.py's spoken_override()
    picks that line up and vad_listener.py speaks it verbatim instead of
    the fixed RESPONSES confirmation -- the one handler that opts into
    this, see scripts/README.md's exit-code table and intent_routing.md.

    Also opens a rich-rendered visual companion in a new Windows Terminal
    tab via lib\system_metrics.ps1's Open-ReportWindow -- additive, not
    part of the output contract above: if it fails (no wt.exe, no pvenv),
    that's logged and swallowed, the spoken answer still happens either
    way.
#>

$ErrorActionPreference = 'Stop'
# Also brings in the shared threshold defaults ($DiskFreePercentLow etc.)
# used below, see their comment in lib\system_metrics.ps1.
. (Join-Path $PSScriptRoot 'lib\system_metrics.ps1')

$flags = @()
# Per-metric flag state, keyed the same as the $metricRows built below --
# this is what lets the visual window color a row red without re-deriving
# the threshold logic a second time in render_report.py. A dumb hashtable
# rather than tagging it onto the metric objects themselves, since
# Get-*Metric's job (lib\system_metrics.ps1) is querying, not judging.
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
$parts = @()
if ($cpu.Load -ne $null) { $parts += "CPU at $($cpu.Load)%" }
if ($ram.UsedPercent -ne $null) { $parts += "memory at $($ram.UsedPercent)%" }
if ($disk.FreeGB -ne $null) { $parts += ("{0:N0} GB free on disk" -f $disk.FreeGB) }
if ($battery.Percent -ne $null) {
    $parts += "battery at $($battery.Percent)% and $(if ($battery.Charging) { 'charging' } else { 'unplugged' })"
}
if ($gpu.Util -ne $null) { $parts += "GPU at $($gpu.Util)% and $($gpu.TempC) degrees" }

$numbers = $parts -join ", "

if ($flags.Count -gt 0) {
    $summary = "Heads up, " + ($flags -join "; and ") + ". Otherwise, " + $numbers + "."
} else {
    $summary = "System's fine. " + $numbers + "."
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
Open-ReportWindow -Summary $summary -Metrics $metricRows

exit 0
