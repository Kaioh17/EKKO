<#
.SYNOPSIS
    Opens the Apple Music web player in Brave.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    open_apple_music intent in routing/intents.yaml. Standalone on
    purpose: no dependency on the listener or the matcher, so it can be
    run and tested directly.

    Opens the web player (music.apple.com) rather than a native app --
    there's no Apple Music app installed on this machine, and the web
    player needs nothing installed to work. Launched specifically in
    Brave, rather than Start-Process'ing the bare URL and letting
    Windows hand it to whatever the default browser is, since Brave is
    the intended browser here (see open_app.ps1's note on Brave being
    the default on this machine).

.EXIT CODES
    0 - launched successfully
    1 - Brave not found at any known install path
    2 - launch failed
#>

$ErrorActionPreference = 'Stop'

$Url = 'https://music.apple.com'

# The three locations Brave's installer can put brave.exe: per-machine
# (64-bit), per-machine (32-bit host), per-user. Checked in that order,
# first one found wins. Duplicated in scripts/web_search.ps1 rather than
# shared, matching this folder's "no shared state between scripts"
# convention (see scripts/README.md); update both if Brave moves.
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
    Write-Output 'Apple Music opened.'
    exit 0
}
catch {
    Write-Error "Failed to open Apple Music: $_"
    exit 2
}
