#!/usr/bin/env bash
# Reports which processes are using the most memory right now. Linux
# counterpart to scripts/windows/check_memory_activity.ps1 -- shells out
# to the same scripts/check_memory_activity.py (psutil, already portable,
# unchanged for its default JSON mode) rather than reimplementing the
# scan. --speak/--json-out are the modes added to that file for this
# script's benefit -- see its module docstring.
#
# EXIT CODES
#   0 - reported
#   1 - python/venv not found, or the query failed
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/linux
SCRIPTS_ROOT="$(dirname "$SCRIPT_DIR")"                       # scripts
. "$SCRIPT_DIR/lib/system_metrics.sh"                         # defines REPO_ROOT, PYTHON_BIN

if [ -z "$PYTHON_BIN" ]; then
    echo "check_memory_activity: no venv/bin/python or python3 found" >&2
    exit 1
fi

JSON_PATH="$(mktemp "${TMPDIR:-/tmp}/ekko_report_XXXXXX.json")"

if ! "$PYTHON_BIN" "$SCRIPTS_ROOT/check_memory_activity.py" --limit 15 --speak --json-out "$JSON_PATH"; then
    echo "check_memory_activity: python query failed" >&2
    exit 1
fi

open_report_window "$JSON_PATH" "EKKO"
exit 0
