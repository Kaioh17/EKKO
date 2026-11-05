import datetime
from conftest import AUTH

from memory import archive, day_log, store


def test_long_term_add_edit_delete(client):
    r = client.post("/api/memory/long-term/lines", json={"section": "Preferences", "text": "likes\ntea  "}, headers=AUTH)
    assert r.status_code == 201
    (line,) = r.json()["sections"]["Preferences"]
    assert line["text"] == "likes tea" and line["date"]

    body = {"section": "Preferences", "index": 0, "expected": "likes tea", "text": "likes green tea"}
    r = client.put("/api/memory/long-term/lines", json=body, headers=AUTH)
    assert r.json()["sections"]["Preferences"][0] == {"date": line["date"], "text": "likes green tea"}  # date kept

    # stale view: the text at that index is no longer what the UI saw
    assert client.put("/api/memory/long-term/lines", json=body, headers=AUTH).status_code == 409

    params = {"section": "Preferences", "index": 0, "expected": "likes green tea"}
    r = client.delete("/api/memory/long-term/lines", params=params, headers=AUTH)
    assert r.json()["sections"]["Preferences"] == []
    assert store.read_memory().sections["Preferences"] == ()


def test_long_term_rejects_bad_input(client):
    post = lambda **b: client.post("/api/memory/long-term/lines", json=b, headers=AUTH).status_code  # noqa: E731
    assert post(section="Nope", text="x") == 422
    assert post(section="Identity", text="   ") == 422
    assert post(section="Identity", text="x" * 501) == 422


def test_day_log_list_and_delete(client):
    day_log.clear_all()
    day_log.record_turn("hello", "hi there")
    day_log.record_turn("second", "reply")
    data = client.get("/api/memory/day-log", headers=AUTH).json()
    assert [t["user"] for t in data["turns"]] == ["hello", "second"] and data["tokens_est"] > 0
    ts = data["turns"][0]["ts"]
    assert client.delete("/api/memory/day-log/turn", params={"ts": ts}, headers=AUTH).status_code == 204
    assert client.delete("/api/memory/day-log/turn", params={"ts": ts}, headers=AUTH).status_code == 404
    assert client.delete("/api/memory/day-log", headers=AUTH).status_code == 204
    assert client.get("/api/memory/day-log", headers=AUTH).json()["turns"] == []


def test_archive_list_and_delete(client):
    key = "2026-10-03 | installer build"
    archive.write_entry(key, {"summary": "s", "topics": ["build"], "turns": 4, "details": "d"})
    (entry,) = [e for e in client.get("/api/memory/archive", headers=AUTH).json()["entries"] if e["key"] == key]
    assert entry["date"] == "2026-10-03" and entry["topics"] == ["build"]
    assert client.delete("/api/memory/archive", params={"key": key}, headers=AUTH).status_code == 204
    assert client.delete("/api/memory/archive", params={"key": key}, headers=AUTH).status_code == 404


STEPS = "Method\n1. Heat.\n2. Mix.\n3. Bake."


def _turn(n, user, assistant, **kw):
    """Distinct, ordered timestamps: a coarse Windows clock gives back-to-back turns the same ts, and ts is the review item id."""
    day_log.record_turn(user, assistant, now=datetime.datetime.now() - datetime.timedelta(minutes=10 - n), **kw)


def _resolve(client, item, action, **extra):
    return client.post("/api/memory/review/resolve", json={"id": item["id"], "action": action, **extra}, headers=AUTH)


def test_review_groups_versions_and_resolves_all(client, tmp_path):
    from memory import review

    day_log.clear_all()
    review.RESOLVED_PATH.unlink(missing_ok=True)
    _turn(0, "How do I make a kite?", STEPS)
    _turn(1, "how to make a kite", STEPS)
    _turn(2, "cake recipe please", STEPS)
    _turn(3, "hello", "Sorry.", kind="unrecognized")  # not a Keep item
    items = client.get("/api/memory/review", headers=AUTH).json()["items"]
    assert len(items) == 2
    kite = next(i for i in items if "kite" in i["title"])
    assert kite["versions"] == 2 and kite["filename"] == "how-to-make-a-kite.md"

    r = _resolve(client, kite, "file", filename="../../kite.md", folder=str(tmp_path))
    assert r.status_code == 200
    assert (tmp_path / "kite.md").read_text(encoding="utf-8").startswith("# how to make a kite")  # traversal stripped
    assert [i["title"] for i in client.get("/api/memory/review", headers=AUTH).json()["items"]] == ["cake recipe please"]
    assert _resolve(client, kite, "file").status_code == 404  # already resolved
    assert _resolve(client, items[0], "file", folder="relative/dir").status_code == 422


def test_review_memory_discard_and_no_overwrite(client, tmp_path):
    from memory import review

    day_log.clear_all()
    review.RESOLVED_PATH.unlink(missing_ok=True)
    _turn(4, "recipe for soup", STEPS)
    _turn(5, "how do I tie a tie", STEPS)
    tie, soup = client.get("/api/memory/review", headers=AUTH).json()["items"]
    assert _resolve(client, soup, "memory").status_code == 200
    assert any("recipe for soup" in line.text for line in store.read_memory().sections["Known Facts"])
    assert _resolve(client, tie, "discard").status_code == 200
    assert client.get("/api/memory/review", headers=AUTH).json()["items"] == []

    # same name twice never overwrites
    _turn(6, "recipe for soup again", "x\n- a\n- b\n- c")
    (again,) = client.get("/api/memory/review", headers=AUTH).json()["items"]
    (tmp_path / "soup.md").write_text("mine", encoding="utf-8")
    assert _resolve(client, again, "file", filename="soup", folder=str(tmp_path)).status_code == 200
    assert (tmp_path / "soup.md").read_text(encoding="utf-8") == "mine"
    assert (tmp_path / "soup-2.md").exists()


def test_context_lists_what_the_next_message_carries(client):
    day_log.clear_all()
    day_log.record_turn("what is my name", "Your name is Sam.")
    data = client.get("/api/memory/context", headers=AUTH).json()
    names = [s["name"] for s in data["sections"]]
    assert names == ["Long-term memory", "Archive titles", "Today so far", "Live conversation"]
    convo = data["sections"][3]
    assert "what is my name" in convo["text"] and convo["tokens"] > 0
    assert data["total_tokens"] == sum(s["tokens"] for s in data["sections"])
