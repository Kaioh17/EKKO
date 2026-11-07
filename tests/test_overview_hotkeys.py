from conftest import AUTH


def hotkeys(client):
    return {h["name"]: h for h in client.get("/api/overview/general", headers=AUTH).json()["hotkeys"]}


def test_overview_lists_hold_to_talk_off_by_default(client):
    h = hotkeys(client)["Hold to talk"]
    assert h["enabled"] is False and h["chord"] == "ctrl+alt+space"


def test_overview_follows_the_settings(client):
    r = client.patch(
        "/api/settings/general", headers=AUTH, json={"push_to_talk": True, "push_to_talk_hotkey": "Ctrl+Alt+T"}
    )
    assert r.status_code == 200, r.text
    h = hotkeys(client)["Hold to talk"]
    assert h["enabled"] is True and h["chord"] == "ctrl+alt+t"
    # the wake hint in the top bar looks for "wake": it must not pick this one
    assert "wake" not in "Hold to talk".lower()
