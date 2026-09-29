from conftest import AUTH


def test_llm_defaults(client):
    body = client.get("/api/settings/llm", headers=AUTH).json()
    assert body["provider"] == "gemini"
    assert body["failover_enabled"] is True
    assert body["failover_order"] == ["gemini", "deepseek", "claude_api", "openai", "claude_code", "ollama"]
    assert body["failover_cooldown_s"] == 120.0
    assert body["research_enabled"] is True
    assert body["budget_usd"] == {"deepseek": 2.0, "claude_api": 2.0, "openai": 2.0}


def test_llm_patch_persists_and_resets(client):
    r = client.patch("/api/settings/llm", headers=AUTH, json={"failover_order": ["openai", "gemini"], "provider": "openai"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings/llm", headers=AUTH).json()["failover_order"] == ["openai", "gemini"]
    client.post("/api/settings/llm/reset", headers=AUTH)
    assert client.get("/api/settings/llm", headers=AUTH).json()["provider"] == "gemini"


def test_llm_rejects_bad_order(client):
    for order in (["gemini", "gemini"], ["nope"], []):
        assert client.patch("/api/settings/llm", headers=AUTH, json={"failover_order": order}).status_code == 422, order


def test_llm_provider_must_be_in_order(client):
    r = client.patch("/api/settings/llm", headers=AUTH, json={"failover_order": ["gemini"], "provider": "openai"})
    assert r.status_code == 422


def test_unknown_field_rejected(client):
    assert client.patch("/api/settings/llm", headers=AUTH, json={"bogus": 1}).status_code == 422


def test_voice_and_system_sections(client):
    assert client.get("/api/settings/voice", headers=AUTH).json()["tts_engine"] == "piper"
    assert client.patch("/api/settings/voice", headers=AUTH, json={"tts_engine": "espeak"}).status_code == 422
    assert client.get("/api/settings/system", headers=AUTH).json() == {"os": "auto"}
    assert client.patch("/api/settings/system", headers=AUTH, json={"os": "linux"}).status_code == 200


def test_whisper_model_defaults_to_auto(client):
    assert client.get("/api/settings/general", headers=AUTH).json()["whisper_model"] == "auto"
