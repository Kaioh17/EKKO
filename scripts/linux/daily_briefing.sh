#!/usr/bin/env bash
# "What's happening today" -- a fast system-health SPEAK: line, then hands
# off to scripts/daily_briefing.py in a new terminal window for tech news,
# a stock watchlist, and LLM commentary. Linux counterpart to
# scripts/windows/daily_briefing.ps1 -- see that file's header for why the
# split matters: routing/execute.py only waits on THIS script, which does
# only fast local work before exiting; the slow, genuinely-external part
# runs entirely off the voice pipeline's critical path, in a process
# nothing here waits on.
#
# EXIT CODES
#   0 - ran (individual metrics may still be "unavailable")
#   1 - python/venv not found, or the query failed outright
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/linux
SCRIPTS_ROOT="$(dirname "$SCRIPT_DIR")"                       # scripts
. "$SCRIPT_DIR/lib/system_metrics.sh"                         # defines REPO_ROOT, PYTHON_BIN

if [ -z "$PYTHON_BIN" ]; then
    echo "daily_briefing: no venv/bin/python or python3 found" >&2
    exit 1
fi

JSON_PATH="$(mktemp "${TMPDIR:-/tmp}/ekko_briefing_XXXXXX.json")"

if ! "$PYTHON_BIN" "$SCRIPTS_ROOT/system_metrics.py" --briefing --json-out "$JSON_PATH"; then
    echo "daily_briefing: python query failed" >&2
    exit 1
fi

open_briefing_window "$JSON_PATH"
exit 0
