import pytest
from conftest import AUTH, TOKEN
from starlette.websockets import WebSocketDisconnect


def test_header_token_ok(client):
    assert client.get("/api/settings/general", headers=AUTH).status_code == 200


def test_missing_or_wrong_token_rejected(client):
    assert client.get("/api/settings/general").status_code == 403
    assert client.get("/api/settings/general", headers={"X-Ekko-Token": "x" * 64}).status_code == 403


def test_query_token_rejected_on_http(client):
    assert client.get(f"/api/settings/general?token={TOKEN}").status_code == 403


def test_foreign_origin_rejected(client):
    r = client.get("/api/settings/general", headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_tauri_origin_allowed(client):
    r = client.get("/api/settings/general", headers={**AUTH, "Origin": "http://tauri.localhost"})
    assert r.status_code == 200


def test_ws_subprotocol_token_accepted(client):
    with client.websocket_connect("ws://127.0.0.1/ws/status", subprotocols=["ekko", TOKEN]) as ws:
        assert ws.accepted_subprotocol == "ekko"


def test_ws_query_token_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"ws://127.0.0.1/ws/status?token={TOKEN}"):
            pass


def test_ws_wrong_subprotocol_token_rejected(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://127.0.0.1/ws/status", subprotocols=["ekko", "nope"]):
            pass


def test_health_needs_token(client):
    assert client.get("/api/health").status_code == 403
    body = client.get("/api/health", headers=AUTH).json()
    assert body["ok"] is True and body["version"]
