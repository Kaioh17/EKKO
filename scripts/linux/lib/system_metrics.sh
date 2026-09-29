#!/usr/bin/env bash
# Shared terminal-launch helper for system_diagnosis.sh, system_metric_query.sh,
# and daily_briefing.sh -- the Linux counterpart to
# scripts/windows/lib/system_metrics.ps1's Open-ReportWindow /
# Open-BriefingWindow.
#
# NOT symmetric with the Windows file: metric collection, threshold
# flagging, and the SPEAK: line all live in scripts/system_metrics.py's
# --diagnosis/--briefing/--metric modes instead of here. See that file's
# own docstring for why -- PowerShell parses its own JSON natively
# (ConvertFrom-Json), bash doesn't without adding a new dependency, so
# Python already owns the full output contract by the time these .sh
# files call it. This file's only job is finding a terminal emulator and
# launching the same render_report.py / daily_briefing.py windows already
# use, pointed at the JSON file system_metrics.py just wrote.
#
# Sourced (`. lib/system_metrics.sh`), not executed -- like its Windows
# counterpart, this only defines functions.
#
# Additive, never load-bearing: EKKO has already spoken by the time either
# function here runs, so a missing terminal emulator or venv is a log
# line on stdout, never a failed handler -- same posture the .ps1 version
# documents for a missing wt.exe/pvenv.

# Three levels up from scripts/linux/lib -- lib -> linux -> scripts -> repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || true)"

# ponytail: first terminal emulator found on PATH wins, x-terminal-emulator
# (Debian's alternatives symlink) and bare xterm both get xterm-style
# args, which isn't guaranteed for every possible target of that symlink.
# Upgrade path if that bites in practice: detect the real target
# (readlink -f) and branch like gnome-terminal/konsole below.
_find_terminal() {
    for term in gnome-terminal konsole x-terminal-emulator xterm; do
        if command -v "$term" >/dev/null 2>&1; then
            echo "$term"
            return 0
        fi
    done
    return 1
}

# open_report_window <json_path> <window_title>
# Runs scripts/render_report.py <json_path> in a new terminal window --
# the Linux counterpart to Open-ReportWindow's wt.exe new-tab.
open_report_window() {
    _open_window "$1" "${2:-EKKO}" "$REPO_ROOT/scripts/render_report.py" "Report window"
}

# open_briefing_window <json_path>
# Runs scripts/daily_briefing.py <json_path> -- the Linux counterpart to
# Open-BriefingWindow.
open_briefing_window() {
    _open_window "$1" "EKKO Daily Briefing" "$REPO_ROOT/scripts/daily_briefing.py" "Briefing window"
}

_open_window() {
    local json_path="$1" title="$2" py_script="$3" label="$4"

    if [ -z "$PYTHON_BIN" ] || [ ! -f "$py_script" ]; then
        echo "$label: skipped (venv/python3 or $(basename "$py_script") not found)"
        return 0
    fi

    local term
    if ! term="$(_find_terminal)"; then
        echo "$label: skipped (no terminal emulator found)"
        return 0
    fi

    case "$term" in
        gnome-terminal)
            nohup "$term" --title="$title" -- "$PYTHON_BIN" "$py_script" "$json_path" >/dev/null 2>&1 &
            ;;
        konsole)
            nohup "$term" -p tabtitle="$title" -e "$PYTHON_BIN" "$py_script" "$json_path" >/dev/null 2>&1 &
            ;;
        *)
            nohup "$term" -T "$title" -e "$PYTHON_BIN" "$py_script" "$json_path" >/dev/null 2>&1 &
            ;;
    esac
    disown 2>/dev/null || true
    echo "$label: opened"
}
