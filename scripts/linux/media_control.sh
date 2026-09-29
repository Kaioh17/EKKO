#!/usr/bin/env bash
# Global media control for EKKO, via playerctl (MPRIS) -- Linux
# counterpart to scripts/windows/media_control.ps1's keybd_event
# media-key simulation. playerctl talks to whatever MPRIS-compliant
# player currently has focus (most Linux media players and browsers
# implement MPRIS), so this is player-agnostic the same way the Windows
# version's OS-level media keys are, just via D-Bus instead of a
# simulated keypress.
#
# Returns JSON on stdout, same shape as media_control.ps1, so
# scripts/media_handler.py's parsing doesn't need an OS branch.
#
# --action: PlayPause, Next, Previous, Stop, VolumeUp, VolumeDown, Mute
#
# EXIT CODES
#   0 - command sent
#   1 - unknown --action
#   2 - playerctl not installed, or no player available
set -euo pipefail

action=""
while [ $# -gt 0 ]; do
    case "$1" in
        --action) action="$2"; shift 2 ;;
        *) echo "{\"status\": \"error\", \"error\": \"Unknown argument: $1\"}"; exit 1 ;;
    esac
done

if ! command -v playerctl >/dev/null 2>&1; then
    echo "{\"status\": \"error\", \"action\": \"$action\", \"error\": \"playerctl not installed\"}"
    exit 2
fi

# VolumeUp/VolumeDown/Mute have no MPRIS equivalent -- playerctl controls
# playback, not system/player volume. wpctl (PipeWire) is the closest
# native equivalent on most current distros; amixer (ALSA) as a fallback.
# ponytail: only the two most common volume tools are tried; add another
# here if yours isn't one of them.
run_volume() {
    local step="$1"  # "+5%" / "-5%" / "toggle-mute"
    if command -v wpctl >/dev/null 2>&1; then
        wpctl set-volume @DEFAULT_AUDIO_SINK@ "$step" >/dev/null 2>&1 && return 0
    fi
    if command -v amixer >/dev/null 2>&1; then
        case "$step" in
            "+5%") amixer -q sset Master 5%+ ;;
            "-5%") amixer -q sset Master 5%- ;;
            toggle-mute) amixer -q sset Master toggle ;;
        esac >/dev/null 2>&1 && return 0
    fi
    return 1
}

case "$action" in
    PlayPause) cmd=(playerctl play-pause) ;;
    Next)      cmd=(playerctl next) ;;
    Previous)  cmd=(playerctl previous) ;;
    Stop)      cmd=(playerctl stop) ;;
    VolumeUp)
        if run_volume "+5%"; then
            echo "{\"status\": \"ok\", \"action\": \"$action\", \"note\": \"Volume raised via wpctl/amixer.\"}"
            exit 0
        fi
        echo "{\"status\": \"error\", \"action\": \"$action\", \"error\": \"no wpctl or amixer found\"}"
        exit 2
        ;;
    VolumeDown)
        if run_volume "-5%"; then
            echo "{\"status\": \"ok\", \"action\": \"$action\", \"note\": \"Volume lowered via wpctl/amixer.\"}"
            exit 0
        fi
        echo "{\"status\": \"error\", \"action\": \"$action\", \"error\": \"no wpctl or amixer found\"}"
        exit 2
        ;;
    Mute)
        if run_volume "toggle-mute"; then
            echo "{\"status\": \"ok\", \"action\": \"$action\", \"note\": \"Mute toggled via wpctl/amixer.\"}"
            exit 0
        fi
        echo "{\"status\": \"error\", \"action\": \"$action\", \"error\": \"no wpctl or amixer found\"}"
        exit 2
        ;;
    *)
        echo "{\"status\": \"error\", \"error\": \"Unknown action: $action\"}"
        exit 1
        ;;
esac

# PlayPause/Next/Previous/Stop: playerctl gives no confirmation that a
# player actually consumed the command, same caveat
# media_control.ps1's own JSON note carries for keybd_event.
if "${cmd[@]}" >/dev/null 2>&1; then
    echo "{\"status\": \"ok\", \"action\": \"$action\", \"note\": \"Command sent via playerctl. No confirmation available that a player consumed it.\"}"
    exit 0
else
    echo "{\"status\": \"error\", \"action\": \"$action\", \"error\": \"playerctl found no player to control\"}"
    exit 2
fi
