"""Daily briefing: settings validation, section order/on-off, plain vs LLM
narration and token accounting. Network and LLM are stubbed."""

import json
import sys

import pytest
from pydantic import ValidationError

from backend.schemas.settings import BriefingSettings
from paths import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import daily_briefing as db  # noqa: E402

NEWS = [
    {"id": "1", "title": "Big model ships", "url": "https://a.test/1", "points": 90, "topic": "AI"},
    {"id": "2", "title": "Tiny tool", "url": "https://a.test/2", "points": 10, "topic": "new tools"},
]
STOCKS = [
    {"ticker": "SPY", "price": 500.0, "change_pct": 1.234, "headlines": ["SPY rallies"], "error": None},
    {"ticker": "GC=F", "price": 2000.0, "change_pct": -0.5, "headlines": [], "error": None},
    {"ticker": "DEAD", "price": None, "change_pct": None, "headlines": [], "error": "delisted"},
]


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(db, "fetch_all_news", lambda *a, **k: NEWS)
    monkeypatch.setattr(db, "fetch_all_stocks", lambda *a, **k: STOCKS)


def test_settings_defaults_and_validation():
    cfg = BriefingSettings()
    assert [s.id for s in cfg.sections] == ["system", "news", "stocks"]
    assert BriefingSettings(tickers=[" spy", "SPY", ""]).tickers == ["SPY"]
    only_stocks = BriefingSettings(sections=[{"id": "stocks", "enabled": False}])
    assert [s.id for s in only_stocks.sections] == ["stocks", "system", "news"]  # missing ids appended
    with pytest.raises(ValidationError):
        BriefingSettings(extra_tabs=["javascript:alert(1)"])
    with pytest.raises(ValidationError):
        BriefingSettings(sections=[{"id": "news"}, {"id": "news"}])


def test_plain_mode_orders_sections_and_uses_no_llm(stub, monkeypatch):
    monkeypatch.setattr(db, "ask_json", lambda *a, **k: pytest.fail("plain mode must not call the LLM"))
    cfg = BriefingSettings(
        mode="plain",
        sections=[{"id": "stocks"}, {"id": "news"}, {"id": "system", "enabled": False}],
        ticker_names={"GC=F": "gold"},
    )
    seen = []
    result = db.build(cfg, on_section=lambda s: seen.append(s["id"]))
    assert seen == [s["id"] for s in result["sections"]] == ["stocks", "news"]
    assert result["usage"] is None
    stocks, news = result["sections"]
    assert stocks["spoken"].startswith("Here's your stock watchlist: SPY is up 1.2 percent.")
    assert "gold is down 0.5 percent." in stocks["spoken"] and "DEAD" not in stocks["spoken"]
    assert news["spoken"] == "Here's what's happening: Big model ships Also, Tiny tool"
    assert news["payload"]["read"] == 2


def test_system_section_needs_handler_data(stub):
    cfg = BriefingSettings(mode="plain")
    assert [s["id"] for s in db.build(cfg)["sections"]] == ["news", "stocks"]
    with_system = db.build(cfg, system_data={"metrics": [], "summary": "ok"})
    assert [s["id"] for s in with_system["sections"]] == ["system", "news", "stocks"]


def test_llm_mode_narrates_counts_tokens_and_falls_back(stub, monkeypatch):
    calls = []

    def fake(prompt, system, label, required, start=None, timeout=None):
        calls.append(label)
        if "news" in label:
            parsed = {"items": [{"id": "1", "summary": "A big model just shipped."}]}
        else:
            parsed = {"items": [{"ticker": "SPY", "commentary": "Steady."}]}
        return parsed, {"provider": "gemini", "model": "m", "usage": {"promptTokenCount": 100, "candidatesTokenCount": 20}}

    monkeypatch.setattr(db, "ask_json", fake)
    cfg = BriefingSettings(mode="llm", audible_news_count=1)
    result = db.build(cfg)
    assert calls == ["[briefing news]", "[briefing stocks]"]
    assert result["usage"] == {"calls": 2, "input": 200, "output": 40, "approx": False, "models": ["gemini/m"]}
    news = next(s for s in result["sections"] if s["id"] == "news")
    assert news["spoken"] == "Top story: A big model just shipped."
    assert "~240 tokens" in db.usage_note(result["usage"])

    def boom(*a, **k):
        raise RuntimeError("all providers down")

    monkeypatch.setattr(db, "ask_json", boom)
    degraded = db.build(cfg)
    assert degraded["usage"] is None
    assert next(s for s in degraded["sections"] if s["id"] == "news")["spoken"] == "Top story: Big model ships"


def test_missing_provider_usage_is_estimated(stub, monkeypatch):
    monkeypatch.setattr(db, "ask_json", lambda *a, **k: ({"items": [{"id": "1", "summary": "x"}]}, {"provider": "p", "model": "m", "usage": {}}))
    cfg = BriefingSettings(mode="llm", sections=[{"id": "news"}, {"id": "system", "enabled": False}, {"id": "stocks", "enabled": False}])
    usage = db.build(cfg)["usage"]
    assert usage["approx"] is True and usage["input"] > 0


def test_chat_markdown_is_text_only(stub):
    cfg = BriefingSettings(mode="plain", chat_news_items=1)
    text = db.render_markdown(db.build(cfg), cfg)
    assert "[Big model ships](https://a.test/1)" in text and "Tiny tool" not in text
    assert "| SPY | 500.00 | +1.23% |" in text and "DEAD" not in text
    assert "tokens" not in text


# --- voice: compiled data, streamed narration, watchlist follow-up ---

def test_stocks_spoken_is_values_only_reasons_stay_on_screen(stub, monkeypatch):
    monkeypatch.setattr(db, "ask_json", lambda *a, **k: ({"items": [{"ticker": "SPY", "commentary": "Steady."}]}, {"provider": "p", "model": "m", "usage": {}}))
    sections = {s["id"]: s for s in db.build(BriefingSettings(mode="llm"))["sections"]}
    assert "Steady." not in sections["stocks"]["spoken"]
    takes = {i["ticker"]: i["take"] for i in sections["stocks"]["payload"]["items"]}
    assert takes["SPY"] == "Steady." and takes["GC=F"] == db.NO_REASON and takes["DEAD"] == ""


def test_enrich_news_never_drops_a_story(monkeypatch):
    monkeypatch.setattr(db, "_article_text", lambda url, timeout: "" if url.endswith("/2") else "Full article.")
    monkeypatch.setattr(db, "_hn_comments", lambda sid, timeout: ["Nice."] if sid == "1" else [])
    items = [dict(n) for n in NEWS]
    db.enrich_news(items)
    assert [(i["title"], i["article"], i["comments"]) for i in items] == [
        ("Big model ships", "Full article.", ["Nice."]),
        ("Tiny tool", "", []),
    ]


def test_deep_news_only_enriches_the_spoken_stories(stub, monkeypatch):
    seen = []
    monkeypatch.setattr(db, "enrich_news", lambda items, timeout=8.0: seen.append([i["id"] for i in items]))
    cfg = BriefingSettings(mode="plain", audible_news_count=1)
    db.build(cfg, deep_news=True)
    db.build(cfg)
    assert seen == [["1"]]


def test_narration_is_one_call_spoken_as_one_block(stub, monkeypatch):
    said = []
    monkeypatch.setattr(db, "speak_text", lambda text, label="": said.append(text))
    calls = []

    def fake(prompt, system, label, required, start=None, timeout=None):
        calls.append((required, prompt))
        parsed = {"narration": "Good morning. Big model ships."} if required == "narration" else {"items": []}
        return parsed, {"provider": "p", "model": "m", "usage": {}}

    monkeypatch.setattr(db, "ask_json", fake)
    cfg = BriefingSettings(mode="llm", audible_news_count=1)
    result = db.build(cfg, {"summary": "ok", "metrics": []}, narrate_news=False)
    usage = db._Usage()
    assert db.narrate_briefing(cfg, result, usage) == "Good morning. Big model ships."
    assert said == ["Good morning. Big model ships."] and usage.calls == 1
    assert calls[-1][0] == "narration" and "about 150 words" in calls[-1][1]  # regular is the default length

    def boom(*a, **k):
        raise RuntimeError("down")

    said.clear()
    monkeypatch.setattr(db, "ask_json", boom)
    assert "Top story: Big model ships" in db.narrate_briefing(cfg, result, db._Usage())  # plain wording, not silence
    assert said and "Top story" in said[0]


def test_narration_payload_and_followup(stub, monkeypatch):
    monkeypatch.setattr(db, "ask_json", lambda *a, **k: ({"items": [{"ticker": "SPY", "commentary": "Rate hopes."}]}, {"provider": "p", "model": "m", "usage": {}}))
    cfg = BriefingSettings(mode="llm", narration_length="long")
    result = db.build(cfg, {"summary": "System's fine.", "metrics": [{"name": "CPU", "detail": "CPU 95%", "flagged": True}]}, narrate_news=False)
    data = db.narration_payload(cfg, result)
    assert list(data) == ["system_health", "tech_news", "watchlist_values"]  # section order
    assert data["system_health"]["flagged"] == ["CPU 95%"] and len(data["tech_news"]) == cfg.audible_news_count
    question, reasons = db.watchlist_followup(cfg, result)
    assert question == cfg.speak_watchlist_followup and "SPY: Rate hopes." in reasons
    assert db.watchlist_followup(cfg, {**result, "mode": "plain"}) == (None, None)


# --- API: settings route, the handler mirror, the preview button ---

def test_briefing_settings_api_and_mirror(client):
    from conftest import AUTH, DATA_DIR

    body = client.get("/api/settings/briefing", headers=AUTH).json()
    assert body["mode"] == "llm" and [s["id"] for s in body["sections"]] == ["system", "news", "stocks"]

    r = client.patch(
        "/api/settings/briefing",
        headers=AUTH,
        json={"cpu_high_percent": 75, "system_metrics": ["cpu", "disk"], "deliver": "sandbox", "speak_system_ok": "All good."},
    )
    assert r.status_code == 200, r.text
    mirror = json.loads((DATA_DIR / "backend" / "briefing_mirror.json").read_text(encoding="utf-8"))
    assert (mirror["cpu_high"], mirror["metrics"], mirror["deliver"], mirror["speak_ok"]) == (75, ["cpu", "disk"], "sandbox", "All good.")

    assert client.patch("/api/settings/briefing", headers=AUTH, json={"extra_tabs": ["ftp://x"]}).status_code == 422
    assert client.patch("/api/settings/briefing", headers=AUTH, json={"audible_news_count": 99}).status_code == 422
    client.patch("/api/settings/briefing", headers=AUTH, json={"mode": "plain", "deliver": "both"})
    assert json.loads((DATA_DIR / "backend" / "briefing_mirror.json").read_text(encoding="utf-8"))["deliver"] == "sandbox"  # plain = silent
    client.post("/api/settings/briefing/reset", headers=AUTH)
    assert json.loads((DATA_DIR / "backend" / "briefing_mirror.json").read_text(encoding="utf-8"))["cpu_high"] == 90


def test_preview_endpoint_applies_mode_override(client, stub, monkeypatch):
    from conftest import AUTH

    monkeypatch.setattr(db, "ask_json", lambda *a, **k: pytest.fail("plain preview must not call the LLM"))
    r = client.post("/api/briefing/preview", headers=AUTH, json={"mode": "plain"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "plain" and body["usage"] is None
    assert [s["id"] for s in body["sections"]] == ["news", "stocks"]
