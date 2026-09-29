#!/usr/bin/env bash
# Reports how the stock watchlist is doing right now -- price and %
# change for every ticker in config/daily_briefing.yaml. Linux counterpart
# to scripts/windows/watchlist_report.ps1 -- shells out to the same
# scripts/watchlist_report.py (yfinance) rather than reimplementing the
# fetch. --speak/--json-out are the modes added to that file for this
# script's benefit -- see its module docstring.
#
# yfinance is a scripts/-only dependency on this machine's pvenv, not in
# requirements.txt (see scripts/README.md's Dependencies note) -- on
# Linux that's your venv/ instead: `venv/bin/pip install yfinance`.
#
# EXIT CODES
#   0 - reported (individual tickers may still be "unavailable")
#   1 - python/venv not found, or the query failed (including no tickers configured)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/linux
SCRIPTS_ROOT="$(dirname "$SCRIPT_DIR")"                       # scripts
. "$SCRIPT_DIR/lib/system_metrics.sh"                         # defines REPO_ROOT, PYTHON_BIN

if [ -z "$PYTHON_BIN" ]; then
    echo "watchlist_report: no venv/bin/python or python3 found" >&2
    exit 1
fi

JSON_PATH="$(mktemp "${TMPDIR:-/tmp}/ekko_report_XXXXXX.json")"

if ! "$PYTHON_BIN" "$SCRIPTS_ROOT/watchlist_report.py" --timeout 8 --speak --json-out "$JSON_PATH"; then
    echo "watchlist_report: python query failed" >&2
    exit 1
fi

open_report_window "$JSON_PATH" "EKKO"
exit 0
