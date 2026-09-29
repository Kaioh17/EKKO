#!/usr/bin/env bash
# Answers a question about ONE laptop metric: battery, CPU, RAM, disk,
# GPU, or network. Linux counterpart to
# scripts/windows/system_metric_query.ps1.
#
# --metric is validated by scripts/system_metrics.py's own argparse
# choices= list, mirroring routing/intents.yaml's system_metric_query
# slot vocabulary -- same defence-in-depth reasoning
# scripts/windows/system_metric_query.ps1's ValidateSet documents.
#
# EXIT CODES
#   0 - answered (the metric itself may still be "unavailable")
#   1 - python/venv not found, or the query failed (including an invalid --metric)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # scripts/linux
SCRIPTS_ROOT="$(dirname "$SCRIPT_DIR")"                       # scripts
. "$SCRIPT_DIR/lib/system_metrics.sh"                         # defines REPO_ROOT, PYTHON_BIN

metric=""
while [ $# -gt 0 ]; do
    case "$1" in
        --metric) metric="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$PYTHON_BIN" ]; then
    echo "system_metric_query: no venv/bin/python or python3 found" >&2
    exit 1
fi

JSON_PATH="$(mktemp "${TMPDIR:-/tmp}/ekko_report_XXXXXX.json")"

if ! "$PYTHON_BIN" "$SCRIPTS_ROOT/system_metrics.py" --metric "$metric" --json-out "$JSON_PATH"; then
    echo "system_metric_query: python query failed" >&2
    exit 1
fi

open_report_window "$JSON_PATH" "EKKO"
exit 0
