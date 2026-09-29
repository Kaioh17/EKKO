#!/usr/bin/env bash
# Opens a Brave Search results page for a spoken query. Linux counterpart
# to scripts/windows/web_search.ps1 -- see that file's header for why
# --query is free text, not a closed vocabulary (routing/intents.yaml's
# web_search slot), and why that's still safe: an argv list, never a
# shell string (routing/execute.py's module docstring, routing/host.py's
# command()).
#
# EXIT CODES
#   0 - launched successfully
#   1 - no browser and no xdg-open fallback found
set -euo pipefail

query=""
while [ $# -gt 0 ]; do
    case "$1" in
        --query) query="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Stdlib urllib for percent-encoding rather than a bash implementation --
# same "stdlib does it" reasoning as everywhere else python already does
# the JSON/formatting work in this project's Linux scripts.
encoded="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$query")"
url="https://search.brave.com/search?q=$encoded"

for candidate in brave-browser brave; do
    if command -v "$candidate" >/dev/null 2>&1; then
        nohup "$candidate" "$url" >/dev/null 2>&1 &
        disown
        echo "Searched Brave for '$query'."
        exit 0
    fi
done

if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$url" >/dev/null 2>&1 &
    disown
    echo "Searched (via xdg-open) for '$query'."
    exit 0
fi

echo "Brave not found on PATH, and no xdg-open fallback either." >&2
exit 1
