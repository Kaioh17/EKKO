from conftest import AUTH


def test_unanswered_request_is_refused(client):
    r = client.post("/api/approvals", json={"title": "t", "lines": ["a"], "timeout_s": 0.1}, headers=AUTH)
    assert r.json() == {"approved": False}


def test_answer_for_unknown_request_is_404(client):
    assert client.post("/api/approvals/nope", json={"approved": True}, headers=AUTH).status_code == 404
