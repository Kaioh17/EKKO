"""EKKO's daily briefing. One builder, three front ends:

    voice    main(): launched hidden by scripts/lib/system_metrics.ps1's
             Open-BriefingWindow with the system-health JSON. Pushes each
             section to ekko-ui as it resolves, speaks it, opens Brave tabs.
    chat     build_chat_briefing(): the same sections as one markdown message.
    preview  preview(): what the UI's Preview button shows (sections + tokens).

Everything editable lives in the "briefing" settings section
(backend/schemas/settings.py BriefingSettings, edited in the UI's Briefing
panel): which sections run and in what order, news topics, tickers, tabs,
the narration mode, the prompts, the spoken phrases. Nothing is hardcoded
here except fetch mechanics.

Adding a briefing feature (calendar, weather, ...): add its id to
BRIEFING_SECTION_IDS, its settings fields to BriefingSettings, one
`_<id>_section(cfg, ...)` below returning a section dict, and a line in
_SECTION_TITLES / build(). Order, on/off, delivery and the UI list follow.

Narration mode: "plain" uses no LLM and says nothing: the briefing is on
screen only. "llm" voice (main()) compiles everything first (news with
article text and HN comments, health, watchlist values plus a silent "why
it moved" per ticker), then makes ONE call under the briefing's own system
prompt (narrate_briefing) and speaks the result as one block. The
watchlist is spoken as values only; afterwards EKKO asks whether to explain
it, and the reasons ride in the day log so a "yes" is answered from them.
Chat/preview (build()) keep one short call per section. Calls go through the
app's provider chain (llm_fallback/brain/summarize, so failover order, models
and spend caps apply) and report their tokens. An LLM failure degrades to the
plain wording, never to silence. `legacy_window` runs the old per-section
voice flow (_main_legacy) instead.

Runs under pvenv (yfinance and Piper live there). Every fetch/LLM/TTS step
degrades one section to a note instead of crashing, same posture as the
metrics functions in scripts/lib/system_metrics.ps1.

Usage:
    python scripts/daily_briefing.py %TEMP%\\ekko_briefing_....json
"""

import html
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import ekko_ui
import requests
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Resolve repo packages regardless of cwd (this is launched as
# `python scripts\daily_briefing.py <json>`, which puts scripts/ on sys.path).
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.schemas.chat import tokens_from_usage  # noqa: E402
from backend.schemas.settings import NARRATION_WORDS  # noqa: E402
from llm_fallback.brain.summarize import ask_json  # noqa: E402
from routing import host  # noqa: E402

_SPEECH_SCRIPT = _REPO_ROOT / "feedback" / "speech.py"
BRIEFING_TABS_SCRIPT = host.script("open_briefing_tabs")

# Cross-process signal to listener/vad_listener.py (its BRIEFING_NARRATION_FLAG_PATH
# must stay identical): written once the briefing is done being spoken so the
# listener re-arms its "anything else?" follow-up instead of racing the narration.
NARRATION_FLAG_PATH = Path(tempfile.gettempdir()) / "ekko_briefing_narration_done.flag"

# feedback/speech.py loads the Piper model fresh per call, so scale the TTS
# ceiling to the text: ~2.6 words/s of playback plus a slab for load/synthesis.
_TTS_WORDS_PER_SECOND = 2.6
_TTS_FIXED_OVERHEAD_SECONDS = 25.0
_TTS_MIN_TIMEOUT_SECONDS = 45.0

_SECTION_TITLES = {"system": "System", "news": "Tech News", "stocks": "Stocks"}
_SECTION_KINDS = {"system": "metrics", "news": "news", "stocks": "stocks"}


# ---------------------------------------------------------------- config

def _migrate_yaml() -> None:
    """One-time: a personal/daily_briefing.yaml (the old, hand-edited config)
    moves into the settings DB, then is renamed so it can't override a later
    "Restore defaults". The shipped defaults now live in BriefingSettings."""
    from backend import db
    from backend.schemas.settings import BriefingSettings

    old = host.PERSONAL_DIR / "daily_briefing.yaml"
    if not old.is_file() or db.get_section("briefing"):
        return
    try:
        loaded = yaml.safe_load(old.read_text(encoding="utf-8")) or {}
        values = {k: v for k, v in loaded.items() if k in BriefingSettings.model_fields and v is not None}
        BriefingSettings(**values)  # validate before writing anything
        db.put_section("briefing", values)
        old.replace(old.with_suffix(".yaml.migrated"))
        print("[daily_briefing] moved personal/daily_briefing.yaml into the Briefing settings")
    except Exception as exc:  # noqa: BLE001 -- leave the file alone, defaults apply
        print(f"[daily_briefing] could not migrate {old.name}: {exc}")


def load_config():
    """The current BriefingSettings (DB first, code defaults for the rest)."""
    from backend.config import get

    _migrate_yaml()
    return get("briefing")


def display_name(ticker: str, names: dict | None = None) -> str:
    """Friendly name for `ticker` ("GC=F" -> "gold", else the raw symbol), so
    TTS never says "G C equals F"."""
    return (names or {}).get(ticker) or ticker


# ---------------------------------------------------------------- fetching

def _plain(html_text: str) -> str:
    """HN comment/story bodies are HTML fragments; the narrator wants words."""
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(html_text or "")).split())


def fetch_news_for_topic(topic: str, timeout: float = 8.0, window_hours: int = 24) -> list[dict]:
    """One HN Algolia query for `topic`, newest stories within the window
    (search_by_date, not relevance: relevance returned the same all-time
    high-point stories every run). [] on any failure: one topic failing
    must not take the others down."""
    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": topic,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(time.time()) - window_hours * 3600}",
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
        if not title:
            continue
        items.append(
            {
                "id": hit.get("objectID"),
                "title": title,
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                "points": hit.get("points") or 0,
                "topic": topic,
                "text": _plain(hit.get("story_text") or ""),  # Ask/Show HN posts carry their own body
            }
        )
    return items


def fetch_all_news(topics: list[str], max_items: int, timeout: float = 8.0, window_hours: int = 24) -> list[dict]:
    """One thread per topic, merged, de-duplicated by HN id (a story often
    matches several topics), sorted by points, capped. Fewer than max_items
    on a quiet day is a real answer, not a shortfall to pad."""
    if not topics:
        return []
    merged: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(topics)) as pool:
        futures = [pool.submit(fetch_news_for_topic, t, timeout, window_hours) for t in topics]
        for future in as_completed(futures):
            for item in future.result():
                merged.setdefault(item["id"], item)
    return sorted(merged.values(), key=lambda item: item["points"], reverse=True)[:max_items]


def fetch_stock(ticker: str, timeout: float = 10.0, include_news: bool = True, headlines: int = 3) -> dict:
    """Price, % change and up to `headlines` recent headlines for one ticker.
    fast_info, not .info (a much heavier request). Degrades to an error row:
    a delisted ticker must not take the report down. `include_news=False`
    skips the extra news request (watchlist_report.py runs inside the voice
    handler timeout and only wants price/change)."""
    try:
        import yfinance as yf

        t = yf.Ticker(ticker)
        info = t.fast_info
        price = info.get("lastPrice")
        prev_close = info.get("previousClose")
        change_pct = None
        if price is not None and prev_close:
            change_pct = round(((price - prev_close) / prev_close) * 100, 2)

        found, news = [], []
        if include_news and headlines > 0:
            try:
                for item in (t.news or [])[:headlines]:
                    # yfinance's news shape shifted across versions (nested
                    # under "content" or flat): try both.
                    content = item.get("content", item)
                    title = content.get("title")
                    if title:
                        found.append(title)
                        news.append({"title": title, "summary": _plain(content.get("summary") or content.get("description") or "")[:400]})
            except Exception:  # noqa: BLE001, S110 -- price/change still stand without headlines
                pass
        return {"ticker": ticker, "price": price, "change_pct": change_pct, "headlines": found, "news": news, "error": None}
    except Exception as exc:  # noqa: BLE001 -- degrade this ticker only
        return {"ticker": ticker, "price": None, "change_pct": None, "headlines": [], "news": [], "error": str(exc)}


def fetch_all_stocks(
    tickers: list[str], timeout: float = 10.0, include_news: bool = True, headlines: int = 3
) -> list[dict]:
    if not tickers:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=len(tickers)) as pool:
        futures = {pool.submit(fetch_stock, t, timeout, include_news, headlines): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                results.append(future.result(timeout=timeout))
            except Exception as exc:  # noqa: BLE001 -- a hung fetch degrades, doesn't block the rest
                results.append({"ticker": ticker, "price": None, "change_pct": None, "headlines": [], "error": str(exc)})
    order = {t: i for i, t in enumerate(tickers)}  # configured order, not completion order
    results.sort(key=lambda r: order.get(r["ticker"], len(order)))
    return results


# Deeper news, all free: the story's own page (via the research pipeline's
# fetcher/extractor, absent in public checkouts) plus the top HN comments.
# Either half missing just means less to narrate, never a dropped story.
_ARTICLE_CHARS = 1800
_COMMENTS_PER_STORY = 3
_COMMENT_CHARS = 300


def _article_text(url: str, timeout: float) -> str:
    try:
        from control_center.hands_on.research.extract import extract
        from control_center.hands_on.research.fetch import Fetcher

        with Fetcher(timeout=timeout) as fetcher:
            final_url, body = fetcher.get(url)
        page = extract(body, final_url, max_chars=_ARTICLE_CHARS)
        return page.text or page.description
    except Exception:  # noqa: BLE001 -- ImportError (no pipeline), blocked, paywalled, slow: all "no article"
        return ""


def _hn_comments(story_id: str, timeout: float) -> list[str]:
    try:
        resp = requests.get(f"https://hn.algolia.com/api/v1/items/{story_id}", timeout=timeout)
        resp.raise_for_status()
        comments = [_plain(c.get("text") or "")[:_COMMENT_CHARS] for c in resp.json().get("children", [])]
        return [c for c in comments if c][:_COMMENTS_PER_STORY]
    except Exception:  # noqa: BLE001
        return []


def enrich_news(items: list[dict], timeout: float = 8.0) -> None:
    """Adds "article" and "comments" to each item, in place, one thread per story."""
    if not items:
        return

    def one(item: dict) -> None:
        item["article"] = item.get("text") or _article_text(item["url"], timeout)
        item["comments"] = _hn_comments(item["id"], timeout)

    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        list(pool.map(one, items))


def add_search_news(cfg, stocks: list[dict]) -> None:
    """reasons_use_search: a web search (credits!) for each priced ticker Yahoo
    had no news for, so "why did it move" still has something to read."""
    if not cfg.reasons_use_search:
        return
    try:
        import web_scraping

        if not web_scraping.available():
            return
        for s in stocks:
            if s.get("news") or s.get("price") is None:
                continue
            up = (s.get("change_pct") or 0) >= 0
            report = web_scraping.search(f"why is {display_name(s['ticker'], cfg.ticker_names)} {'up' if up else 'down'} today", count=3, freshness="pd")
            s["news"] = [{"title": h.title, "summary": (h.snippet or "")[:400]} for h in report.hits[:3]]
            s["headlines"] = [h.title for h in report.hits[:3]]
    except Exception as exc:  # noqa: BLE001 -- reasons degrade to "No clear reason found."
        print(f"[daily_briefing] reason search: {exc}")


# ---------------------------------------------------------------- narration

class _Usage:
    """Tokens spent on the briefing's LLM calls. Provider-reported when the
    provider reports it, else a rough chars/4 estimate (`approx`)."""

    def __init__(self) -> None:
        self.calls = 0
        self.input = 0
        self.output = 0
        self.approx = False
        self.models: list[str] = []

    def add(self, meta: dict, prompt_chars: int, parsed: dict) -> None:
        tokens = tokens_from_usage(meta.get("usage") or {})
        if tokens and (tokens.input is not None or tokens.output is not None):
            self.input += tokens.input or 0
            self.output += tokens.output or 0
        else:
            self.input += prompt_chars // 4
            self.output += len(json.dumps(parsed)) // 4
            self.approx = True
        self.calls += 1
        label = f"{meta.get('provider')}/{meta.get('model')}"
        if label not in self.models:
            self.models.append(label)

    def as_dict(self) -> dict | None:
        if not self.calls:
            return None
        return {"calls": self.calls, "input": self.input, "output": self.output, "approx": self.approx, "models": self.models}


def _narrate(cfg, instruction: str, payload: list[dict], id_key: str, text_key: str, label: str, usage: _Usage) -> dict[str, str]:
    """One LLM call turning `payload` (a list of items keyed by `id_key`) into
    {id: sentence}. The instruction is the user's editable prompt; the JSON
    reply contract is appended here so editing the prompt can't break parsing.
    {} on any failure: callers fall back to the plain wording."""
    system = (
        f"{instruction}\n\nRespond with ONLY a JSON object, no prose, no markdown fence: "
        f'{{"items": [{{"{id_key}": "<{id_key}>", "{text_key}": "<text>"}}, ...]}}'
    )
    prompt = json.dumps(payload)
    try:
        parsed, meta = ask_json(prompt, system, label, "items", start=cfg.llm_provider, timeout=cfg.llm_timeout_s)
    except Exception as exc:  # noqa: BLE001 -- degrade to plain wording, never crash the briefing
        print(f"[daily_briefing] {label}: {exc}")
        return {}
    usage.add(meta, len(system) + len(prompt), parsed)
    items = parsed.get("items")
    if not isinstance(items, list):
        return {}
    return {str(i[id_key]): i[text_key] for i in items if isinstance(i, dict) and i.get(id_key) and i.get(text_key)}


def news_spoken(cfg, news: list[dict], narration: dict[str, str], count: int) -> str | None:
    """The top `count` stories as one paragraph: the narration sentence when
    there is one, else the bare title (never silently dropped)."""
    lines = [narration.get(item["id"]) or item["title"] for item in news[:count]]
    if not lines:
        return None
    if len(lines) == 1:
        return f"{cfg.speak_news_single} {lines[0]}"
    return f"{cfg.speak_news_intro} " + f" {cfg.speak_joiner} ".join(lines)


def stocks_spoken(cfg, stocks: list[dict], commentary: dict[str, str]) -> str | None:
    """Price move (+ commentary when the LLM gave one) per priced ticker.
    An unavailable ticker still shows in the table but isn't read out."""
    lines = []
    for s in stocks:
        if s.get("price") is None:
            continue
        change = s.get("change_pct")
        move = f"{'up' if change >= 0 else 'down'} {abs(change):.1f} percent" if change is not None else f"at {s['price']:.2f}"
        take = commentary.get(s["ticker"])
        lines.append(f"{display_name(s['ticker'], cfg.ticker_names)} is {move}." + (f" {take}" if take else ""))
    if not lines:
        return None
    if len(lines) == 1:
        return f"{cfg.speak_stocks_single} {lines[0]}"
    return f"{cfg.speak_stocks_intro} " + f" {cfg.speak_joiner} ".join(lines)


# ---------------------------------------------------------------- sections
# Each returns {"id", "kind", "title", "payload", "spoken"}; payload is what
# ekko-ui renders (BriefingPanel.tsx / Sandbox.tsx), spoken what TTS says.

def _section(sid: str, payload: dict, spoken: str | None) -> dict:
    return {
        "id": sid,
        "kind": _SECTION_KINDS[sid],
        "title": f"EKKO — Daily Briefing: {_SECTION_TITLES[sid]}",
        "payload": payload,
        "spoken": spoken,
    }


def _news_section(cfg, news: list[dict], narrate: bool, usage: _Usage, deep: bool = False) -> dict:
    count = cfg.audible_news_count
    if deep:
        enrich_news(news[:count], cfg.news_timeout_s)
    narration = {}
    if narrate and news and count > 0:
        top = [{"id": n["id"], "title": n["title"]} for n in news[:count]]
        narration = _narrate(cfg, cfg.news_prompt, top, "id", "summary", "[briefing news]", usage)
    payload = {"items": news, "read": count, "narration": narration}
    return _section("news", payload, news_spoken(cfg, news, narration, count))


def _stocks_section(cfg, stocks: list[dict], llm: bool, usage: _Usage) -> dict:
    """Spoken: the values only. The LLM's per-ticker "why it moved" goes on
    screen (`take`) and into the follow-up, never into the spoken line."""
    commentary = {}
    with_news = [s for s in stocks if s.get("price") is not None and (s.get("news") or s.get("headlines"))]
    if llm and with_news:
        payload = [
            {
                "ticker": s["ticker"],
                "move": f"{'up' if (s.get('change_pct') or 0) >= 0 else 'down'} {abs(s.get('change_pct') or 0):.1f} percent",
                "news": s.get("news") or [{"title": h} for h in s["headlines"]],
            }
            for s in with_news
        ]
        commentary = _narrate(cfg, cfg.stocks_prompt, payload, "ticker", "commentary", "[briefing stocks]", usage)
    items = []
    for s in stocks:
        label = display_name(s["ticker"], cfg.ticker_names)
        if label != s["ticker"]:
            label = f"{label} ({s['ticker']})"
        unavailable = s.get("error") and s.get("price") is None
        items.append(
            {
                "ticker": s["ticker"],
                "label": label,
                "price": None if unavailable else s.get("price"),
                "change_pct": None if unavailable else s.get("change_pct"),
                "error": s["error"] if unavailable else None,
                "take": commentary.get(s["ticker"]) or (NO_REASON if llm and not unavailable else ""),
            }
        )
    return _section("stocks", {"items": items}, stocks_spoken(cfg, stocks, {}))


NO_REASON = "No clear reason found."


def build(
    cfg, system_data: dict | None = None, on_section=None, narrate_news: bool = True, deep_news: bool = False,
    usage: _Usage | None = None,
) -> dict:
    """Runs every enabled section in the configured order. News and stocks
    are fetched concurrently up front, so a slow section never delays the
    start of the others' network work; `on_section(section)` fires as each
    one completes (voice pushes/speaks from it). `system_data` is the
    handler's health JSON; without it (chat, preview) the system section is
    skipped. `narrate_news=False` skips the news LLM call (nothing to say it
    to). `deep_news` adds article text and HN comments to the spoken stories
    (the voice narrator's input). `usage`: a tally the caller keeps adding to
    after build() returns (the voice narration's own call)."""
    started = time.perf_counter()
    usage = usage or _Usage()
    llm = cfg.mode == "llm"
    order = [s.id for s in cfg.sections if s.enabled and (s.id != "system" or system_data is not None)]
    sections = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        news_f = pool.submit(fetch_all_news, cfg.news_topics, cfg.max_news_items, cfg.news_timeout_s, cfg.news_window_hours) if "news" in order else None
        stocks_f = (
            pool.submit(fetch_all_stocks, cfg.tickers, cfg.stock_timeout_s, llm and cfg.stock_headlines > 0, cfg.stock_headlines)
            if "stocks" in order
            else None
        )
        for sid in order:
            if sid == "system":
                section = _section("system", system_data, None)
            elif sid == "news":
                section = _news_section(cfg, news_f.result(), llm and narrate_news, usage, deep_news)
            else:
                stocks = stocks_f.result()
                if llm:
                    add_search_news(cfg, stocks)
                section = _stocks_section(cfg, stocks, llm, usage)
            sections.append(section)
            if on_section:
                on_section(section)
    return {"mode": cfg.mode, "sections": sections, "usage": usage.as_dict(), "seconds": round(time.perf_counter() - started, 2)}


def usage_note(usage: dict | None) -> str:
    if not usage:
        return "Plain: no AI used."
    total = usage["input"] + usage["output"]
    return f"~{total:,} tokens ({usage['input']:,} in, {usage['output']:,} out) via {', '.join(usage['models'])}"


# ---------------------------------------------------------------- front ends

def preview(mode: str | None = None) -> dict:
    """What the UI's Preview button shows: no TTS, no tabs, no system section."""
    cfg = load_config()
    if mode:
        cfg = cfg.model_copy(update={"mode": mode})
    return build(cfg)


def render_markdown(result: dict, cfg) -> str:
    """The briefing as one markdown message for the chat panel."""
    parts = ["## Your briefing"]
    for section in result["sections"]:
        if section["id"] == "news" and section["payload"]["items"]:
            narration = section["payload"]["narration"]
            lines = []
            for n in section["payload"]["items"][: cfg.chat_news_items]:
                gloss = f" — {narration[n['id']]}" if n["id"] in narration else ""
                lines.append(f"- [{n['title']}]({n['url']}){gloss}")
            parts.append("### Tech news\n" + "\n".join(lines))
        elif section["id"] == "stocks":
            rows = []
            for s in section["payload"]["items"]:
                if s["price"] is None:
                    continue
                move = "n/a" if s["change_pct"] is None else f"{s['change_pct']:+.2f}%"
                rows.append(f"| {s['label']} | {s['price']:,.2f} | {move} | {s['take']} |")
            if rows:
                parts.append("### Watchlist\n| Ticker | Price | Change | Take |\n|---|---|---|---|\n" + "\n".join(rows))
    if len(parts) == 1:
        parts.append("Nothing to report: no sections are on, nothing is configured, or the fetches failed.")
    elif result["usage"]:
        parts.append(f"_{usage_note(result['usage'])}_")
    return "\n\n".join(parts)


def build_chat_briefing() -> str:
    """Chat is text-only: no system health, TTS, tabs or flag file."""
    cfg = load_config()
    return render_markdown(build(cfg), cfg)


# ---------------------------------------------------------------- voice

def load_system_report(path: Path) -> dict:
    """The JSON Open-BriefingWindow wrote (summary + metrics). utf-8-sig:
    Windows PowerShell 5.1's Set-Content -Encoding utf8 always writes a BOM."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 -- the window must show something, not crash
        return {"summary": None, "metrics": [], "_error": str(exc)}


def _tts_timeout(text: str) -> float:
    return max(_TTS_MIN_TIMEOUT_SECONDS, len(text.split()) / _TTS_WORDS_PER_SECOND + _TTS_FIXED_OVERHEAD_SECONDS)


def speak_text(text: str | None, label: str = "narration") -> None:
    """Speaks `text` via feedback/speech.py --text under this interpreter (a
    subprocess: speech.py loads its Piper voice per call by design). Blocking
    is fine, this whole script is off the voice pipeline's critical path.
    Never raises: a TTS failure is a printed note."""
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
    except Exception as exc:  # noqa: BLE001
        print(f"[daily_briefing] {label} TTS failed: {exc}")


def open_tabs(urls: list[str]) -> None:
    """Opens `urls` as Brave tabs via scripts/<os>/open_briefing_tabs (which
    re-validates each as absolute http/https). Fire-and-forget: a failure
    only costs a missing tab."""
    if not urls:
        return
    if not BRIEFING_TABS_SCRIPT.is_file():
        print(f"[daily_briefing] tabs skipped ({BRIEFING_TABS_SCRIPT.name} not found)")
        return
    try:
        subprocess.Popen(
            host.command(BRIEFING_TABS_SCRIPT, {"urls": "|".join(urls)}),
            cwd=_REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[daily_briefing] failed to open tabs: {exc}")


def signal_narration_done(summary_text: str | None = None, speak: str | None = None) -> None:
    """Touches NARRATION_FLAG_PATH exactly once, at the very end (even with
    nothing narrated: vad_listener.py is waiting on it either way). JSON
    {"ts", "summary", "speak"}; the listener shows `summary` in its response
    panel (generic line if missing) and says `speak`, when set, instead of
    "anything else?" and opens a follow-up session on it. Best-effort."""
    try:
        NARRATION_FLAG_PATH.write_text(json.dumps({"ts": time.time(), "summary": summary_text, "speak": speak}), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(f"[daily_briefing] failed to signal narration done: {exc}")


# ---------------------------------------------------------------- voice narration

def narration_payload(cfg, result: dict) -> dict:
    """What the narrator is given: one block per enabled section, in order."""
    data: dict = {}
    for section in result["sections"]:
        payload = section["payload"]
        if section["id"] == "system":
            data["system_health"] = {
                "already_announced": payload.get("summary"),
                "flagged": [m["detail"] for m in payload.get("metrics", []) if m.get("flagged")],
            }
        elif section["id"] == "news" and payload["items"]:
            data["tech_news"] = [
                {"title": n["title"], "points": n["points"], "article": n.get("article", ""), "hacker_news_comments": n.get("comments", [])}
                for n in payload["items"][: payload["read"]]
            ]
        elif section["id"] == "stocks" and section["spoken"]:
            data["watchlist_values"] = section["spoken"]
    return data


def narrate_briefing(cfg, result: dict, usage: _Usage) -> str | None:
    """One LLM call writes the whole narration (the briefing's own system
    prompt, length from narration_length), then it is spoken as one block.
    An LLM failure speaks the sections' plain wording instead. Returns the
    text said, None if nothing was."""
    data = narration_payload(cfg, result)
    if not data:
        return None
    system = (
        f"{cfg.briefing_system_prompt}\n\nRespond with ONLY a JSON object, no prose, no markdown fence: "
        '{"narration": "<the spoken briefing>"}'
    )
    prompt = f"Length: {NARRATION_WORDS[cfg.narration_length]}.\n\n{json.dumps(data)}"
    try:
        parsed, meta = ask_json(prompt, system, "[briefing narration]", "narration", start=cfg.llm_provider, timeout=cfg.llm_timeout_s)
        usage.add(meta, len(system) + len(prompt), parsed)
        text = str(parsed["narration"]).strip()
    except Exception as exc:  # noqa: BLE001 -- degrade to the plain wording, never silence
        print(f"[daily_briefing] narration: {exc}")
        text = " ".join(s["spoken"] for s in result["sections"] if s["id"] != "system" and s["spoken"])
    speak_text(text, label="narration")
    return text or None


def watchlist_followup(cfg, result: dict) -> tuple[str | None, str | None]:
    """(question to ask, reasons text). The question only when the watchlist
    was read out (and so has reasons to offer); the reasons are saved with
    the briefing's day-log turn so a "yes" is answered from them."""
    stocks = next((s for s in result["sections"] if s["id"] == "stocks" and s["spoken"]), None)
    if stocks is None or result["mode"] != "llm":
        return None, None
    reasons = " ".join(f"{i['label']}: {i['take']}" for i in stocks["payload"]["items"] if i["price"] is not None and i["take"])
    return cfg.speak_watchlist_followup, reasons


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: daily_briefing.py <path-to-system-json>")
        return

    json_path = Path(sys.argv[1])
    system_data = load_system_report(json_path)
    json_path.unlink(missing_ok=True)  # throwaway temp file

    cfg = load_config()
    if cfg.legacy_window:
        return _main_legacy(cfg, system_data)
    # Plain mode = screen only: no LLM, no voice, whatever `deliver` says.
    speak = cfg.deliver in ("both", "voice") and cfg.mode == "llm"
    show = cfg.deliver in ("both", "sandbox") or cfg.mode == "plain"
    total = sum(1 for s in cfg.sections if s.enabled) + 1  # + the closing "run" card
    tabs_opened = False

    def on_section(section: dict) -> None:
        nonlocal tabs_opened
        if show:
            ekko_ui.push_report(section["kind"], section["title"], {**section["payload"], "total": total})
        if section["id"] == "news" and cfg.open_tabs:
            open_tabs([n["url"] for n in section["payload"]["items"][: cfg.tab_news_count]] + cfg.extra_tabs)
            tabs_opened = True

    usage = _Usage()
    result = build(cfg, system_data, on_section, narrate_news=False, deep_news=speak, usage=usage)
    if cfg.open_tabs and not tabs_opened:
        open_tabs(cfg.extra_tabs)
    narration = question = None
    if speak:
        narration = narrate_briefing(cfg, result, usage)
        result["usage"] = usage.as_dict()
        question, reasons = watchlist_followup(cfg, result)
        if question and narration:
            try:
                from memory.day_log import record_turn

                record_turn("daily briefing", f"{narration} {question} (Reasons for the watchlist, not said aloud yet: {reasons})")
            except Exception as exc:  # noqa: BLE001 -- the follow-up just loses its reasons
                print(f"[daily_briefing] day log: {exc}")
    if show:
        ekko_ui.push_report(
            "run",
            "EKKO — Daily Briefing: Run",
            {"mode": result["mode"], "usage": result["usage"], "seconds": result["seconds"], "note": usage_note(result["usage"]), "total": total},
        )
    signal_narration_done(narration, speak=question if narration else None)


def _main_legacy(cfg, system_data: dict) -> None:
    """The old detached window (cfg.legacy_window): per-section narration
    calls, each section spoken as it resolves, no follow-up question."""
    speak = cfg.deliver in ("both", "voice")
    show = cfg.deliver in ("both", "sandbox")
    enabled = sum(1 for s in cfg.sections if s.enabled)
    # The sandbox counts the cards of one briefing to know when it's done
    # writing: every enabled section plus the closing "run" card below.
    total = enabled + 1
    spoken: list[str] = []
    tabs_opened = False

    def on_section(section: dict) -> None:
        nonlocal tabs_opened
        if show:
            ekko_ui.push_report(section["kind"], section["title"], {**section["payload"], "total": total})
        if speak and section["spoken"]:
            speak_text(section["spoken"], label=section["id"])
            spoken.append(section["spoken"])
        if section["id"] == "news" and cfg.open_tabs:
            top = [n["url"] for n in section["payload"]["items"][: cfg.tab_news_count]]
            open_tabs(top + cfg.extra_tabs)
            tabs_opened = True

    result = build(cfg, system_data, on_section, narrate_news=speak)
    if cfg.open_tabs and not tabs_opened:
        open_tabs(cfg.extra_tabs)
    if show:
        ekko_ui.push_report(
            "run",
            "EKKO — Daily Briefing: Run",
            {"mode": result["mode"], "usage": result["usage"], "seconds": result["seconds"], "note": usage_note(result["usage"]), "total": total},
        )
    # vad_listener's response panel shows this: the text actually said.
    signal_narration_done(" ".join(spoken) or None)


if __name__ == "__main__":
    main()
