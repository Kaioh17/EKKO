#!/usr/bin/env bash
# Opens the Apple Music web player in Brave. Linux counterpart to
# scripts/windows/open_apple_music.ps1 -- fixed URL, no slot, no
# arguments.
#
# EXIT CODES
#   0 - launched successfully
#   1 - no browser and no xdg-open fallback found
set -euo pipefail

url="https://music.apple.com"

for candidate in brave-browser brave; do
    if command -v "$candidate" >/dev/null 2>&1; then
        nohup "$candidate" "$url" >/dev/null 2>&1 &
        disown
        echo "Apple Music opened."
        exit 0
    fi
done

if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$url" >/dev/null 2>&1 &
    disown
    echo "Apple Music opened (via xdg-open)."
    exit 0
fi

echo "Brave not found on PATH, and no xdg-open fallback either." >&2
exit 1
