<#
.SYNOPSIS
    Opens a Brave Search results page for a spoken query.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    web_search intent in routing/intents.yaml. Standalone on purpose: no
    dependency on the listener or the matcher, so it can be run and
    tested directly.

    Unlike every other parameterised handler in this folder, -Query is
    free text, not a closed vocabulary checked with [ValidateSet(...)] --
    a search query genuinely can't be a fixed list of values. See the
    comment on web_search in routing/intents.yaml and
    routing/slots.py's find_free_text for why that's a deliberate,
    narrow exception to this project's closed-vocabulary rule, and
    routing/execute.py's module docstring for why it's still safe:
    the invocation is built as an argument list, never a shell string
    (shell=False), so there's no shell for -Query to inject into, and
    it's URL-encoded below before it ever reaches Brave.

.PARAMETER Query
    The search terms, exactly as routing/slots.py's find_free_text
    extracted them (lowercased, punctuation stripped -- see
    routing/config.py's normalise). Not checked against any list.

.EXIT CODES
    0 - launched successfully
    1 - Brave not found at any known install path
    2 - launch failed
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$Query
)

$ErrorActionPreference = 'Stop'

$EncodedQuery = [Uri]::EscapeDataString($Query)
$Url = "https://search.brave.com/search?q=$EncodedQuery"

# Duplicated from scripts/open_apple_music.ps1 rather than shared, matching
# this folder's "no shared state between scripts" convention (see
# scripts/README.md); update both if Brave moves.
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
    Start-Process -FilePath $BravePath -ArgumentList $Url
    Write-Output "Searched Brave for '$Query'."
    exit 0
}
catch {
    Write-Error "Failed to search: $_"
    exit 2
}
