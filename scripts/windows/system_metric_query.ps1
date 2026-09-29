<#
.SYNOPSIS
    Answers a question about ONE laptop metric: battery, CPU, RAM, disk,
    GPU, or network.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    system_metric_query intent's `-Metric` slot (see
    routing/intents.yaml). Standalone on purpose, same reasoning as every
    other handler in this folder (scripts/README.md): can be run and
    tested directly, no listener/matcher dependency.

    Dot-sources scripts/lib/system_metrics.ps1, the same functions
    scripts/system_diagnosis.ps1 uses for its full report -- one query
    per metric, not duplicated per script. This script differs from that
    one only in scope: it reports and speaks a single metric instead of
    all six with threshold flags.

    -Metric is constrained by [ValidateSet(...)] mirroring
    routing/intents.yaml's closed_vocabulary values for this slot, same
    defence-in-depth reasoning as open_app.ps1's -App: routing/slots.py
    already guarantees only a canonical value reaches this far, so the
    ValidateSet should be unreachable, and a bug upstream should fail as
    a parameter error rather than silently answer the wrong question.

.EXIT CODES
    0 - answered (the metric itself may still be "unavailable", e.g. no
        battery on this machine -- that's a real answer, not a failure)
    1 - unexpected failure

.OUTPUT CONTRACT
    Same as system_diagnosis.ps1: full detail line to stdout, then a
    final "SPEAK: <text>" line. See scripts/README.md's exit-code table.
    Also opens the same rich-rendered visual companion window
    system_diagnosis.ps1 does (lib\system_metrics.ps1's Open-ReportWindow),
    scoped to this one metric -- additive, not part of the output
    contract above, failures there are logged and swallowed.
#>

param(
    [Parameter(Mandatory)]
    [ValidateSet('battery', 'cpu', 'ram', 'disk', 'gpu', 'network')]
    [string]$Metric
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\system_metrics.ps1')

$displayName = @{
    battery = 'Battery'; cpu = 'CPU'; ram = 'Memory'; disk = 'Disk C:'; gpu = 'GPU'; network = 'Network'
}[$Metric]
$flagged = $false

switch ($Metric) {
    'battery' {
        $m = Get-BatteryMetric
        Write-Output $m.Detail
        $summary = if ($m.Percent -eq $null) {
            "There's no battery reported on this machine."
        } else {
            "Battery's at $($m.Percent)%, $(if ($m.Charging) { 'charging' } else { 'not charging' })."
        }
        $flagged = $m.Percent -ne $null -and $m.Percent -lt $BatteryPercentLow -and -not $m.Charging
    }
    'cpu' {
        $m = Get-CpuMetric
        Write-Output $m.Detail
        $summary = if ($m.Load -eq $null) { "CPU load isn't available right now." } else { "CPU load is at $($m.Load)%." }
        $flagged = $m.Load -ne $null -and $m.Load -gt $CpuPercentHigh
    }
    'ram' {
        $m = Get-RamMetric
        Write-Output $m.Detail
        $summary = if ($m.UsedPercent -eq $null) { "Memory usage isn't available right now." } else { "Memory usage is at $($m.UsedPercent)%." }
        $flagged = $m.UsedPercent -ne $null -and $m.UsedPercent -gt $RamPercentHigh
    }
    'disk' {
        $m = Get-DiskMetric
        Write-Output $m.Detail
        $summary = if ($m.FreeGB -eq $null) {
            "Disk space isn't available right now."
        } else {
            "You've got {0:N0} gigabytes free on drive C." -f $m.FreeGB
        }
        $flagged = $m.FreePercent -ne $null -and $m.FreePercent -lt $DiskFreePercentLow
    }
    'gpu' {
        $m = Get-GpuMetric
        Write-Output $m.Detail
        $summary = if ($m.Util -eq $null) {
            "GPU info isn't available right now."
        } else {
            "GPU is at $($m.Util)% utilization, $($m.TempC) degrees."
        }
        # No GPU threshold defined (see system_diagnosis.ps1), so never flagged.
    }
    'network' {
        $m = Get-NetworkMetric
        Write-Output $m.Detail
        $summary = if ($m.Connected -eq $null) {
            "Network status isn't available right now."
        } elseif ($m.Connected) {
            "Yes, you're connected to the internet."
        } else {
            "No, there's no internet connection right now."
        }
        $flagged = $m.Connected -eq $false
    }
}

Write-Output "SPEAK: $summary"
Open-ReportWindow -Summary $summary -Metrics @(
    @{ name = $displayName; detail = $m.Detail; flagged = $flagged }
)

exit 0
