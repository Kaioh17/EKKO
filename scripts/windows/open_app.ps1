<#
.SYNOPSIS
    Opens one of a fixed set of applications.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the open_app
    intent in routing/intents.yaml. Standalone on purpose: no dependency
    on the listener or the matcher, so it can be run and tested directly.

    Unlike the other handlers in this folder, this one takes a parameter,
    because open_app is one intent with a slot rather than one intent per
    application (see routing/intents.yaml).

    The ValidateSet on -App is deliberate duplication of the slot
    vocabulary in intents.yaml. Defence in depth: routing/slots.py already
    guarantees only a canonical config value reaches here, so this
    ValidateSet should be unreachable, and that's exactly why it's worth
    having. A bug upstream fails as a parameter validation error rather
    than launching something unintended. Keep the two lists in sync when
    adding an app; the pair of them is the whole permission boundary for
    this intent.

    Targets are Start Menu shortcuts rather than exe paths: none of these
    apps are on PATH, and their real install locations move with updates
    (VS Code and Discord in particular install per-user under AppData and
    version their own directories). The .lnk is the stable pointer
    Windows itself maintains. Paths are built from $env:APPDATA and
    $env:ProgramData rather than hardcoded like open_ghelper.ps1's, since
    unlike GHelper these are all formally installed apps in the standard
    locations.

.PARAMETER App
    Which application to open. Must be one of the values in the
    ValidateSet below, matching open_app's slot vocabulary.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\open_app.ps1 -App vscode

.EXIT CODES
    0 - launched successfully
    1 - shortcut not found for that app (installed somewhere non-standard,
        or uninstalled)
    2 - launch failed
#>

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('chrome', 'discord', 'vscode', 'cursor', 'steam', 'brave')]
    [string]$App
)

$ErrorActionPreference = 'Stop'

$UserPrograms = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$MachinePrograms = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'

$Shortcuts = @{
    'chrome'  = Join-Path $MachinePrograms 'Google Chrome.lnk'
    'discord' = Join-Path $UserPrograms 'Discord Inc\Discord.lnk'
    'vscode'  = Join-Path $UserPrograms 'Visual Studio Code\Visual Studio Code.lnk'
    'cursor'  = Join-Path $UserPrograms 'Cursor\Cursor.lnk'
    'steam'   = Join-Path $UserPrograms 'Steam\Steam.lnk'
    'brave'   = Join-Path $MachinePrograms 'Brave.lnk'
}

$Target = $Shortcuts[$App]

if (-not (Test-Path -LiteralPath $Target)) {
    Write-Error "No shortcut for '$App' at '$Target'. It may not be installed, or it installed somewhere non-standard -- update the `$Shortcuts table in this script."
    exit 1
}

try {
    Start-Process -FilePath $Target
    Write-Output "$App opened."
    exit 0
}
catch {
    Write-Error "Failed to open ${App}: $_"
    exit 2
}
