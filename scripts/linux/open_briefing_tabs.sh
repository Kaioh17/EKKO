#!/usr/bin/env bash
# Opens the given URLs in Brave, one tab each -- no search tab. Linux
# counterpart to scripts/windows/open_briefing_tabs.ps1, called by
# scripts/daily_briefing.py's open_top_news_tabs() via routing/host.py's
# command(). Not named by any routing/intents.yaml handler, same as the
# Windows original.
#
# --urls is '|'-delimited, same reasoning as open_research_tabs.sh.
# Re-validated here independently of whatever daily_briefing.py already
# checked.
#
# EXIT CODES
#   0 - launched successfully (0 or more URLs opened; 0 is not an error)
#   1 - no browser found
set -euo pipefail

urls=""
while [ $# -gt 0 ]; do
    case "$1" in
        --urls) urls="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

valid_urls=()
if [ -n "$urls" ]; then
    IFS='|' read -ra candidates <<< "$urls"
    for candidate in "${candidates[@]}"; do
        if python3 -c 'import sys, urllib.parse as u
p = u.urlparse(sys.argv[1])
sys.exit(0 if p.scheme in ("http", "https") and p.netloc else 1)' "$candidate"; then
            valid_urls+=("$candidate")
        else
            echo "Dropping URL that isn't absolute http/https: '$candidate'" >&2
        fi
        [ "${#valid_urls[@]}" -ge 10 ] && break
    done
fi

if [ "${#valid_urls[@]}" -eq 0 ]; then
    echo "No valid URLs to open."
    exit 0
fi

for candidate in brave-browser brave; do
    if command -v "$candidate" >/dev/null 2>&1; then
        nohup "$candidate" "${valid_urls[@]}" >/dev/null 2>&1 &
        disown
        echo "Opened ${#valid_urls[@]} Brave tab(s)."
        exit 0
    fi
done

echo "Brave not found on PATH." >&2
exit 1
