<#
.SYNOPSIS
    Global media control for EKKO. Sends system-level media keys, so it
    works with whatever is currently playing, Apple Music in Brave,
    Spotify, YouTube, the native Apple Music app, doesn't matter.

.DESCRIPTION
    Uses keybd_event from user32.dll to send the same virtual key codes
    a keyboard's media buttons send. This is why it's player-agnostic,
    the OS routes the key to whatever app currently holds media focus,
    no browser automation or DOM scraping involved.

    Returns JSON on stdout so the Python orchestration layer can parse
    the outcome rather than guessing from exit codes alone.

.PARAMETER Action
    One of: PlayPause, Next, Previous, Stop, VolumeUp, VolumeDown, Mute

.EXAMPLE
    .\media_control.ps1 -Action PlayPause
    .\media_control.ps1 -Action Next
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('PlayPause', 'Next', 'Previous', 'Stop',
                 'VolumeUp', 'VolumeDown', 'Mute')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'

# Virtual key codes for the media keys. These are Windows constants,
# see the VK_MEDIA_* / VK_VOLUME_* entries in the Win32 docs.
$VirtualKeys = @{
    'PlayPause'  = 0xB3
    'Next'       = 0xB0
    'Previous'   = 0xB1
    'Stop'       = 0xB2
    'VolumeUp'   = 0xAF
    'VolumeDown' = 0xAE
    'Mute'       = 0xAD
}

# keybd_event is deprecated in favour of SendInput, but it's simpler to
# P/Invoke from PowerShell and works fine for media keys specifically.
# Only add the type if it isn't already loaded, re-adding throws.
if (-not ('EkkoMediaKeys' -as [type])) {
    Add-Type -Name EkkoMediaKeys -Namespace Win32 -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true)]
public static extern void keybd_event(
    byte bVk, byte bScan, uint dwFlags, System.UIntPtr dwExtraInfo);
'@
}

$KEYEVENTF_EXTENDEDKEY = 0x0001
$KEYEVENTF_KEYUP       = 0x0002

function Send-MediaKey {
    param([byte]$KeyCode)

    # Key down, then key up. Media keys are extended keys, hence the flag.
    [Win32.EkkoMediaKeys]::keybd_event(
        $KeyCode, 0, $KEYEVENTF_EXTENDEDKEY, [UIntPtr]::Zero)
    [Win32.EkkoMediaKeys]::keybd_event(
        $KeyCode, 0, $KEYEVENTF_EXTENDEDKEY -bor $KEYEVENTF_KEYUP, [UIntPtr]::Zero)
}

try {
    Send-MediaKey -KeyCode $VirtualKeys[$Action]

    # Note the caveat below: the OS gives no confirmation that any app
    # actually consumed the key, so "success" here means the key was
    # sent, not that music is now playing. If nothing has media focus,
    # this silently does nothing. The orchestration layer should treat
    # this as fire-and-forget rather than a verified state change.
    @{
        status = 'ok'
        action = $Action
        note   = 'Key sent. No confirmation available that a player consumed it.'
    } | ConvertTo-Json -Compress

    exit 0
}
catch {
    @{
        status = 'error'
        action = $Action
        error  = $_.Exception.Message
    } | ConvertTo-Json -Compress

    exit 1
}