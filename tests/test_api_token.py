"""/api/v1 for Home Assistant: only the API token opens it, and it can read the status and run the screen."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.api.admin_shared import hash_password, require_api_key, token_hash
from mirrordash_core.app import app

TOKEN = "t0ken-for-home-assistant"
CONFIG = {
    "admin_auth": {"hash": hash_password("secret", "salt"), "salt": "salt"},
    "api_token": {"hash": token_hash(TOKEN), "created": "2026-10-08"},
    "system": {"brightness": 70},
}
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    with patch("mirrordash_core.api.admin_shared.load_config", return_value=CONFIG), \
         patch("mirrordash_core.api.public.load_config", return_value=CONFIG):
        yield TestClient(app)


def test_only_the_token_opens_the_api(client):
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/status", headers={"Authorization": "Bearer secret"}).status_code == 401  # admin password
    assert client.get("/api/v1/status", headers={"X-API-Key": "secret"}).status_code == 401
    r = client.get("/api/v1/status", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["brightness"] == 70 and "screen_on" in r.json() and "sensors" in r.json()


def test_no_token_created_means_no_access():
    with patch("mirrordash_core.api.admin_shared.load_config", return_value={"system": {}}):
        assert TestClient(app).get("/api/v1/status", headers={"Authorization": "Bearer "}).status_code == 401


def test_token_runs_the_screen_and_brightness(client):
    from mirrordash_core.display_power import display_power_manager
    with patch.object(display_power_manager, "turn_off") as off:
        assert client.post("/api/v1/screen", json={"state": "off"}, headers=AUTH).status_code == 200
    off.assert_called_once()
    with patch("mirrordash_core.api.admin_system.update_system_settings", new_callable=AsyncMock) as save:
        assert client.post("/api/v1/brightness", json={"value": 40}, headers=AUTH).json()["brightness"] == 40
        assert client.post("/api/v1/brightness", json={"value": True}, headers=AUTH).status_code == 400
    save.assert_awaited_once_with(settings={"brightness": 40})


def test_admin_creates_and_removes_the_token():
    saved = {}

    def save(config):
        saved.clear()
        saved.update(config)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with patch("mirrordash_core.api.admin_system_panels.load_config", side_effect=lambda: dict(saved)), \
             patch("mirrordash_core.api.admin_system_panels.save_config", side_effect=save):
            client = TestClient(app)
            r = client.post("/admin/panels/system/api-token/create")
            token = r.text.split('id="api-token-value"')[1].split('value="')[1].split('"')[0]
            assert saved["api_token"]["hash"] == token_hash(token)  # only the hash is kept
            assert token not in client.get("/admin/panels/system/api-access").text  # shown once
            client.post("/admin/panels/system/api-token/remove")
            assert "api_token" not in saved
    finally:
        app.dependency_overrides.clear()
