#!/usr/bin/env bash
# Shared report-launch helper for system_diagnosis.sh, system_metric_query.sh,
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
# files call it. This file's only job is launching the same render_report.py /
# daily_briefing.py scripts, pointed at the JSON file system_metrics.py just wrote.
#
# Sourced (`. lib/system_metrics.sh`), not executed -- like its Windows
# counterpart, this only defines functions.
#
# Additive, never load-bearing: EKKO has already spoken by the time either
# function here runs, so a missing venv is a log
# line on stdout, never a failed handler -- same posture the .ps1 version
# documents for a missing pvenv.

# Three levels up from scripts/linux/lib -- lib -> linux -> scripts -> repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || true)"

# open_report_window <json_path> <window_title>
# Runs scripts/render_report.py <json_path> in the background -- the Linux
# counterpart to Open-ReportWindow.
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

    nohup "$PYTHON_BIN" "$py_script" "$json_path" >/dev/null 2>&1 &
    disown 2>/dev/null || true
    echo "$label: sent to ekko-ui"
}
