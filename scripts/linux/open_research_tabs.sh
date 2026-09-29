#!/usr/bin/env bash
# Opens Brave with a search tab plus up to 3 curated result tabs, for an
# open-ended question EKKO just answered out loud. Linux counterpart to
# scripts/windows/open_research_tabs.ps1 -- see that file's header for the
# full reasoning (llm_fallback/claude_code/fallback.py's
# open_research_tabs()). Not named by any routing/intents.yaml handler,
# same as the Windows original -- called directly via routing/host.py's
# command().
#
# --urls is '|'-delimited, not comma -- see open_research_tabs.ps1's own
# param doc for why (a URL's query string can legitimately contain a
# literal comma). Each candidate is re-validated as absolute http/https
# here, independently of whatever the caller already checked -- same
# "re-validate at the boundary that acts on it" reasoning
# routing/execute.py's module docstring gives.
#
# EXIT CODES
#   0 - launched successfully
#   1 - no browser found
set -euo pipefail

query="" urls=""
while [ $# -gt 0 ]; do
    case "$1" in
        --query) query="$2"; shift 2 ;;
        --urls) urls="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

encoded="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$query")"
search_url="https://search.brave.com/search?q=$encoded"

# Belt-and-suspenders cap, same as open_research_tabs.ps1's own: capped at
# 3 curated URLs even though fallback.py's parse_result() already limits
# this upstream.
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
        [ "${#valid_urls[@]}" -ge 3 ] && break
    done
fi

tab_urls=("$search_url" "${valid_urls[@]}")

for candidate in brave-browser brave; do
    if command -v "$candidate" >/dev/null 2>&1; then
        nohup "$candidate" "${tab_urls[@]}" >/dev/null 2>&1 &
        disown
        echo "Opened ${#tab_urls[@]} Brave tab(s) for '$query' (${#valid_urls[@]} curated)."
        exit 0
    fi
done

echo "Brave not found on PATH." >&2
exit 1
