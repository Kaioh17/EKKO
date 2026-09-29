<#
.SYNOPSIS
    Opens Brave with a search tab plus up to 3 curated result tabs for an
    open-ended question EKKO just answered out loud.

.DESCRIPTION
    Handler for llm_fallback/claude_code's open-ended-answer path (see
    llm_fallback/claude_code/fallback.py's open_research_tabs()), not a
    routing/intents.yaml intent -- this never runs off a matched command,
    only alongside a spoken `answer` when routing/route.py returned
    NO_MATCH and the fallback model decided the transcript was a genuine
    question rather than a misheard command. Standalone on purpose, same
    reasoning as web_search.ps1: no dependency on the caller, so it can be
    run and tested directly.

    Unlike web_search.ps1 (one Brave Search results tab for a closed-
    vocabulary-adjacent free_text slot), -Urls here is a list the fallback
    model found itself via its WebSearch tool while answering -- see
    fallback.py's parse_result() for how that list is capped and typed
    before it ever reaches a script argument, and validate_pick()'s
    docstring for why nothing from that model is ever trusted blindly
    elsewhere in this project either. This script re-validates independently
    anyway (each -Url must parse as an absolute http/https URI): a script
    argument is a script argument regardless of who upstream already
    checked it, and a bad scheme (file://, javascript:, etc.) must not
    reach Start-Process just because one caller already meant to filter it.

    All tabs open in a single Brave invocation -- Start-Process with
    multiple URL arguments in one call opens each as a new tab in the same
    window (or a new window if Brave wasn't already running), rather than
    one new window per URL.

.PARAMETER Query
    The topic to search for, spoken back into a Brave Search results URL
    (https://search.brave.com/search?q=...). URL-encoded below before it
    ever reaches Brave, same as web_search.ps1.

.PARAMETER Urls
    0-3 curated URLs the fallback model already found via WebSearch while
    answering, joined with '|'. A single delimited string, not a
    [string[]], because PowerShell's CLI argument binder only reliably
    splits a delimited scalar into an array when the delimiter is a comma
    -- and a URL's query string can legitimately contain a literal comma
    (confirmed in testing: "a,b?x=1,c" silently binds as three elements,
    not one), which would silently corrupt a curated URL. '|' isn't legal
    unencoded in a URL per RFC 3986, so it can't collide with real URL
    content; splitting on it here is unambiguous. Optional -- an answer
    that didn't need or find anything worth linking still gets the search
    tab alone. Anything that isn't an absolute http/https URI is dropped
    rather than failing the whole call; only the first 3 survivors after
    that filter are opened.

.EXIT CODES
    0 - launched successfully (search tab, plus any URLs that validated)
    1 - Brave not found at any known install path
    2 - launch failed
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Query,

    [string]$Urls = ''
)

$ErrorActionPreference = 'Stop'

$EncodedQuery = [Uri]::EscapeDataString($Query)
$SearchUrl = "https://search.brave.com/search?q=$EncodedQuery"

$CandidateUrls = if ($Urls) { $Urls -split '\|' } else { @() }

$ValidUrls = @()
foreach ($candidate in $CandidateUrls) {
    $parsed = $null
    if ([Uri]::TryCreate($candidate, [UriKind]::Absolute, [ref]$parsed) -and
        ($parsed.Scheme -eq 'http' -or $parsed.Scheme -eq 'https')) {
        $ValidUrls += $parsed.AbsoluteUri
    }
    else {
        Write-Warning "Dropping curated URL that isn't absolute http/https: '$candidate'"
    }
}
# Belt-and-suspenders cap -- fallback.py's parse_result() already limits
# this to 3, but this script doesn't trust its caller any more than it
# trusts the scheme above.
$ValidUrls = $ValidUrls | Select-Object -First 3 -Unique

$TabUrls = @($SearchUrl) + $ValidUrls

# Duplicated from scripts/web_search.ps1 rather than shared, matching this
# folder's "no shared state between scripts" convention (see
# scripts/README.md); update all copies if Brave moves.
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
    Start-Process -FilePath $BravePath -ArgumentList $TabUrls
    Write-Output "Opened $($TabUrls.Count) Brave tab(s) for '$Query' ($($ValidUrls.Count) curated)."
    exit 0
}
catch {
    Write-Error "Failed to open research tabs: $_"
    exit 2
}
