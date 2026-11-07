<#
.SYNOPSIS
    Native Spotify / Apple Music: launch, or search and play a request.
    Thin wrapper over scripts/media_player.py, which owns the parsing,
    detection, logic and exit codes.

.PARAMETER Request
    The raw utterance (play_media's `transcript` slot). Not validated
    against a set: it's free text, and media_player.py only ever puts it in
    a percent-encoded URI, never on a shell line (see its docstring).

.NOTES
    EXIT CODES: 0 done; 4 failed with a spoken reason (player not
    installed, nothing found, launch error); 1 no python found.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Request
)

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) {
    [Console]::Error.WriteLine('play_media: no pvenv or python found')
    exit 1
}

& $python (Join-Path $repoRoot 'scripts\media_player.py') $Request
exit $LASTEXITCODE
