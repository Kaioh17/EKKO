#!/usr/bin/env bash
# Opens a GUI process monitor. Linux counterpart to
# scripts/windows/open_task_manager.ps1 -- there's no single built-in
# binary the way taskmgr.exe is, so this tries the common desktop
# environment monitors in order, falling back to `top` in a terminal.
#
# EXIT CODES
#   0 - launched successfully
#   1 - no monitor or terminal emulator found
set -euo pipefail

for candidate in gnome-system-monitor ksysguard xfce4-taskmanager; do
    if command -v "$candidate" >/dev/null 2>&1; then
        nohup "$candidate" >/dev/null 2>&1 &
        disown
        echo "$candidate opened."
        exit 0
    fi
done

# No GUI monitor installed -- fall back to top in a terminal.
for term in gnome-terminal konsole x-terminal-emulator xterm; do
    if command -v "$term" >/dev/null 2>&1; then
        nohup "$term" -e top >/dev/null 2>&1 &
        disown
        echo "Opened top in $term."
        exit 0
    fi
done

echo "No process monitor or terminal emulator found." >&2
exit 1
