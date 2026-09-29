import os

from conftest import AUTH, DATA_DIR

ENV = DATA_DIR / ".env"


def test_set_get_delete_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ENV.write_text("# keep me\nGEMINI_API_KEY=abc\n", encoding="utf-8")

    r = client.put("/api/keys/OPENAI_API_KEY", headers=AUTH, json={"value": "sk-secret-123456789"})
    assert r.status_code == 204
    text = ENV.read_text(encoding="utf-8")
    assert "# keep me" in text and "GEMINI_API_KEY=abc" in text and "OPENAI_API_KEY=sk-secret-123456789" in text
    assert os.environ["OPENAI_API_KEY"] == "sk-secret-123456789"

    listing = client.get("/api/keys", headers=AUTH).json()
    assert listing["OPENAI_API_KEY"] == {"set": True, "last4": "6789"}
    assert "sk-secret" not in client.get("/api/keys", headers=AUTH).text

    client.put("/api/keys/OPENAI_API_KEY", headers=AUTH, json={"value": "sk-new-000000000"})
    assert ENV.read_text(encoding="utf-8").count("OPENAI_API_KEY=") == 1

    assert client.delete("/api/keys/OPENAI_API_KEY", headers=AUTH).status_code == 204
    assert "OPENAI_API_KEY" not in ENV.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" not in os.environ


def test_unknown_key_rejected(client):
    assert client.put("/api/keys/PATH", headers=AUTH, json={"value": "x"}).status_code == 404


def test_value_cannot_inject_lines(client):
    r = client.put("/api/keys/GEMINI_API_KEY", headers=AUTH, json={"value": "a\nEKKO_X=1"})
    assert r.status_code == 422
