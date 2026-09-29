import os

import pytest
from conftest import AUTH

from backend.routers import autostart


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(argv):
        seen.append(argv)
        return 0

    monkeypatch.setattr(autostart, "_run", fake)
    return seen


@pytest.mark.skipif(os.name != "nt", reason="Task Scheduler is Windows only")
def test_enable_disable_and_status(client, calls):
    assert client.get("/api/autostart", headers=AUTH).json() == {"supported": True, "enabled": True}
    assert client.put("/api/autostart", headers=AUTH, json={"enabled": True}).json()["enabled"] is True
    assert "install_task.ps1" in calls[-1][-1]
    assert client.put("/api/autostart", headers=AUTH, json={"enabled": False}).status_code == 200
    assert "uninstall_task.ps1" in calls[-1][-1]


@pytest.mark.skipif(os.name != "nt", reason="Task Scheduler is Windows only")
def test_failure_is_reported(client, monkeypatch):
    monkeypatch.setattr(autostart, "_run", lambda argv: 1)
    assert client.put("/api/autostart", headers=AUTH, json={"enabled": True}).status_code == 500


@pytest.mark.skipif(os.name == "nt", reason="non-Windows behaviour")
def test_unsupported_elsewhere(client):
    assert client.get("/api/autostart", headers=AUTH).json() == {"supported": False, "enabled": False}
    assert client.put("/api/autostart", headers=AUTH, json={"enabled": True}).status_code == 501
