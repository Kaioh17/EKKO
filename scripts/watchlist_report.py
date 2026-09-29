"""Price and % change for every ticker in config/daily_briefing.yaml's
`tickers` list -- the "watchlist" behind the watchlist_report intent
(routing/intents.yaml).

Not a handler itself, not named by any intent: scripts/watchlist_report.ps1
runs this through pvenv and turns the JSON below into a spoken SPEAK: line
plus a rich-rendered report window, the same .ps1 -> .py split
check_memory_activity.ps1/.py already use (and for the same reason: the
fetch logic is easier to keep resilient in Python).

Deliberately reuses scripts/daily_briefing.py's fetch_all_stocks() and
load_watchlist_config() rather than re-fetching quotes its own way -- one
definition of "what's on the watchlist" and one yfinance call shape, so
this command and the daily briefing's Stocks section can never disagree.
What it does NOT do is any of daily_briefing's slower extras (HN news, the
Gemini commentary call, a second TTS utterance, Brave tabs): this is just
the watchlist, kept fast enough to finish on the voice pipeline's critical
path inside routing/execute.py's handler timeout (15s) and still answer
out loud with real numbers. include_news=False on the fetch skips the
per-ticker news round trip daily_briefing needs but this doesn't -- see
fetch_stock()'s docstring.

Runs under pvenv (yfinance lives there, not the root venv) -- same
interpreter and reasoning as daily_briefing.py / render_report.py, see
scripts/README.md's Dependencies note.

Output: a single JSON object on stdout, nothing else --
    {"stocks": [{"ticker","name","price","change_pct","error"}, ...], "count": N}
`name` is the friendly label from config/daily_briefing.yaml's
`ticker_names` map (or the raw symbol when unmapped) -- what
watchlist_report.ps1 speaks and shows instead of "GC=F". `error` is true
when the ticker couldn't be priced at all (delisted, mistyped, or the
fetch timed out); price/change_pct are then null.

Usage:
    python scripts/watchlist_report.py
    python scripts/watchlist_report.py --timeout 6
    python scripts/watchlist_report.py --speak --json-out PATH
        Per-ticker detail lines + a "SPEAK: <text>" line -- the
        watchlist_report contract scripts/windows/watchlist_report.ps1
        builds itself via PowerShell's native ConvertFrom-Json. --speak
        exists for scripts/linux/watchlist_report.sh, which has no
        equivalent JSON parser -- see scripts/system_metrics.py's own
        docstring for the fuller reasoning. Purely additive: the default
        (no --speak) JSON-only behaviour above is unchanged.
"""

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# scripts/ is on sys.path when this is launched as
# `python scripts\watchlist_report.py` (same invocation shape as
# daily_briefing.py), so this is a flat import, not scripts.daily_briefing.
from daily_briefing import display_name, fetch_all_stocks, load_watchlist_config  # noqa: E402

# Lower than daily_briefing.py's own STOCK_TIMEOUT_SECONDS (10s): this
# runs synchronously inside routing/execute.py's 15s handler timeout, with
# Python/pvenv startup already eating a second or two, so the quote fetch
# needs to come back well under that. fetch_all_stocks() runs every ticker
# concurrently and bounds each with future.result(timeout=...), so this is
# close to the real wall-clock cost, not a per-ticker sum.
DEFAULT_TIMEOUT_SECONDS = 8.0


def build_report(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    config = load_watchlist_config()
    tickers = config.get("tickers", [])
    names = config.get("ticker_names", {})
    stocks = fetch_all_stocks(tickers, timeout=timeout, include_news=False)
    rows = [
        {
            "ticker": s["ticker"],
            "name": display_name(s["ticker"], names),
            "price": s.get("price"),
            "change_pct": s.get("change_pct"),
            # One flat bool for the .ps1 to branch on, rather than making it
            # re-derive "unpriced" from a null price plus an error string.
            "error": bool(s.get("error")) and s.get("price") is None,
        }
        for s in stocks
    ]
    return {"stocks": rows, "count": len(rows)}


def _label(stock: dict) -> str:
    return f"{stock['name']} ({stock['ticker']})" if stock["name"] != stock["ticker"] else stock["ticker"]


def print_speak(report: dict, json_out: str | None) -> None:
    """The watchlist_report output contract:
    scripts/windows/watchlist_report.ps1's stdout shape, built here
    instead so scripts/linux/watchlist_report.sh can just relay it -- see
    this module's docstring and scripts/system_metrics.py's for why.
    """
    stocks = report["stocks"]
    for s in stocks:
        if s["error"] or s["price"] is None:
            print(f"{_label(s):<18} unavailable")
        else:
            chg = "n/a" if s["change_pct"] is None else f"{s['change_pct']:+.2f}%"
            print(f"{_label(s):<18} ${s['price']:.2f}  {chg}")

    parts = []
    for s in stocks:
        if s["error"] or s["price"] is None:
            parts.append(f"{s['name']} is unavailable")
        elif s["change_pct"] is None:
            parts.append(f"{s['name']} is at {s['price']:.2f}")
        else:
            direction = "up" if s["change_pct"] >= 0 else "down"
            parts.append(f"{s['name']} is {direction} {abs(s['change_pct']):.1f} percent")
    summary = "Your watchlist: " + ", ".join(parts) + "."
    print(f"SPEAK: {summary}")

    if json_out:
        rows = []
        for s in stocks:
            if s["error"] or s["price"] is None:
                rows.append({"name": _label(s), "detail": "unavailable", "flagged": True})
            else:
                chg = "" if s["change_pct"] is None else f" ({s['change_pct']:+.1f}%)"
                rows.append({
                    "name": _label(s),
                    "detail": f"${s['price']:.2f}{chg}",
                    # Flag a red row on a hard drop, same threshold as
                    # watchlist_report.ps1's own $metricRows.
                    "flagged": s["change_pct"] is not None and s["change_pct"] <= -3,
                })
        Path(json_out).write_text(json.dumps({"summary": summary, "metrics": rows}), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Seconds to wait for the quote fetch (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--speak", action="store_true",
        help="Detail lines + SPEAK: line (see module docstring). scripts/linux/"
        "watchlist_report.sh's mode; the .ps1 handler never passes this.",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="--speak only: write the {summary, metrics} report-window payload here.",
    )
    args = parser.parse_args()

    report = build_report(timeout=args.timeout)
    if args.speak:
        # Same "nothing configured" guard watchlist_report.ps1 applies
        # before building its own summary -- config/daily_briefing.yaml's
        # `tickers` list is empty, not a fetch failure, but there's
        # nothing to speak either way.
        if report["count"] < 1:
            print("watchlist_report: no tickers configured (config/daily_briefing.yaml)", file=sys.stderr)
            raise SystemExit(1)
        print_speak(report, args.json_out)
    else:
        print(json.dumps(report))
