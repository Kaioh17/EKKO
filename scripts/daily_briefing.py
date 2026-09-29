"""Renders EKKO's daily_briefing report: system health (already computed,
handed in as JSON) plus tech news and a stock watchlist, both fetched
here, in whatever console window is running it.

Not a handler itself, not named by any intent in routing/intents.yaml.
Launched by scripts/lib/system_metrics.ps1's Open-BriefingWindow, in a new
wt.exe tab, same launch shape as Open-ReportWindow's render_report.py --
but unlike that script, this one does real work of its own after launch
rather than just rendering data it was handed. That's the whole point:
scripts/daily_briefing.ps1 (what routing/execute.py actually waits on)
only computes system metrics and starts this process, so the slow,
genuinely-external work below -- HTTP fetches, yfinance, a Gemini call,
a second TTS utterance -- happens entirely off the voice pipeline's
critical path. See scripts/daily_briefing.ps1's header comment.

Runs under pvenv, same as render_report.py and the listener itself --
see system/README.md's "Native Windows only (pvenv), not the WSL venv/"
note. pvenv already has everything: requests/rich (already used by
render_report.py), yfinance (added alongside them, see scripts/README.md's
Dependencies note), and Piper/sounddevice (the listener's own TTS deps),
so speak_text() below just shells out to feedback/speech.py with this
same interpreter (sys.executable) rather than needing a second venv.

Every fetch/LLM/TTS step below is wrapped so one failure (no internet, an
API rate limit, Gemini unreachable, TTS erroring) degrades only that
section to an "unavailable" note -- never crashes the whole window, same
bug-tolerant posture as scripts/lib/system_metrics.ps1's Get-*Metric
functions and llm_fallback/gemini/client.py's GeminiError handling.

News narration and stock commentary (fetch_news_narration()/
fetch_commentary() below) call llm_fallback/gemini/client.py's
generate_content() directly -- Gemini instead of `claude -p`, confirmed
working on real news text and a real multi-story batch before this was
wired in (see llm_fallback/gemini/README.md's "What's migrated" for the
reasoning: no billing needed, since summarizing text already given in the
prompt is a different job from live web search, which is the thing
Gemini's free tier can't do). A small, self-contained call rather than an
import of llm_fallback/gemini/fallback_gemini.py's run_gemini(): that
module's machinery (FallbackOutcome, validate_pick(), the NO_MATCH intent
schema) exists to keep an LLM from ever triggering an action, which
doesn't apply here -- this call only ever produces spoken/displayed text.

Usage:
    python scripts/daily_briefing.py %TEMP%\\ekko_briefing_....json
"""

import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import ekko_ui
import requests
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Needed to import llm_fallback.gemini.client below -- same sys.path
# pattern llm_fallback/gemini/fallback_gemini.py and
# llm_fallback/ollama/fallback_ollama.py both use, for the same reason:
# resolve regardless of invocation cwd (this script is launched as
# `python scripts\daily_briefing.py <json path>`, which puts scripts/ on
# sys.path, not the repo root).
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from llm_fallback.gemini.client import GeminiError, extract_text, generate_content  # noqa: E402
from routing import host  # noqa: E402

# personal/daily_briefing.yaml (your own watchlist, gitignored) wins over the
# generic config/daily_briefing.yaml that ships with the repo.
_SHIPPED_CONFIG = _REPO_ROOT / "config" / "daily_briefing.yaml"
DEFAULT_CONFIG_PATH = host.PERSONAL_DIR / "daily_briefing.yaml"
if not DEFAULT_CONFIG_PATH.is_file():
    DEFAULT_CONFIG_PATH = _SHIPPED_CONFIG

_SPEECH_SCRIPT = _REPO_ROOT / "feedback" / "speech.py"

# Cross-process signal to listener/vad_listener.py: written the moment
# this script is done narrating (see signal_narration_done() below), so
# that process -- a different one, with no visibility into this window --
# knows it's safe to re-arm its own "anything else?" follow-up instead of
# either racing this script's narration or never re-arming at all. Path
# and reasoning must stay identical to vad_listener.py's own
# BRIEFING_NARRATION_FLAG_PATH constant; duplicated rather than imported
# since this script deliberately doesn't import anything from listener/
# (different venv assumptions historically, see the module docstring).
NARRATION_FLAG_PATH = Path(tempfile.gettempdir()) / "ekko_briefing_narration_done.flag"

# One extra "more reading" link, unparsed -- see the module docstring and
# the user-facing decision this came from: Techmeme has no public API, so
# its homepage is listed, never scraped.
TECHMEME_URL = "https://www.techmeme.com/"
HN_SEARCH_UI = "https://hn.algolia.com/"

# Per-source timeouts. Deliberately short and per-topic/per-ticker rather
# than one big timeout for "all news" or "all stocks": a single slow HN
# query or a single delisted ticker should cost seconds, not stall every
# other topic/ticker behind it. ThreadPoolExecutor below runs them
# concurrently, so the real wall-clock cost of N topics is close to the
# slowest one, not the sum.
HN_TIMEOUT_SECONDS = 8.0
STOCK_TIMEOUT_SECONDS = 10.0
# Lower than llm_fallback/claude_code/fallback.py's old 20s guess for the
# same call shape -- a direct HTTPS call has no `claude` CLI cold-start to
# pay for. Matches llm_fallback/gemini/client.py's own DEFAULT_TIMEOUT,
# not imported from there since a caller is free to want a different
# number for its own retry/degrade posture; happens to agree today.
GEMINI_TIMEOUT_SECONDS = 15.0
# How long this window waits for a keypress before closing itself, same
# reasoning and default as render_report.py's WINDOW_TIMEOUT_SECONDS --
# this window opened itself, unprompted, off the back of a voice command,
# so it must not wait forever for a keypress that may never come.
WINDOW_TIMEOUT_SECONDS = 90.0

# scripts/<os>/open_briefing_tabs.{ps1,sh} -- resolved via routing/host.py's
# script(), same OS-aware resolution llm_fallback/claude_code/fallback.py's
# RESEARCH_TABS_SCRIPT uses. A dedicated script, not open_research_tabs:
# that one always opens a Brave Search results tab first, which isn't
# wanted here -- this is meant to open exactly the narrated stories' own
# sites, nothing extra.
BRIEFING_TABS_SCRIPT = host.script("open_briefing_tabs")


def wait_for_close(timeout: float = WINDOW_TIMEOUT_SECONDS) -> None:
    """Identical to render_report.py's wait_for_close() -- see its
    docstring for why this branches on sys.platform (msvcrt vs. select())
    rather than picking one: this window is launched by both
    scripts/windows/lib/system_metrics.ps1's Open-BriefingWindow and
    scripts/linux/lib/system_metrics.sh's open_briefing_window.
    """
    print(f"\nPress Enter to close (auto-closes in {int(timeout)}s)...")
    deadline = time.monotonic() + timeout

    if sys.platform == "win32":
        import msvcrt

        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    return
            time.sleep(0.05)
    else:
        import select

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            ready, _, _ = select.select([sys.stdin], [], [], min(remaining, 0.5))
            if ready:
                sys.stdin.readline()
                return


def load_system_report(path: Path) -> dict:
    """Reads the JSON Open-BriefingWindow wrote (same shape
    Open-ReportWindow writes for render_report.py: summary + metrics).
    utf-8-sig for the same reason render_report.py uses it -- Windows
    PowerShell 5.1's Set-Content -Encoding utf8 always writes a BOM.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 -- this window must show something, not crash
        return {"summary": None, "metrics": [], "_error": str(exc)}


def render_system_section(data: dict, console: Console) -> None:
    metrics = data.get("metrics", [])
    table = Table(title="EKKO — Daily Briefing: System", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    for metric in metrics:
        style = "bold red" if metric.get("flagged") else "green"
        table.add_row(metric.get("name", "?"), f"[{style}]{metric.get('detail', '?')}[/{style}]")
    console.print(table)
    if data.get("_error"):
        console.print(f"[red]System section: failed to read report ({data['_error']})[/red]")
    ekko_ui.push_report("metrics", "EKKO — Daily Briefing: System", data)


def load_watchlist_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Reads config/daily_briefing.yaml. A missing or malformed config
    degrades to an empty watchlist/topic list rather than crashing --
    daily_briefing.ps1 still spoke the system status by the time this
    runs, so there's nothing to gain from failing loudly here over just
    showing an empty News/Stocks section.
    """
    defaults = {
        "tickers": [],
        "news_topics": [],
        "max_news_items": 10,
        "audible_news_count": 2,
        "extra_tabs": [],
        "ticker_names": {},
    }
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 -- see docstring
        return defaults
    defaults.update({k: v for k, v in loaded.items() if v is not None})
    return defaults


def display_name(ticker: str, names: dict | None = None) -> str:
    """Friendly name for `ticker` from config/daily_briefing.yaml's
    `ticker_names` map, or the raw symbol when it isn't mapped. Keeps a
    watchlist entry like "GC=F" reading as "gold" both aloud (TTS would
    otherwise say "G C equals F") and in the Stocks table, without every
    ticker needing an entry. `names` being None/empty is the normal
    no-map case, not an error.
    """
    return (names or {}).get(ticker) or ticker


def fetch_news_for_topic(topic: str, timeout: float = HN_TIMEOUT_SECONDS) -> list[dict]:
    """One HN Algolia query for `topic`, restricted to stories posted in
    the last 24h (search_by_date + created_at_i filter, not the default
    search endpoint's all-time relevance ranking -- that returned the
    same handful of high-point AI/Anthropic stories run after run,
    regardless of what was actually new that day). Points-based ranking
    still happens afterward, in fetch_all_news()'s merge/sort.
    Returns [] on any failure (timeout, non-200, bad JSON) -- one topic
    failing must never take the others down with it, see the module
    docstring.
    """
    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": topic,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(time.time()) - 86400}",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except Exception:  # noqa: BLE001 -- degrade this topic only
        return []

    items = []
    for hit in hits:
        title = hit.get("title")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title:
            continue
        items.append(
            {
                "id": hit.get("objectID"),
                "title": title,
                "url": url,
                "points": hit.get("points") or 0,
                "topic": topic,
            }
        )
    return items


def fetch_all_news(topics: list[str], max_items: int) -> list[dict]:
    """Runs one thread per topic, merges, de-duplicates by HN object ID
    (the same story often matches more than one topic query), sorts by
    points descending, caps at max_items. Doesn't need to return exactly
    max_items -- a quiet day with fewer real results is a real answer,
    not a shortfall to pad out, see config/daily_briefing.yaml's comment.
    """
    if not topics:
        return []
    merged: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        futures = [pool.submit(fetch_news_for_topic, topic) for topic in topics]
        for future in as_completed(futures):
            for item in future.result():
                # First topic to find a story wins its "topic" label; which
                # one doesn't matter, this is just for a quick per-item
                # provenance note, not used for ranking.
                merged.setdefault(item["id"], item)
    ranked = sorted(merged.values(), key=lambda item: item["points"], reverse=True)
    return ranked[:max_items]


def render_news_section(news: list[dict], console: Console) -> None:
    if not news:
        console.print(Panel("No news fetched (offline, or nothing matched today's topics).", title="Tech News", border_style="yellow"))
        ekko_ui.push_report("news", "EKKO — Daily Briefing: Tech News", {"items": []})
        return
    table = Table(title="EKKO — Daily Briefing: Tech News", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Points", justify="right", width=6)
    table.add_column("Story")
    table.add_column("Link", overflow="fold")
    for idx, item in enumerate(news, start=1):
        table.add_row(str(idx), str(item["points"]), item["title"], item["url"])
    console.print(table)
    console.print(f"[dim]More reading: {TECHMEME_URL}  |  {HN_SEARCH_UI}[/dim]")
    ekko_ui.push_report("news", "EKKO — Daily Briefing: Tech News", {"items": news})


def fetch_news_narration(
    news: list[dict], count: int, timeout: float = GEMINI_TIMEOUT_SECONDS
) -> dict[str, str]:
    """One combined Gemini call (not one per story, same latency/cost
    reasoning as fetch_commentary() below and
    llm_fallback/README.md's original merged-call reasoning for the
    intent-routing fallback) that turns the top `count` stories' bare
    titles into a fuller, spoken-style sentence each -- what got read
    aloud used to just be the raw title concatenated with the next one,
    which undersold stories whose title alone doesn't read naturally out
    loud.

    Deliberately called while fetch_all_stocks()'s future is still
    running in main()'s ThreadPoolExecutor: stock quotes take
    meaningfully longer than a handful of HN Algolia queries (yfinance
    per ticker vs. one fast JSON API), so there's real idle time between
    "news is ready" and "stocks are ready" worth spending on this instead
    of a flat title readout, without adding to the briefing's total
    wall-clock time.

    HN Algolia gives a title, a point count, and a URL -- no article
    text -- so the prompt is explicit about not inventing specifics
    (numbers, names, claims) beyond what the title itself states or
    strongly implies; this is a natural-language gloss on a headline, not
    a real summary of the story's contents, and must not be spoken as if
    it were one.

    Returns {} on any failure (missing API key, timeout, bad JSON) --
    build_narration_text() below falls back to the bare titles when a
    story has no entry here, same degrade-not-crash posture as
    fetch_commentary().
    """
    top = news[:count]
    if not top:
        return {}

    payload = [{"id": item["id"], "title": item["title"]} for item in top]
    prompt = (
        "For each Hacker News story title below, write ONE short, natural, "
        "spoken-style sentence conveying what the story is about -- based "
        "ONLY on its title, no invented specifics (no numbers, names, or "
        "claims the title doesn't already state or strongly imply). If the "
        "title is already self-explanatory, a light rephrasing that reads "
        "naturally aloud is enough; don't pad it with filler. Respond with "
        "ONLY a JSON array, no prose, no markdown fence: "
        '[{"id": "<id>", "summary": "<one sentence>"}, ...]\n\n'
        f"{json.dumps(payload)}"
    )
    try:
        envelope = generate_content(prompt, timeout=timeout)
        result_text = extract_text(envelope)
        if not result_text:
            block_reason = envelope.get("promptFeedback", {}).get("blockReason")
            print(f"[daily_briefing] news narration: no text in response (blockReason={block_reason!r})")
            return {}
        parsed = json.loads(result_text)
        if not isinstance(parsed, list):
            return {}
        return {
            item["id"]: item["summary"]
            for item in parsed
            if isinstance(item, dict) and item.get("id") and item.get("summary")
        }
    except GeminiError as exc:
        # Message already names what happened (missing key, timeout,
        # non-200, bad JSON) -- see client.py's GeminiError docstring.
        print(f"[daily_briefing] news narration: {exc}")
        return {}
    except Exception as exc:  # noqa: BLE001 -- degrade to bare titles, never crash the window
        print(f"[daily_briefing] news narration failed: {exc}")
        return {}


def build_narration_text(news: list[dict], narration: dict[str, str], count: int) -> str | None:
    """Combines the top `count` stories into one spoken paragraph, using
    fetch_news_narration()'s gloss for each story when there is one and
    falling back to its bare title otherwise -- a story missing from
    `narration` (the LLM call failed entirely, or skipped just that one)
    still gets read, just less richly, rather than silently dropped.
    Returns None when there's nothing to say (no news, count <= 0).
    """
    top = news[:count]
    if not top:
        return None
    lines = [narration.get(item["id"]) or item["title"] for item in top]
    if len(lines) == 1:
        return f"Top story: {lines[0]}"
    return "Here's what's happening: " + " Also, ".join(lines)


# feedback/speech.py loads the Piper voice model fresh on every
# subprocess call (a few seconds of ONNX init -- see its module docstring:
# load-once-at-startup doesn't apply to a standalone --text call) and then
# plays the audio back in real time. A flat 30s ceiling was fine for the
# old one-or-two-story news line but cuts off the multi-ticker stock
# narration mid-sentence (two tickers with commentary is easily 60+
# spoken words, ~25-30s of playback alone before load/synthesis
# overhead). Scale the ceiling to the text: ~2.6 words/sec of speech plus
# a fixed slab for model load + synthesis + device warm-up, floored so a
# short line still gets a sane minimum.
_TTS_WORDS_PER_SECOND = 2.6
_TTS_FIXED_OVERHEAD_SECONDS = 25.0
_TTS_MIN_TIMEOUT_SECONDS = 45.0


def _tts_timeout(text: str) -> float:
    return max(
        _TTS_MIN_TIMEOUT_SECONDS,
        len(text.split()) / _TTS_WORDS_PER_SECOND + _TTS_FIXED_OVERHEAD_SECONDS,
    )


def speak_text(text: str | None, label: str = "narration") -> None:
    """Second (and third, see build_stock_narration_text() below) spoken
    utterance (the first already happened in daily_briefing.ps1's own
    SPEAK: line for system status) -- speaks `text` via a subprocess call
    to feedback/speech.py --text using this same interpreter
    (sys.executable, pvenv already has Piper/sounddevice, see the module
    docstring). A subprocess rather than importing feedback.speech
    directly: that module loads a Piper voice model at import-adjacent
    cost (load_voice()) meant to happen once at listener startup, not per
    briefing, and speech.py's own CLI (--text) already exists exactly for
    a standalone call like this one.

    Generalised from the old news-only speak_top_news(): both the news
    narration and the stock commentary below need the identical call, and
    a shared `label` (only used in the printed log line, e.g. "news",
    "stocks") is cheaper than two near-identical copies of this function
    drifting apart.

    Fire-and-forget in spirit but actually blocking here: this whole
    script is already running off the voice pipeline's critical path (in
    the detached wt.exe tab), so there's no caller waiting on this call
    the way routing/execute.py waits on daily_briefing.ps1 -- blocking
    here just means the window's later sections render a beat after the
    speech finishes, not before. Never raises: a TTS failure degrades to
    a printed note, same posture as every other section here.
    """
    if not text:
        return

    if not _SPEECH_SCRIPT.is_file():
        print(f"[daily_briefing] {label} TTS skipped ({_SPEECH_SCRIPT.name} not found)")
        return
    try:
        subprocess.run(
            [sys.executable, str(_SPEECH_SCRIPT), "--text", text],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=_tts_timeout(text),
        )
    except Exception as exc:  # noqa: BLE001 -- never let a TTS hiccup break the window
        print(f"[daily_briefing] {label} TTS failed: {exc}")


def open_top_news_tabs(news: list[dict], count: int, extra_urls: list[str] | None = None) -> None:
    """Opens the top `count` narrated stories' own URLs in Brave, plus any
    fixed `extra_urls` from config/daily_briefing.yaml's `extra_tabs`
    (pages wanted up every briefing, not fetched), via
    scripts/<os>/open_briefing_tabs -- same cross-process launch shape as
    llm_fallback/claude_code/fallback.py's own open_research_tabs(), a
    dedicated script rather than a shared function since the scripts/<os>/
    tree owns every Brave launch in this project (see scripts/README.md's
    "no shared state between scripts" convention). Passing news + extra
    URLs in one call keeps them in the same Brave window;
    open_briefing_tabs re-validates each and drops any that isn't
    absolute http/https.

    Fire-and-forget for real this time (Popen, not run()): unlike
    speak_text() above, there's no reason for anything here to wait
    on Brave actually opening, and no reaper thread either -- a failure
    to launch only costs a missing browser tab, not a missing spoken
    response, so it's logged and left alone rather than watched.
    """
    urls_list = [item["url"] for item in news[:count]] + list(extra_urls or [])
    if not urls_list:
        return
    if not BRIEFING_TABS_SCRIPT.is_file():
        print(f"[daily_briefing] news tabs skipped ({BRIEFING_TABS_SCRIPT.name} not found)")
        return
    urls = "|".join(urls_list)
    try:
        subprocess.Popen(
            host.command(BRIEFING_TABS_SCRIPT, {"urls": urls}),
            cwd=_REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001 -- a missing tab is not worth crashing the window over
        print(f"[daily_briefing] failed to open news tabs: {exc}")


def signal_narration_done(summary_text: str | None = None) -> None:
    """Touches NARRATION_FLAG_PATH so listener/vad_listener.py's own
    pending_briefing_followup_until check picks it up, shows
    `summary_text` in its response panel (so the voice UI has something to
    display for what was just said, instead of going idle and hiding
    itself the moment the very first quick confirmation finished, well
    before this window has said anything substantial), and re-arms.

    Called unconditionally exactly ONCE, at the very end of main() --
    after BOTH news narration and stock commentary have been spoken, not
    right after news alone (the bug this fixes: vad_listener.py used to
    treat the briefing as "done" the instant news finished, while stock
    commentary was still about to be fetched, summarized, and spoken
    entirely invisibly to it). Called even when there was nothing to
    narrate at all (config with no topics/tickers, every fetch came back
    empty) -- vad_listener.py is waiting on this flag regardless, and
    shouldn't sit there for the full BRIEFING_FOLLOWUP_TIMEOUT_S just
    because there was nothing to say.

    The flag file's content is JSON now, not a bare timestamp: `{"ts":
    ..., "summary": ...}`. `ts` is kept for a human glancing at the file;
    vad_listener.py only reads `summary`, falling back to a generic line
    if it's missing/null (never speak/show nothing just because this
    write failed or summary_text was empty) -- see its own flag-read
    comment. Best-effort: a failure to write it only costs a generic
    (not missing) follow-up on the listener side, which already degrades
    gracefully on its own timeout, so this is logged rather than raised.
    """
    payload = {"ts": time.time(), "summary": summary_text}
    try:
        NARRATION_FLAG_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 -- never let this break the render
        print(f"[daily_briefing] failed to signal narration done: {exc}")


def fetch_stock(ticker: str, timeout: float = STOCK_TIMEOUT_SECONDS, include_news: bool = True) -> dict:
    """Price, % change, and up to 3 recent headlines for one ticker.

    fast_info over the full .info dict on purpose: .info makes a much
    heavier request (the full quote summary page) for data this doesn't
    need, fast_info is yfinance's purpose-built fast path for exactly
    price/change. Degrades to an "unavailable" row rather than raising --
    a delisted or mistyped ticker in the watchlist must not take the rest
    of the report down with it.

    `timeout` isn't wired into yfinance itself (its session handling
    doesn't take one cleanly across versions); the ThreadPoolExecutor this
    is called from bounds overall wait via future.result(timeout=...)
    instead, see fetch_all_stocks() below.

    `include_news=False` skips the `t.news` call entirely -- a second HTTP
    request per ticker that the daily briefing needs (it feeds
    fetch_commentary()) but scripts/watchlist_report.py does not: that
    handler runs on the voice pipeline's critical path inside
    routing/execute.py's handler timeout and only wants price/change, so
    it isn't worth the extra round trip or the risk of a slow news
    endpoint pushing a ticker past future.result()'s timeout and losing
    its price too. The returned dict shape is unchanged either way --
    `headlines` is just always [] when this is False.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        info = t.fast_info
        price = info.get("lastPrice")
        prev_close = info.get("previousClose")
        change_pct = None
        if price is not None and prev_close:
            change_pct = round(((price - prev_close) / prev_close) * 100, 2)

        headlines = []
        if include_news:
            try:
                for item in (t.news or [])[:3]:
                    # yfinance's news item shape has shifted across versions
                    # (content nested under "content" in some, flat in
                    # others) -- try both rather than assuming one.
                    content = item.get("content", item)
                    title = content.get("title")
                    if title:
                        headlines.append(title)
            except Exception:  # noqa: BLE001 -- price/change still stand without headlines
                pass

        return {
            "ticker": ticker,
            "price": price,
            "change_pct": change_pct,
            "headlines": headlines,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 -- degrade this ticker only
        return {"ticker": ticker, "price": None, "change_pct": None, "headlines": [], "error": str(exc)}


def fetch_all_stocks(
    tickers: list[str], timeout: float = STOCK_TIMEOUT_SECONDS, include_news: bool = True
) -> list[dict]:
    if not tickers:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=len(tickers)) as pool:
        futures = {
            pool.submit(fetch_stock, ticker, timeout, include_news): ticker for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results.append(future.result(timeout=timeout))
            except Exception as exc:  # noqa: BLE001 -- a hung fetch degrades, doesn't block the rest
                results.append({"ticker": ticker, "price": None, "change_pct": None, "headlines": [], "error": str(exc)})
    # Stable order matching the configured watchlist, not completion order,
    # so the table doesn't reshuffle between runs.
    order = {ticker: i for i, ticker in enumerate(tickers)}
    results.sort(key=lambda r: order.get(r["ticker"], len(order)))
    return results


def fetch_commentary(stocks: list[dict], timeout: float = GEMINI_TIMEOUT_SECONDS) -> dict[str, str]:
    """One combined Gemini call covering every ticker with headlines, not
    one call per ticker -- avoiding a separate round trip per ticker when
    one call can just as well cover all of them at once.

    This is a small, self-contained helper rather than an import of
    llm_fallback/gemini/fallback_gemini.py's run_gemini(): that module's
    machinery (FallbackOutcome, validate_pick(), IntentBundle
    re-validation) exists to keep an LLM from ever triggering an action,
    which doesn't apply here -- this call only ever produces
    spoken/displayed text, same "open-ended answer" category as that
    module's own `answer` field, just without needing its
    intent-validation half. Both this function and fetch_news_narration()
    above call llm_fallback/gemini/client.py's generate_content() directly,
    the shared transport layer both are built on.

    Returns {} on any failure (missing API key, timeout, bad JSON) -- the
    stocks table still renders with price/change alone, just no
    commentary column filled in, same degrade-not-crash posture as every
    other fetch in this file.
    """
    with_headlines = [s for s in stocks if s.get("headlines")]
    if not with_headlines:
        return {}

    payload = [{"ticker": s["ticker"], "headlines": s["headlines"]} for s in with_headlines]
    prompt = (
        "For each stock below, write one or two short spoken-style sentences "
        "of commentary based ONLY on its given headlines -- no invented facts, "
        "no financial advice framed as certainty, just a quick informed take "
        "the way you'd say it out loud. Respond with ONLY a JSON array, no "
        "prose, no markdown fence: "
        '[{"ticker": "<ticker>", "commentary": "<1-2 sentences>"}, ...]\n\n'
        f"{json.dumps(payload)}"
    )
    try:
        envelope = generate_content(prompt, timeout=timeout)
        result_text = extract_text(envelope)
        if not result_text:
            block_reason = envelope.get("promptFeedback", {}).get("blockReason")
            print(f"[daily_briefing] commentary: no text in response (blockReason={block_reason!r})")
            return {}
        parsed = json.loads(result_text)
        if not isinstance(parsed, list):
            return {}
        return {
            item["ticker"]: item["commentary"]
            for item in parsed
            if isinstance(item, dict) and item.get("ticker") and item.get("commentary")
        }
    except GeminiError as exc:
        print(f"[daily_briefing] commentary: {exc}")
        return {}
    except Exception as exc:  # noqa: BLE001 -- degrade to no commentary, never crash the window
        print(f"[daily_briefing] commentary failed: {exc}")
        return {}


def render_stocks_section(
    stocks: list[dict], commentary: dict[str, str], console: Console, names: dict | None = None
) -> None:
    if not stocks:
        console.print(Panel("No tickers configured (config/daily_briefing.yaml).", title="Stocks", border_style="yellow"))
        ekko_ui.push_report("stocks", "EKKO — Daily Briefing: Stocks", {"items": []})
        return
    table = Table(title="EKKO — Daily Briefing: Stocks", show_lines=True)
    table.add_column("Ticker", style="bold")
    table.add_column("Price", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Take")
    items = []
    for stock in stocks:
        # Friendly name as the label, raw symbol kept in dim parens when
        # the two differ so the table still shows what was actually
        # priced; an unmapped ticker is just itself.
        label = display_name(stock["ticker"], names)
        if label != stock["ticker"]:
            label = f"{label} [dim]({stock['ticker']})[/dim]"
        take = commentary.get(stock["ticker"], "")
        if stock.get("error") and stock.get("price") is None:
            table.add_row(label, "[red]unavailable[/red]", "", stock["error"][:80])
            items.append({"ticker": stock["ticker"], "label": label, "price": None, "change_pct": None, "error": stock["error"], "take": take})
            continue
        price = f"${stock['price']:.2f}" if stock.get("price") is not None else "?"
        change = stock.get("change_pct")
        if change is None:
            change_text = "?"
        else:
            style = "green" if change >= 0 else "red"
            change_text = f"[{style}]{change:+.2f}%[/{style}]"
        table.add_row(label, price, change_text, take)
        items.append(
            {
                "ticker": stock["ticker"],
                "label": label,
                "price": stock.get("price"),
                "change_pct": change,
                "error": None,
                "take": take,
            }
        )
    console.print(table)
    ekko_ui.push_report("stocks", "EKKO — Daily Briefing: Stocks", {"items": items})


def build_stock_narration_text(
    stocks: list[dict], commentary: dict[str, str], names: dict | None = None
) -> str | None:
    """Combines price/change and fetch_commentary()'s gloss into one
    spoken paragraph per ticker with headlines, same "one sentence per
    item, joined with 'Also'" shape build_narration_text() above uses for
    news -- kept as a separate function rather than a shared one since the
    two operate on different item shapes (news items vs. stock dicts) and
    speak different things (a headline gloss vs. a price move plus a
    take), not worth forcing into one signature for the sake of avoiding
    two short functions.

    Added specifically because stock commentary used to be fetched (see
    fetch_commentary()) and rendered to the table, but never spoken --
    real, silent, wasted work every single run. Only speaks tickers that
    have both a resolved price AND commentary; an unavailable/delisted
    ticker or one commentary genuinely failed for still shows up in the
    table (render_stocks_section() has its own "unavailable" row), it
    just isn't narrated -- reading "AMPX is unavailable" aloud isn't
    useful the way a real price move is.

    Returns None when there's nothing worth saying (no stocks configured,
    or none with both a price and commentary).
    """
    speakable = [s for s in stocks if s.get("price") is not None and commentary.get(s["ticker"])]
    if not speakable:
        return None

    lines = []
    for stock in speakable:
        change = stock.get("change_pct")
        direction = "up" if (change or 0) >= 0 else "down"
        move = f"{direction} {abs(change):.1f} percent" if change is not None else "flat"
        spoken = display_name(stock["ticker"], names)
        lines.append(f"{spoken} is {move}. {commentary[stock['ticker']]}")
    if len(lines) == 1:
        return f"On your watchlist: {lines[0]}"
    return "Here's your stock watchlist: " + " Also, ".join(lines)


def main() -> None:
    console = Console()
    if len(sys.argv) < 2:
        console.print("[red]usage: daily_briefing.py <path-to-system-json>[/red]")
        wait_for_close()
        return

    json_path = Path(sys.argv[1])
    system_data = load_system_report(json_path)
    json_path.unlink(missing_ok=True)  # throwaway temp file, same as render_report.py
    render_system_section(system_data, console)

    config = load_watchlist_config()

    # News and stocks fetch concurrently with each other too, not just
    # within themselves -- the two ThreadPoolExecutors below (one inside
    # fetch_all_news, one inside fetch_all_stocks) each parallelize their
    # own sources, but running the news fetch and the stock fetch as two
    # more top-level threads means neither has to wait for the other to
    # even start.
    with ThreadPoolExecutor(max_workers=2) as pool:
        news_future = pool.submit(fetch_all_news, config["news_topics"], config["max_news_items"])
        stocks_future = pool.submit(fetch_all_stocks, config["tickers"])

        news = news_future.result()
        render_news_section(news, console)
        # Everything here runs right after news resolves, without waiting
        # on stocks/commentary too -- "audibly summarize two and just
        # list out the rest" doesn't depend on the stock section at all.
        # It's also exactly the idle time stocks_future's slower yfinance
        # fetch leaves on the table: news comes back fast, so there's
        # real time here to build a fuller narration (one Gemini call,
        # see fetch_news_narration()) and open Brave tabs for the top
        # stories before stocks are even ready, rather than a flat title
        # readout done as fast as possible for its own sake.
        narration = fetch_news_narration(news, config["audible_news_count"])
        narration_text = build_narration_text(news, narration, config["audible_news_count"])
        speak_text(narration_text, label="news")
        # signal_narration_done() moved to the end of main(), after stock
        # commentary is also spoken -- see that function's own docstring
        # for the bug this fixes: calling it here, right after news alone,
        # told vad_listener.py the whole briefing was done while stock
        # commentary was still about to be fetched, summarized, and spoken
        # with zero visibility into that from the listener side.
        open_top_news_tabs(news, config["audible_news_count"], config["extra_tabs"])

        stocks = stocks_future.result()

    commentary = fetch_commentary(stocks)
    render_stocks_section(stocks, commentary, console, config["ticker_names"])
    stock_narration_text = build_stock_narration_text(stocks, commentary, config["ticker_names"])
    speak_text(stock_narration_text, label="stocks")

    # Exactly one flag write, now that both sections have actually been
    # spoken -- see signal_narration_done()'s docstring. summary_text is
    # what vad_listener.py's response panel shows; joining both texts
    # (skipping whichever section had nothing to say) gives it the real
    # content instead of a placeholder, same "show what was actually
    # said" pattern llm_fallback's open-ended answers already use there.
    summary_text = " ".join(t for t in (narration_text, stock_narration_text) if t) or None
    signal_narration_done(summary_text)

    wait_for_close()


if __name__ == "__main__":
    main()
