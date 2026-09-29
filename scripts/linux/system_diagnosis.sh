#!/usr/bin/env bash
# Reports on the laptop's own health: CPU, RAM, disk, battery, GPU,
# network. Linux counterpart to scripts/windows/system_diagnosis.ps1.
#
# All metric collection, threshold flagging, and the SPEAK: line live in
# scripts/system_metrics.py's --diagnosis mode -- see that file's own
# docstring for why the Windows/Linux split isn't symmetric. This
# script's whole job is: run it, relay its stdout verbatim (the
# "SPEAK: <text>" line routing/execute.py's spoken_override() looks for),
# and try to open the same visual companion window
# scripts/windows/system_diagnosis.ps1 opens, via
# lib/system_metrics.sh's open_report_window.
#
# EXIT CODES
#   0 - ran (individual metrics may still be "unavailable")
#   1 - python/venv not found, or the query failed outright
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/linux
SCRIPTS_ROOT="$(dirname "$SCRIPT_DIR")"                       # scripts
. "$SCRIPT_DIR/lib/system_metrics.sh"                         # defines REPO_ROOT, PYTHON_BIN

if [ -z "$PYTHON_BIN" ]; then
    echo "system_diagnosis: no venv/bin/python or python3 found" >&2
    exit 1
fi

JSON_PATH="$(mktemp "${TMPDIR:-/tmp}/ekko_report_XXXXXX.json")"

if ! "$PYTHON_BIN" "$SCRIPTS_ROOT/system_metrics.py" --diagnosis --json-out "$JSON_PATH"; then
    echo "system_diagnosis: python query failed" >&2
    exit 1
fi

open_report_window "$JSON_PATH" "EKKO"
exit 0
