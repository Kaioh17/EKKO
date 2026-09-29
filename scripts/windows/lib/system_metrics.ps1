<#
.SYNOPSIS
    Shared metric-query functions for system_diagnosis.ps1 and
    system_metric_query.ps1. Not a handler itself, not named by any intent
    in routing/intents.yaml -- dot-source it, don't invoke it directly.

.DESCRIPTION
    One function per metric (CPU, RAM, disk, battery, GPU, network), each
    wrapping its own query in try/catch and returning a PSCustomObject
    with `$null` fields on failure rather than throwing. That's what lets
    both call sites -- one report of everything, one query of a single
    metric -- degrade a single unavailable metric ("no battery reported")
    without duplicating the try/catch around it in two places, or in five
    of the six functions when only the sixth is unavailable on this
    machine.

    Every function also carries its own one-line Detail string (for
    stdout, same as before this file existed) so callers don't re-derive
    formatting in two places and drift.
#>

# Threshold defaults shared between system_diagnosis.ps1 and
# system_metric_query.ps1, dot-sourced into both rather than declared
# twice, so "what counts as low battery" has one definition, not two that
# can drift apart. Hardcoded common-sense values, not calibrated against
# real data the way voice_auth's/routing's thresholds were (see
# readme.md) -- there's no equivalent of an enrollment set for "what
# counts as low disk space." Adjust here if they turn out wrong in
# practice.
$DiskFreePercentLow = 10
$BatteryPercentLow  = 20
$CpuPercentHigh     = 90
$RamPercentHigh     = 90

function Get-CpuMetric {
    try {
        $load = [math]::Round((Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average)
        [PSCustomObject]@{ Load = $load; Detail = "CPU load: $load%" }
    } catch {
        [PSCustomObject]@{ Load = $null; Detail = "CPU load: unavailable ($_)" }
    }
}

function Get-RamMetric {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $usedPercent = [math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100)
        [PSCustomObject]@{ UsedPercent = $usedPercent; Detail = "Memory used: $usedPercent%" }
    } catch {
        [PSCustomObject]@{ UsedPercent = $null; Detail = "Memory used: unavailable ($_)" }
    }
}

function Get-DiskMetric {
    try {
        $drive = Get-PSDrive -Name C
        $totalGB = ($drive.Used + $drive.Free) / 1GB
        $freeGB = $drive.Free / 1GB
        $freePercent = [math]::Round(($freeGB / $totalGB) * 100)
        [PSCustomObject]@{
            FreeGB      = $freeGB
            FreePercent = $freePercent
            Detail      = "Disk C: free {0:N1} GB ({1}% free)" -f $freeGB, $freePercent
        }
    } catch {
        [PSCustomObject]@{ FreeGB = $null; FreePercent = $null; Detail = "Disk C:: unavailable ($_)" }
    }
}

function Get-BatteryMetric {
    try {
        $battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
        if ($battery) {
            $percent = $battery.EstimatedChargeRemaining
            # BatteryStatus 2 = AC power / charging. See Win32_Battery docs.
            $charging = $battery.BatteryStatus -eq 2
            $word = if ($charging) { "charging" } else { "unplugged" }
            [PSCustomObject]@{ Percent = $percent; Charging = $charging; Detail = "Battery: $percent% ($word)" }
        } else {
            [PSCustomObject]@{ Percent = $null; Charging = $null; Detail = "Battery: unavailable (no battery reported)" }
        }
    } catch {
        [PSCustomObject]@{ Percent = $null; Charging = $null; Detail = "Battery: unavailable ($_)" }
    }
}

function Get-GpuMetric {
    try {
        $nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($nvidiaSmi) {
            $line = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits
            $util, $memUsed, $memTotal, $temp = $line -split ',\s*'
            [PSCustomObject]@{
                Util   = [int]$util
                TempC  = [int]$temp
                Detail = "GPU: $util% util, $memUsed/$memTotal MB, $temp C"
            }
        } else {
            [PSCustomObject]@{ Util = $null; TempC = $null; Detail = "GPU: unavailable (nvidia-smi not found)" }
        }
    } catch {
        [PSCustomObject]@{ Util = $null; TempC = $null; Detail = "GPU: unavailable ($_)" }
    }
}

# Actual reachability, not just NIC state: [NetworkInterface]::GetIsNetworkAvailable()
# only reports whether an interface is up, which is true on a LAN with no
# internet (captive portal, ISP outage) and would misreport "connected" in
# exactly the case worth catching. Test-NetConnection's -InformationLevel
# Quiet does a real reachability check and returns a plain boolean. Two
# targets, not one: a single unreachable IP (transient drop, that address
# blocking ICMP) shouldn't read as "no internet."
function Get-NetworkMetric {
    try {
        $connected = (Test-NetConnection -ComputerName '1.1.1.1' -InformationLevel Quiet -WarningAction SilentlyContinue) `
            -or (Test-NetConnection -ComputerName '8.8.8.8' -InformationLevel Quiet -WarningAction SilentlyContinue)
        [PSCustomObject]@{ Connected = $connected; Detail = "Network: $(if ($connected) { 'connected' } else { 'no connection' })" }
    } catch {
        [PSCustomObject]@{ Connected = $null; Detail = "Network: unavailable ($_)" }
    }
}

# Opens a rich-rendered visual companion to the spoken SPEAK: summary, in a
# new Windows Terminal tab. This is additive, never load-bearing: EKKO
# already answered out loud by the time this runs, so a missing wt.exe, a
# missing pvenv, or rich failing to import must degrade to a log line, not
# a failed handler. Every failure path here is caught and just Write-Output
# rather than thrown, same reasoning as the metric functions above
# returning $null instead of throwing.
#
# -Metrics is an array of hashtables shaped like
# @{ name = 'CPU'; detail = '65%'; flagged = $false } -- lowercase keys,
# matching -Summary's own "summary"/"metrics" top-level JSON keys below,
# because ConvertTo-Json preserves hashtable key casing verbatim and
# render_report.py's json.loads() then reads it back case-sensitively.
# One entry per row of the table scripts/render_report.py draws;
# -Summary is the same text that went into the handler's own SPEAK: line,
# shown again in the window as a panel so what EKKO said and what's on
# screen always match.
function Open-ReportWindow {
    param(
        [string]$Summary,
        [array]$Metrics
    )
    try {
        $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
        if (-not $wt) {
            Write-Output "Report window: skipped (wt.exe not found)"
            return
        }
        # $PSScriptRoot here is this file's own directory
        # (scripts\windows\lib), not the caller's -- functions carry the
        # defining file's $PSScriptRoot, not the caller's, in PowerShell.
        # Three levels up is the repo root from scripts\windows\lib.
        $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
        $pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
        $renderScript = Join-Path $repoRoot 'scripts\render_report.py'
        if (-not (Test-Path $pythonExe) -or -not (Test-Path $renderScript)) {
            Write-Output "Report window: skipped (pvenv or render_report.py not found)"
            return
        }

        $payload = @{ summary = $Summary; metrics = $Metrics } | ConvertTo-Json -Depth 5 -Compress
        $jsonPath = Join-Path $env:TEMP ("ekko_report_{0}.json" -f (Get-Date -Format 'yyyyMMddHHmmssfff'))
        Set-Content -Path $jsonPath -Value $payload -Encoding utf8

        # Start-Process -ArgumentList, given an array, joins elements with
        # plain spaces rather than quoting ones that contain a space
        # themselves (it isn't .NET's ProcessStartInfo.ArgumentList,
        # despite the name). None of these paths have a space for this
        # user, but $env:TEMP can on a different machine/username, and
        # wt.exe's own command-line parser would silently split on it and
        # mangle the launch -- see system\dev.ps1's Quote-Arg for the same
        # fix and the fuller explanation of why.
        $wtArgs = @('new-tab', '--title', 'EKKO', $pythonExe, $renderScript, $jsonPath)
        $argString = ($wtArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
        Start-Process -FilePath $wt.Source -ArgumentList $argString
        Write-Output "Report window: opened"
    } catch {
        Write-Output "Report window: failed to open ($_)"
    }
}

# Sibling to Open-ReportWindow above, not a variant of it: that function is
# tied one-to-one to render_report.py's single job (render a metrics table
# that was already fully computed by the time it's called). This one hands
# off to scripts\daily_briefing.py, which does real work of its own after
# launch (HTTP fetches, yfinance, an LLM call) rather than just rendering
# what it's given -- a different enough job to earn its own function
# instead of a flag bolted onto Open-ReportWindow. Same
# wt.exe-not-found/pvenv-not-found/anything-else degrade-and-log posture
# throughout: this is called after daily_briefing.ps1 has already written
# its SPEAK: line, so a failure here must never surface as a failed
# handler, only a log line.
#
# -Summary/-Metrics are the same system-health payload Open-ReportWindow
# already builds (same JSON shape, same lowercase "summary"/"metrics"
# keys) -- daily_briefing.py renders that System section immediately on
# read, then fetches news/stocks itself and fills those sections in as
# each resolves. Passing already-computed system data in rather than
# having the Python side re-query it keeps "what counts as low battery"
# defined in exactly one place (lib\system_metrics.ps1's thresholds
# above), not duplicated in Python.
function Open-BriefingWindow {
    param(
        [string]$Summary,
        [array]$Metrics
    )
    try {
        $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
        if (-not $wt) {
            Write-Output "Briefing window: skipped (wt.exe not found)"
            return
        }
        # Three levels up from scripts\windows\lib, same as Open-ReportWindow above.
        $repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
        $pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
        $briefingScript = Join-Path $repoRoot 'scripts\daily_briefing.py'
        if (-not (Test-Path $pythonExe) -or -not (Test-Path $briefingScript)) {
            Write-Output "Briefing window: skipped (pvenv or daily_briefing.py not found)"
            return
        }

        $payload = @{ summary = $Summary; metrics = $Metrics } | ConvertTo-Json -Depth 5 -Compress
        $jsonPath = Join-Path $env:TEMP ("ekko_briefing_{0}.json" -f (Get-Date -Format 'yyyyMMddHHmmssfff'))
        Set-Content -Path $jsonPath -Value $payload -Encoding utf8

        # Same ArgumentList-quoting reasoning as Open-ReportWindow above:
        # Start-Process -ArgumentList joins on plain spaces, not
        # ProcessStartInfo.ArgumentList's real quoting, so anything with a
        # space (an $env:TEMP path on a different machine/username) needs
        # its own quotes or wt.exe's parser silently mangles the launch.
        $wtArgs = @('new-tab', '--title', 'EKKO Daily Briefing', $pythonExe, $briefingScript, $jsonPath)
        $argString = ($wtArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
        Start-Process -FilePath $wt.Source -ArgumentList $argString
        Write-Output "Briefing window: opened"
    } catch {
        Write-Output "Briefing window: failed to open ($_)"
    }
}
