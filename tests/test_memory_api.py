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
