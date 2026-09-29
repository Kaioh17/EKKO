<#
.SYNOPSIS
    Opens the given URLs in Brave, one tab each -- no search tab.

.DESCRIPTION
    Called by scripts/daily_briefing.py's open_top_news_tabs(), after the
    news fetch resolves, to open Brave tabs for the top narrated stories
    alongside speaking their summaries. Distinct from
    scripts/open_research_tabs.ps1, which always opens a Brave Search
    results tab first (that script backs an actual search-flavored
    answer): this script's whole point is opening exactly the given
    sites and nothing else, matching what was asked for ("load up two
    of the sites on Brave").

    Not a handler, not named by any intent in routing/intents.yaml.
    Standalone on purpose, same reasoning as every other script here: can
    be run and tested directly, independent of daily_briefing.py.

.PARAMETER Urls
    Pipe-delimited ('|', not ',' -- see open_research_tabs.ps1's own
    parameter doc for why a URL's query string can legitimately contain
    a literal comma) list of absolute http/https URLs. Re-validated here
    independently of whatever daily_briefing.py already checked, same
    "re-validate at the boundary that acts on it" reasoning
    routing/execute.py's module docstring gives for its own arguments.

.EXIT CODES
    0 - launched successfully (0 or more URLs opened; 0 valid URLs is not
        an error, just nothing to do)
    1 - Brave not found at any known install path
    2 - launch failed
#>

param(
    [string]$Urls = ''
)

$ErrorActionPreference = 'Stop'

$CandidateUrls = if ($Urls) { $Urls -split '\|' } else { @() }

$ValidUrls = @()
foreach ($candidate in $CandidateUrls) {
    $parsed = $null
    if ([Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$parsed) -and
        ($parsed.Scheme -eq 'http' -or $parsed.Scheme -eq 'https')) {
        $ValidUrls += $parsed.AbsoluteUri
    }
    else {
        Write-Warning "Dropping URL that isn't absolute http/https: '$candidate'"
    }
}
# Belt-and-suspenders cap, same reasoning as open_research_tabs.ps1's own:
# daily_briefing.py passes audible_news_count (2 by default) news URLs
# plus whatever's in config/daily_briefing.yaml's `extra_tabs`, but this
# script doesn't trust its caller any more than that one does.
$ValidUrls = $ValidUrls | Select-Object -First 10 -Unique

if ($ValidUrls.Count -eq 0) {
    Write-Output 'No valid URLs to open.'
    exit 0
}

# Duplicated from open_apple_music.ps1/web_search.ps1/open_research_tabs.ps1
# rather than shared, matching this folder's "no shared state between
# scripts" convention (see scripts/README.md); update all copies if Brave
# moves.
$BraveCandidates = @(
    (Join-Path $env:ProgramFiles 'BraveSoftware\Brave-Browser\Application\brave.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'BraveSoftware\Brave-Browser\Application\brave.exe'),
    (Join-Path $env:LOCALAPPDATA 'BraveSoftware\Brave-Browser\Application\brave.exe')
)
$BravePath = $BraveCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $BravePath) {
    Write-Error "Brave not found at any of: $($BraveCandidates -join ', '). Update `$BraveCandidates in this script if it's installed somewhere else."
    exit 1
}

try {
    # One Start-Process call with multiple URL arguments opens each as a
    # new tab in the same window (or a new window if Brave wasn't already
    # running), same as open_research_tabs.ps1's own call.
    Start-Process -FilePath $BravePath -ArgumentList $ValidUrls
    Write-Output "Opened $($ValidUrls.Count) Brave tab(s)."
    exit 0
}
catch {
    Write-Error "Failed to open tabs: $_"
    exit 2
}
