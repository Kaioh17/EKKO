import datetime

from conftest import AUTH

from backend import db


def test_history_and_days(client):
    db.add_chat_turn("hello", "hi there", "router", {"handled_by": "router", "total_s": 0.1})
    today = datetime.date.today().isoformat()

    days = client.get("/api/chat/days", headers=AUTH).json()
    assert any(d["day"] == today and d["count"] >= 2 for d in days)

    rows = client.get("/api/chat/history", headers=AUTH).json()  # default = today
    assert rows == client.get("/api/chat/history", params={"day": today}, headers=AUTH).json()
    assert [r["role"] for r in rows[-2:]] == ["user", "assistant"]
    assert rows[-1]["stats"]["handled_by"] == "router"

    assert client.get("/api/chat/history", params={"day": "2000-01-01"}, headers=AUTH).json() == []
    assert client.get("/api/chat/history", params={"day": "nope"}, headers=AUTH).status_code == 422
