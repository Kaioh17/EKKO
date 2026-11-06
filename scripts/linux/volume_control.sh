#!/usr/bin/env bash
# Speaker volume for EKKO: up / down / mute / unmute / a level 0-100.
# Thin wrapper over scripts/system_audio.py (pulsectl), which owns the
# logic, logging and exit codes.
#
# --action: up, down, mute, unmute, or a number 0-100
#
# EXIT CODES
#   0 - done
#   1 - bad --action, no python, or pulsectl missing / no PulseAudio
#   3 - already at that level
set -euo pipefail

action=""
while [ $# -gt 0 ]; do
    case "$1" in
        --action) action="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

case "$action" in
    up|down|mute|unmute) ;;
    *[!0-9]*|"") echo "volume_control: bad --action '$action'" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "volume_control: no venv/bin/python or python3 found" >&2
    exit 1
fi

exec "$PYTHON_BIN" "$REPO_ROOT/scripts/system_audio.py" "$action"
