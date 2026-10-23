import asyncio
import json
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch

import pytest
from conftest import call
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules.service import install_module, uninstall_module
from mirrordash_core.features.settings.schema import get_globals_schema
from mirrordash_core.features.updates.service import (
    check_core_update,
    get_disk_usage,
    rebuild_venv,
    update_core,
)

# Setup config fixtures for test cases
mock_salt = "0123456789abcdef"
# Password 'secret' hashed with salt '0123456789abcdef'
mock_hash = hash_password("secret", mock_salt)

MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    },
    "system": {
        "rotation": "normal",
        "resolution": "auto",
        "brightness": 100,
        "volume": 80
    },
    "modules": {
        "mirrordash-clock": {
            "enabled": True,
            "position": "top_left"
        }
    }
}

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture(autouse=True)
def mock_admin_shared_load_config():
    with patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG):
        yield


@pytest.fixture(autouse=True)
def clear_config_cache():
    """Invalidate the in-memory config cache before each test to prevent test pollution."""
    from mirrordash_core.config import invalidate_config_cache
    invalidate_config_cache()
    yield
    invalidate_config_cache()


@patch("mirrordash_core.features.auth.routes.load_config")
@patch("mirrordash_core.features.auth.routes.save_config")
@patch("mirrordash_core.features.auth.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_auth_status_setup_required(mock_hotspot, mock_save, mock_load, client):
    # Setup not complete: "admin_auth" not in config
    mock_load.return_value = {}
    mock_hotspot.return_value = True

    response = client.get("/admin/auth/status")
    assert response.status_code == 200
    data = response.json()
    assert data["setup_required"] is True
    assert data["auth_corrupt"] is False
    assert data["wifi_hotspot_active"] is True


@patch("mirrordash_core.features.auth.routes.load_config")
@patch("mirrordash_core.features.auth.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_auth_status_corrupt_entry(mock_hotspot, mock_load, client):
    """Corrupt admin_auth must surface as auth_corrupt=True, NOT setup_required."""
    mock_hotspot.return_value = False

    for corrupt in [
        {"admin_auth": {}},                        # both keys missing
        {"admin_auth": {"hash": "abc"}},            # salt missing
        {"admin_auth": {"salt": "xyz"}},            # hash missing
        {"admin_auth": {"hash": "", "salt": "x"}},  # hash empty
        {"admin_auth": {"hash": "x", "salt": ""}},  # salt empty
    ]:
        mock_load.return_value = corrupt
        response = client.get("/admin/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data["setup_required"] is False, f"setup_required must be False for corrupt: {corrupt}"
        assert data["auth_corrupt"] is True, f"auth_corrupt must be True for: {corrupt}"


@patch("mirrordash_core.features.auth.routes.load_config")
@patch("mirrordash_core.features.auth.routes.save_config")
def test_auth_setup_success(mock_save, mock_load, client):
    mock_load.return_value = {}

    response = client.post("/admin/auth/setup", json={"password": "my_new_password"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    mock_save.assert_called_once()
    saved_config = mock_save.call_args[0][0]
    assert "admin_auth" in saved_config
    assert "hash" in saved_config["admin_auth"]
    assert "salt" in saved_config["admin_auth"]


@patch("mirrordash_core.features.auth.routes.load_config")
@patch("mirrordash_core.features.auth.routes.save_config")
def test_auth_setup_failures(mock_save, mock_load, client):
    # Password too short
    response = client.post("/admin/auth/setup", json={"password": "123"})
    assert response.status_code == 400
    assert "must be at least 4 characters" in response.json()["detail"]

    # Password already set (valid entry) — blocked
    mock_load.return_value = {"admin_auth": {"hash": "abc", "salt": "123"}}
    response = client.post("/admin/auth/setup", json={"password": "validpassword"})
    assert response.status_code == 400
    assert "is already set" in response.json()["detail"]

    # Corrupt entry — also blocked by setup; must use change-password instead
    mock_load.return_value = {"admin_auth": {}}
    response = client.post("/admin/auth/setup", json={"password": "validpassword"})
    assert response.status_code == 400
    assert "is already set" in response.json()["detail"]


@patch("mirrordash_core.admin.load_config")
def test_require_api_key_returns_403_on_corrupt_auth(mock_load, client):
    """require_api_key must return 403 (not 500) when auth config is corrupt."""
    for corrupt in [
        {"admin_auth": {"hash": "", "salt": "x"}},
        {"admin_auth": {"hash": "x", "salt": ""}},
        {"admin_auth": {}},
    ]:
        mock_load.return_value = corrupt
        response = client.get("/admin/system", headers={"X-API-Key": "anykey"})
        assert response.status_code == 403, f"Expected 403 for corrupt: {corrupt}"


@patch("mirrordash_core.features.auth.routes.load_config")
@patch("mirrordash_core.features.auth.routes.save_config")
def test_change_password_success(mock_save, mock_auth_load, client):
    """change-password requires the current valid password and updates it.
    The autouse fixture already patches admin_shared.load_config with MOCK_CONFIG.
    Use side_effect so the endpoint gets a fresh copy and cannot mutate MOCK_CONFIG.
    """
    import copy
    mock_auth_load.side_effect = lambda: copy.deepcopy(MOCK_CONFIG)

    response = client.post(
        "/admin/auth/change-password",
        json={"current_password": "secret", "new_password": "brand_new_password"},
        headers={"X-API-Key": "secret"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    saved = mock_save.call_args[0][0]
    assert saved["admin_auth"]["hash"] != mock_hash  # new hash stored
    assert "salt" in saved["admin_auth"]


@patch("mirrordash_core.features.auth.routes.load_config")
def test_change_password_requires_auth(mock_auth_load, client):
    """change-password must be rejected without a valid current password.
    Use side_effect to return a fresh copy so MOCK_CONFIG cannot be mutated.
    """
    import copy
    mock_auth_load.side_effect = lambda: copy.deepcopy(MOCK_CONFIG)

    # No header
    response = client.post("/admin/auth/change-password", json={"new_password": "newpass"})
    assert response.status_code == 401

    # Wrong password
    response = client.post(
        "/admin/auth/change-password",
        json={"new_password": "newpass"},
        headers={"X-API-Key": "wrongpassword"}
    )
    assert response.status_code == 401


def test_the_recovery_code_resets_the_password_once(client):
    """Setup shows a recovery code (stored only as a hash); it sets a new password and is then
    used up, replaced by a new one. A wrong code changes nothing."""
    store = {}
    with patch("mirrordash_core.features.auth.routes.load_config", side_effect=lambda: json.loads(json.dumps(store))), \
         patch("mirrordash_core.features.auth.routes.save_config", side_effect=lambda c: store.update(c)):
        code = client.post("/admin/auth/setup", json={"password": "first"}).json()["recovery_code"]
        assert len(code) == 14 and code.count("-") == 2
        assert code.replace("-", "") not in json.dumps(store)

        r = client.post("/admin/auth/recover", json={"code": "AAAA-AAAA-AAAA", "new_password": "second"})
        assert r.status_code == 401 and store["admin_auth"]["hash"] == hash_password("first", store["admin_auth"]["salt"])

        # Typed back in lower case without dashes still works
        r = client.post("/admin/auth/recover", json={"code": code.replace("-", "").lower(), "new_password": "second"})
        assert r.status_code == 200 and r.json()["recovery_code"] != code
        assert store["admin_auth"]["hash"] == hash_password("second", store["admin_auth"]["salt"])

        r = client.post("/admin/auth/recover", json={"code": code, "new_password": "third"})
        assert r.status_code == 401


def test_recovery_without_a_code_on_file_is_refused(client):
    for config in ({}, {"admin_auth": {}}, MOCK_CONFIG):
        with patch("mirrordash_core.features.auth.routes.load_config", return_value=config):
            r = client.post("/admin/auth/recover", json={"code": "ABCD-EFGH-JKMN", "new_password": "newpass"})
        assert r.status_code == 401


@patch("mirrordash_core.features.auth.routes.save_config")
def test_change_password_keeps_the_recovery_code(mock_save, client):
    recovery = {"hash": "h", "salt": "s"}
    config = {"admin_auth": {"hash": mock_hash, "salt": mock_salt, "recovery": recovery}}
    with patch("mirrordash_core.features.auth.routes.load_config", return_value=config):
        r = client.post("/admin/auth/change-password", json={"current_password": "secret", "new_password": "newpass"},
                        headers={"X-API-Key": "secret"})
    assert r.status_code == 200
    assert mock_save.call_args[0][0]["admin_auth"]["recovery"] == recovery



@patch("mirrordash_core.admin.load_config")
@patch("mirrordash_core.features.updates.service.load_config")
def test_auth_headers_required(mock_sys_load, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    
    # Missing header -> 401
    response = client.get("/admin/system")
    assert response.status_code == 401
    
    # Invalid password -> 401
    response = client.get("/admin/system", headers={"X-API-Key": "wrongpassword"})
    assert response.status_code == 401
    
    # Correct password -> 200
    with patch("mirrordash_core.system_settings.get_available_resolutions", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = ["1920x1080"]
        response = client.get("/admin/system", headers={"X-API-Key": "secret"})
        assert response.status_code == 200

@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
def test_update_system_settings_success(mock_set_ssh, mock_apply, mock_save, mock_load, client):
    mock_load.return_value = MOCK_CONFIG.copy()
    
    payload = {
        "rotation": "left",
        "resolution": "1080p",
        "brightness": 75,
        "volume": 50,
        "ssh": False,  # explicitly disable SSH so no password is required
        "display_control": {
            "mode": "manual",
            "interval": {"start": "07:00", "end": "22:00"},
            "pir": {"pin": 18, "timeout_minutes": 5},
            "button": {"pin": 23}
        }
    }
    
    response = client.post("/admin/system", json=payload, headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    mock_save.assert_called_once()
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg["system"]["rotation"] == "left"
    assert saved_cfg["system"]["brightness"] == 75

@patch("mirrordash_core.features.updates.service.load_config")
def test_update_system_settings_invalid_inputs(mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    
    headers = {"X-API-Key": "secret"}
    
    # Invalid rotation
    r = client.post("/admin/system", json={"rotation": "upside_down"}, headers=headers)
    assert r.status_code == 400
    
    # Out of bounds brightness
    r = client.post("/admin/system", json={"brightness": 5}, headers=headers)
    assert r.status_code == 400
    r = client.post("/admin/system", json={"brightness": 105}, headers=headers)
    assert r.status_code == 400

    # Out of bounds volume
    r = client.post("/admin/system", json={"volume": -5}, headers=headers)
    assert r.status_code == 400
    r = client.post("/admin/system", json={"volume": 105}, headers=headers)
    assert r.status_code == 400

def test_update_screen_state(client):
    # This route is unauthenticated (e.g. for Home Assistant)
    with patch("mirrordash_core.features.power.display_power.display_power_manager") as manager:
        r = client.post("/admin/screen", json={"state": "on"})
        assert r.status_code == 200 and r.json()["status"] == "success"
        manager.wake.assert_called_once_with(None)  # the configured screen timeout

        assert client.post("/admin/screen", json={"state": "on", "timeout_minutes": 2}).status_code == 200
        manager.wake.assert_called_with(2)

        assert client.post("/admin/screen", json={"state": "off"}).status_code == 200
        manager.turn_off.assert_called_once_with()

    assert client.post("/admin/screen", json={"state": "maybe"}).status_code == 400
    assert client.post("/admin/screen", json={"state": "on", "timeout_minutes": 0}).status_code == 400
    assert client.post("/admin/screen", json={"state": "on", "timeout_minutes": "5"}).status_code == 400

def test_mirror_page_hears_the_version_first(client):
    """The kiosk reloads itself when the version it hears differs from the one it was loaded with."""
    from mirrordash_core.features.kiosk.ws import manager
    manager.latest_messages = {"clock": {"module": "clock", "position": "top_left", "html": "<p>12:00</p>"}}
    with patch("mirrordash_core.features.kiosk.ws.get_core_version", return_value="1.2.3"), \
         client.websocket_connect("/ws") as ws:
        assert ws.receive_json() == {"type": "hello", "version": "1.2.3", "online": True,
                                    "video_is_playing": False}
        assert ws.receive_json()["module"] == "clock"  # then the modules, as before
    manager.latest_messages = {}


def test_reload_screen_button(client):
    with patch("mirrordash_core.features.kiosk.ws.manager.broadcast", new_callable=AsyncMock) as broadcast:
        r = client.post("/admin/panels/system/reload-screen", headers={"X-API-Key": "secret"})
    assert r.status_code == 200
    broadcast.assert_awaited_once_with({"action": "reload"})
    assert client.post("/admin/panels/system/reload-screen").status_code == 401


@patch("mirrordash_core.features.kiosk.routes.module_loader")
def test_public_active_modules_endpoint(mock_loader, client):
    # Setup mock active modules
    mock_module_instance = MagicMock()
    mock_module_instance.config = {"position": "top_right"}
    mock_module_instance.translate = lambda key, default: "Clock Module Title" if key == "title" else default
    
    mock_loader.instances = {"mirrordash_clock": mock_module_instance}
    
    r = client.get("/api/active-modules")
    assert r.status_code == 200
    data = r.json()
    assert "modules" in data
    assert len(data["modules"]) == 1
    assert data["modules"][0]["name"] == "mirrordash_clock"
    assert data["modules"][0]["position"] == "top_right"
    assert data["modules"][0]["title"] == "Clock Module Title"

def test_config_positions_validation():
    from mirrordash_core.features.settings.schema import validate_config
    for pos in ["top_center", "middle_left", "middle_right", "bottom_center"]:
        validate_config({"modules": {"mirrordash-clock": {"enabled": True, "position": pos}}})
    with pytest.raises(ValueError, match="invalid position"):
        validate_config({"modules": {"mirrordash-clock": {"enabled": True, "position": "middle_top"}}})

@patch("mirrordash_core.features.modules.service.load_config")
@patch("mirrordash_core.features.modules.service.save_config")
@patch("mirrordash_core.features.modules.service.asyncio.create_subprocess_exec")
def test_uninstall_module_success(mock_subproc, mock_save, mock_load, client):
    config = MOCK_CONFIG.copy()
    config["modules"] = {"mirrordash-clock": {"enabled": True, "position": "top_left"}}
    mock_load.return_value = config
    
    # Mock subprocess return
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"output", b"")
    mock_subproc.return_value = mock_process
    
    response = call(uninstall_module, "mirrordash-clock")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Assert that config was saved and clock module deleted
    mock_save.assert_called_once()
    saved_config = mock_save.call_args[0][0]
    assert "mirrordash-clock" not in saved_config["modules"]
    
    # Assert subprocess executed uninstall
    mock_subproc.assert_any_call(
        "uv", "pip", "uninstall", "--python", ANY, "-y", "mirrordash-clock",
        stdout=-1, stderr=-1, env=ANY
    )

@patch("mirrordash_core.features.modules.service.load_config")
def test_uninstall_module_invalid_name(mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    response = call(uninstall_module, "invalid; rm -rf /")
    assert response.status_code == 400
    assert "Invalid package name" in response.json()["detail"]


# ---------------------------------------------------------------------------
# SSH Password Enforcement Tests
# ---------------------------------------------------------------------------

SYSTEM_PAYLOAD_BASE = {
    "rotation": "normal",
    "resolution": "auto",
    "brightness": 80,
    "volume": 60,
    "display_control": {
        "mode": "manual",
        "interval": {"start": "07:00", "end": "22:00"},
        "pir": {"pin": 18, "timeout_minutes": 5},
        "button": {"pin": 23}
    }
}


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.hardware.display.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_without_password_rejected(
    mock_get_ssh, mock_screen, mock_apply, mock_save, mock_load, client
):
    """Enabling SSH without providing a password must return 400."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True}

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.hardware.display.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_with_short_password_rejected(
    mock_get_ssh, mock_screen, mock_apply, mock_save, mock_load, client
):
    """Enabling SSH with a password shorter than 8 chars must return 400."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True, "pi_password": "short"}

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.hardware.display.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec")
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_with_valid_password_calls_chpasswd(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_save, mock_load, client
):
    """Enabling SSH with a valid password hashes it, applies the hash for pi and enables SSH."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True, "pi_password": "SecurePass1!"}

    # Mock subprocesses success (first openssl, then the pi password helper)
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(side_effect=[(b"$6$salt$hash\n", b""), (b"", b"")])
    mock_subproc.return_value = mock_process

    with patch("builtins.open", mock_open()):
        response = client.post("/admin/system", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    # Verify both openssl and the helper were called
    assert mock_subproc.call_count == 2
    mock_subproc.assert_any_call(
        "sudo", "-n", "/usr/local/bin/mirrordash-pi-password",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    mock_subproc.assert_any_call(
        "openssl", "passwd", "-6", "-stdin",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Verify both communicates were called
    assert mock_process.communicate.call_count == 2
    mock_process.communicate.assert_any_call(input=b"$6$salt$hash\n")
    mock_process.communicate.assert_any_call(input=b"SecurePass1!")


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.hardware.display.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec")
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_disable_ssh_does_not_call_chpasswd(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_save, mock_load, client
):
    """Disabling SSH must NOT call chpasswd regardless of pi_password field."""
    mock_get_ssh.return_value = True
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": False}

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # chpasswd must NOT have been called when disabling SSH
    mock_subproc.assert_not_called()


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.hardware.display.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec")
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_ssh_already_enabled_does_not_require_password(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_save, mock_load, client
):
    """If SSH is already enabled, saving settings with SSH enabled does not require a password."""
    mock_load.return_value = MOCK_CONFIG
    mock_get_ssh.return_value = True
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True}  # True but no password provided

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # chpasswd must NOT have been called
    mock_subproc.assert_not_called()


@patch("mirrordash_core.features.modules.service.scan_community_modules_now", new_callable=AsyncMock)
@patch("mirrordash_core.features.modules.service.load_config")
def test_list_community_modules(mock_load, mock_scan, client):
    import mirrordash_core.features.modules.service as adm_mods
    mock_load.return_value = MOCK_CONFIG
    adm_mods.LAST_SCAN_TIMESTAMP = None
    async def scan():
        adm_mods.DISCOVERED_COMMUNITY_MODULES = [{"name": "mirrordash-clock", "install_name": "git+https://github.com/Menturan/mirrordash-clock.git"}]
        adm_mods.LAST_SCAN_TIMESTAMP = "now"
    mock_scan.side_effect = scan
    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/community-modules", headers=headers)
    assert response.status_code == 200
    modules = response.json()
    assert isinstance(modules, list)
    assert len(modules) > 0
    assert any(m["name"] == "mirrordash-clock" for m in modules)


def test_get_globals_schema():
    response = call(get_globals_schema)
    assert response.status_code == 200
    schema = response.json()
    assert isinstance(schema, dict)
    assert schema["title"] == "Global Settings"
    assert "properties" in schema
    assert "language" in schema["properties"]


# ---------------------------------------------------------------------------
# Core self-update tests
# ---------------------------------------------------------------------------

# A newer MirrorDash on the Python already running (update_core asks PyPI first)
SAME_PYTHON = {"update_available": True, "needs_python": None, "latest_version": "1.1.0"}

@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
def test_core_update_check_up_to_date(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns update_available=False when versions match."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.return_value = ("1.0.0", ">=3.14")  # PyPI returns same version

    response = call(check_core_update)
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == "1.0.0"
    assert data["latest_version"] == "1.0.0"
    assert data["update_available"] is False


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
def test_core_update_check_update_available(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns update_available=True when PyPI has a newer version."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.return_value = ("1.2.0", ">=3.14")  # PyPI has a newer version

    response = call(check_core_update)
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == "1.0.0"
    assert data["latest_version"] == "1.2.0"
    assert data["update_available"] is True


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
def test_core_update_check_pypi_error(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns 502 when PyPI is unreachable."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.side_effect = RuntimeError("PyPI request failed: connection timeout")

    response = call(check_core_update)
    assert response.status_code == 502
    assert "PyPI request failed" in response.json()["detail"]


@patch("mirrordash_core.features.updates.service.check_core_update", new=AsyncMock(return_value=SAME_PYTHON))
@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
@pytest.mark.parametrize("prerelease", [False, True])
def test_core_update_success(mock_version, mock_create_task, mock_exec, mock_restart,
                              mock_load, prerelease, client):
    """POST /admin/core-update succeeds, triggers restart, and installs test versions only when opted in."""
    mock_load.return_value = {**MOCK_CONFIG, "system": {**MOCK_CONFIG.get("system", {}), "prerelease": prerelease}}

    def proc(stdout):
        p = MagicMock(returncode=0)
        p.communicate = AsyncMock(return_value=(stdout, b""))
        return p
    mock_exec.side_effect = [proc(b""), proc(b"1.1.0rc1\n")]  # uv install, then the installed version

    response = call(update_core)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "Restarting" in data["message"]
    mock_create_task.assert_called_once()
    install = mock_exec.call_args_list[0].args
    assert ("--prerelease=allow" in install) is prerelease
    assert install[install.index("--refresh-package") + 1] == "mirrordash"  # a fresh index, not uv's cache


@patch("mirrordash_core.features.updates.service.check_core_update", new=AsyncMock(return_value=SAME_PYTHON))
@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
def test_core_update_without_a_newer_version_doesnt_restart(mock_version, mock_create_task, mock_exec,
                                                           mock_load, client):
    """uv also succeeds when PyPI's index doesn't show the new version yet: no restart, a clear message."""
    mock_load.return_value = MOCK_CONFIG
    def proc(stdout):
        p = MagicMock(returncode=0)
        p.communicate = AsyncMock(return_value=(stdout, b""))
        return p
    mock_exec.side_effect = [proc(b""), proc(b"1.0.0\n")]

    response = call(update_core)
    assert response.status_code == 409
    assert "isn't available yet" in response.json()["detail"]
    mock_create_task.assert_not_called()


@patch("mirrordash_core.features.updates.service.check_core_update", new=AsyncMock(return_value=SAME_PYTHON))
@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
def test_core_update_failure(mock_version, mock_create_task, mock_exec,
                              mock_load, client):
    """POST /admin/core-update returns 500 when uv pip install fails."""
    mock_load.return_value = MOCK_CONFIG

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"pip install failed"))
    mock_exec.return_value = mock_proc

    response = call(update_core)
    assert response.status_code == 500
    assert "Upgrade failed" in response.json()["detail"]
    mock_create_task.assert_not_called()


def test_version_key_orders_pre_releases_below_their_release():
    from mirrordash_core.config import version_key
    ordered = ["0.4.0", "0.5.0a1", "0.5.0b2", "0.5.0rc1", "0.5.0rc2", "0.5.0", "v0.5.1", "0.10.0"]
    assert sorted(reversed(ordered), key=version_key) == ordered
    assert version_key("0.5.0-rc1") == version_key("0.5.0rc1")
    assert version_key("unknown") < version_key("0.0.1")


@pytest.mark.parametrize("prerelease, expected", [(False, "0.4.0"), (True, "0.5.0rc1")])
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="0.4.0")
def test_core_update_check_offers_test_versions_only_when_opted_in(mock_version, prerelease, expected, client):
    """With "Test versions" on, the newest pre-release on PyPI is offered; otherwise only final releases."""
    pypi = {"info": {"version": "0.4.0"},
            "releases": {"0.3.9": [{}], "0.4.0": [{}], "0.5.0rc1": [{}], "0.6.0rc1": []}}  # no files: never offered
    response_body = MagicMock()
    response_body.__enter__.return_value.read.return_value = json.dumps(pypi).encode()
    config = {**MOCK_CONFIG, "system": {**MOCK_CONFIG.get("system", {}), "prerelease": prerelease}}
    with patch("mirrordash_core.features.updates.service.load_config", return_value=config), \
         patch("mirrordash_core.features.updates.service.urllib.request.urlopen", return_value=response_body):
        data = call(check_core_update).json()
    assert data["latest_version"] == expected
    assert data["update_available"] is prerelease


@patch("mirrordash_core.features.updates.service.load_config")
@patch("shutil.disk_usage")
def test_disk_usage_success(mock_disk_usage, mock_load, client):
    """GET /admin/disk-usage returns correct calculated metrics."""
    mock_load.return_value = MOCK_CONFIG
    mock_disk_usage.return_value = (10000000000, 3000000000, 7000000000)

    response = call(get_disk_usage)
    assert response.status_code == 200

    data = response.json()
    assert data["total_bytes"] == 10000000000
    assert data["used_bytes"] == 3000000000
    assert data["free_bytes"] == 7000000000
    assert data["percent_used"] == 30.0
    mock_disk_usage.assert_called_once_with("/")


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
@patch("mirrordash_core.venv.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.commit_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.revert_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.get_venv_paths", return_value=True)
def test_rebuild_venv_success(mock_paths, mock_revert, mock_commit, mock_prepare, mock_version,
                               mock_create_task, mock_exec, mock_restart,
                               mock_load, client):
    """POST /admin/rebuild-venv succeeds, rebuilds venv, and triggers restart."""
    mock_load.return_value = MOCK_CONFIG
    from pathlib import Path
    mock_prepare.return_value = (Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_exec.return_value = mock_proc

    response = call(rebuild_venv)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "rebuilt successfully" in data["message"]
    mock_prepare.assert_awaited_once_with(force_clean=True, python=None)
    mock_commit.assert_awaited_once_with(Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))
    mock_create_task.assert_called_once()


REBUILD_MODULES = [
    {"name": "mirrordash_news", "package_name": "mirrordash-news", "version": "1.2.0", "type": "pypi"},
    {"name": "mirrordash_calendar", "package_name": "mirrordash-calendar", "version": "0.3.0", "type": "pypi",
     "source": "git+https://github.com/someone/mirrordash-calendar@abc123"},
]


@pytest.mark.parametrize("failing", [None, "mirrordash-news==1.2.0"])
@patch("mirrordash_core.features.backup.service.installed_modules", return_value=REBUILD_MODULES)
@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="1.0.0")
@patch("mirrordash_core.venv.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.commit_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.revert_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.get_venv_paths", return_value=True)
def test_rebuild_venv_reinstalls_installed_modules(mock_paths, mock_revert, mock_commit, mock_prepare, mock_version,
                                                   mock_create_task, mock_restart, mock_modules, failing):
    """Rebuild reinstalls what is installed (PyPI at its version, GitHub at its commit), not what
    config names; one module failing undoes the swap instead of losing the module."""
    from pathlib import Path
    mock_prepare.return_value = (Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))
    installed = []

    async def fake_uv_pip(python, command, *args):
        installed.append(args[-1])
        return (1, "", "boom") if args[-1] == failing else (0, "", "")

    with patch("mirrordash_core.features.updates.service.uv_pip", side_effect=fake_uv_pip):
        response = call(rebuild_venv)
    assert installed[:3] == ["mirrordash==1.0.0", "mirrordash-news==1.2.0",
                             "git+https://github.com/someone/mirrordash-calendar@abc123"][:len(installed)]
    if failing:
        assert response.status_code == 500 and "mirrordash-news" in response.json()["detail"]
        mock_revert.assert_awaited_once()
        mock_commit.assert_not_awaited()
    else:
        assert response.status_code == 200 and len(installed) == 3
        mock_commit.assert_awaited_once()


@patch("mirrordash_core.features.hardware.routes.load_config")
@patch("mirrordash_core.system_settings.get_available_resolutions", new_callable=AsyncMock)
def test_get_panel_system(mock_res, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    mock_res.return_value = ["1920x1080"]
    headers = {"X-API-Key": "secret"}
    
    response = client.get("/admin/panels/system", headers=headers)
    assert response.status_code == 200
    assert "sys-rotation" in response.text
    assert "sys-ssh" not in response.text  # SSH lives in Settings › Developer


@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
def test_save_system_settings_route_flat_conversion(mock_set_ssh, mock_apply, mock_save, mock_load, client):
    mock_load.return_value = MOCK_CONFIG.copy()
    headers = {"X-API-Key": "secret"}
    
    # Send flat data with start_h/start_m/end_h/end_m
    form_data = {
        "brightness": "70",
        "volume": "60",
        "rotation": "left",
        "resolution": "auto",
        "display_control[mode]": "interval",
        "display_control[interval][start_h]": "8",
        "display_control[interval][start_m]": "15",
        "display_control[interval][end_h]": "22",
        "display_control[interval][end_m]": "45"
    }
    
    response = client.post("/admin/panels/system/save", data=form_data, headers=headers)
    assert response.status_code == 200
    assert "Saved." in response.headers["HX-Trigger-After-Swap"]
    
    mock_save.assert_called_once()
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg["system"]["brightness"] == 70
    assert saved_cfg["system"]["volume"] == 60
    assert saved_cfg["system"]["rotation"] == "left"
    assert saved_cfg["system"]["display_control"]["interval"]["start"] == "08:15"
    assert saved_cfg["system"]["display_control"]["interval"]["end"] == "22:45"


# ---------------------------------------------------------------------------
# GitHub Module Scanning and Enforcements Tests
# ---------------------------------------------------------------------------

@patch("mirrordash_core.features.modules.service.urllib.request.urlopen")
@patch("mirrordash_core.features.modules.service.load_config")
def test_scan_lists_github_modules_with_one_request(mock_load, mock_urlopen, client):
    """GitHub allows 60 unauthenticated requests an hour per IP, shared by the home network: the
    scan makes one (the search); the release is looked up when a module is installed."""
    mock_load.return_value = MOCK_CONFIG
    search = {"items": [
        {"name": "mirrordash-weather", "owner": {"login": "u"}, "html_url": "https://github.com/u/mirrordash-weather", "description": "Weather"},
        {"name": "mirrordash-calendar", "owner": {"login": "u"}, "html_url": "https://github.com/u/mirrordash-calendar", "description": None},
        {"name": "MirrorDashboard", "owner": {"login": "x"}, "html_url": "https://github.com/x/MirrorDashboard", "description": "not a module"},
    ]}
    github_urls = []

    def urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else req
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.info.return_value.get.return_value = None
        if "api.github.com" in url:
            github_urls.append(url)
            resp.read.return_value = json.dumps(search).encode()
        else:  # PyPI: no mirrordash-* packages
            resp.read.return_value = b""
        return resp

    mock_urlopen.side_effect = urlopen
    headers = {"X-API-Key": "secret"}
    assert client.post("/admin/community-modules/scan", headers=headers).status_code == 200
    modules = {m["name"]: m for m in client.get("/admin/community-modules", headers=headers).json()}

    assert len(github_urls) == 1 and "search/repositories" in github_urls[0]
    assert set(modules) == {"mirrordash-weather", "mirrordash-calendar"}
    assert modules["mirrordash-weather"]["install_name"] == "git+https://github.com/u/mirrordash-weather.git"

    # GitHub refusing (hourly limit): the refresh fails visibly and the earlier list stays
    from urllib.error import HTTPError
    mock_urlopen.side_effect = lambda req, *a, **k: (_ for _ in ()).throw(HTTPError(req.full_url, 403, "rate limit", {}, None)) \
        if "api.github.com" in req.full_url else urlopen(req)
    with pytest.raises(HTTPError):
        client.post("/admin/community-modules/scan", headers=headers)
    assert {m["name"] for m in client.get("/admin/community-modules", headers=headers).json()} == set(modules)


@patch("mirrordash_core.features.modules.service.urllib.request.urlopen")
@patch("mirrordash_core.features.modules.service.load_config")
def test_scan_marks_official_modules_and_no_pypi_package_takes_their_name(mock_load, mock_urlopen, client):
    """Official means GitHub's owner field says Menturan; a PyPI package claiming that repository isn't,
    and one with an official module's name is replaced by the official module (what Install installs)."""
    mock_load.return_value = MOCK_CONFIG
    search = {"items": [
        {"name": "mirrordash-weather", "owner": {"login": "Menturan"}, "html_url": "https://github.com/Menturan/mirrordash-weather"},
        {"name": "mirrordash-tides", "owner": {"login": "someone"}, "html_url": "https://github.com/someone/mirrordash-tides"},
    ]}
    pypi_meta = {"info": {"summary": "x", "project_urls": {"Source": "https://github.com/Menturan/mirrordash-fake"}}}

    def urlopen(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else req
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.info.return_value.get.return_value = None
        if "api.github.com" in url:
            resp.read.return_value = json.dumps(search).encode()
        elif url.endswith("/simple/"):
            resp.read.return_value = b'<a href="/simple/mirrordash-weather/"></a><a href="/simple/mirrordash-fake/"></a>'
        else:
            resp.read.return_value = json.dumps(pypi_meta).encode()
        return resp

    mock_urlopen.side_effect = urlopen
    headers = {"X-API-Key": "secret"}
    assert client.post("/admin/community-modules/scan", headers=headers).status_code == 200
    modules = {m["name"]: m for m in client.get("/admin/community-modules", headers=headers).json()}

    assert set(modules) == {"mirrordash-weather", "mirrordash-fake", "mirrordash-tides"}
    assert modules["mirrordash-weather"]["official"] and modules["mirrordash-weather"]["source"] == "github"
    assert not modules["mirrordash-fake"]["official"] and not modules["mirrordash-tides"]["official"]


@patch("mirrordash_core.features.modules.service.urllib.request.urlopen")
@patch("mirrordash_core.venv.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.commit_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.features.modules.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.modules.service.asyncio.create_subprocess_exec")
def test_install_module_enforce_releases(mock_exec, mock_restart, mock_commit, mock_prepare, mock_urlopen, client):
    from pathlib import Path
    mock_prepare.return_value = (Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))
    
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_proc.wait.return_value = 0
    mock_exec.return_value = mock_proc
    
    # Success case: repo has release
    mock_resp_ok = MagicMock()
    mock_resp_ok.__enter__.return_value = mock_resp_ok
    mock_resp_ok.read.return_value = json.dumps({"tag_name": "v1.0.0"}).encode("utf-8")
    mock_urlopen.return_value = mock_resp_ok
    
    # A module from Discover comes without a tag: it is pinned to the latest release
    payload_ok = {"package_name": "git+https://github.com/user1/mirrordash-widget-ok.git"}
    r1 = call(install_module, **payload_ok)
    assert r1.status_code == 200
    assert any(c.args[-1] == "git+https://github.com/user1/mirrordash-widget-ok.git@v1.0.0" for c in mock_exec.call_args_list)

    from urllib.error import HTTPError
    payload_fail = {"package_name": "git+https://github.com/user2/mirrordash-widget-norelease.git"}
    # No release at all
    mock_urlopen.side_effect = HTTPError("url", 404, "Not Found", {}, None)
    r2 = call(install_module, **payload_fail)
    assert r2.status_code == 400
    assert "does not have any official releases" in r2.json()["detail"]
    # GitHub's hourly limit is not reported as "no releases"
    mock_urlopen.side_effect = HTTPError("url", 403, "rate limit exceeded", {}, None)
    r3 = call(install_module, **payload_fail)
    assert r3.status_code == 503
    assert "Try again in an hour" in r3.json()["detail"]


@patch("mirrordash_core.fetch.urllib.request.urlopen")
@patch("importlib.metadata.entry_points")
def test_check_module_update_github(mock_entry_points, mock_urlopen, client):
    mock_dist = MagicMock()
    direct_url_content = json.dumps({
        "url": "https://github.com/user1/mirrordash-widget-ok",
        "vcs_info": {
            "vcs": "git",
            "commit_id": "oldcommit"
        }
    })
    mock_dist.read_text.return_value = direct_url_content
    mock_dist.name = "mirrordash-widget-ok"
    mock_dist.version = "1.0.0"
    
    mock_ep = MagicMock()
    mock_ep.name = "mirrordash-widget-ok"
    mock_ep.dist = mock_dist
    
    mock_entry_points.return_value = [mock_ep]
    
    headers = {"X-API-Key": "secret"}
    
    # Case 1: Newer release exists (v1.1.0)
    mock_resp_new = MagicMock()
    mock_resp_new.__enter__.return_value = mock_resp_new
    mock_resp_new.read.return_value = json.dumps({"tag_name": "v1.1.0"}).encode("utf-8")
    mock_urlopen.return_value = mock_resp_new
    
    r1 = client.get("/admin/panels/modules/check-update/mirrordash-widget-ok", headers=headers)
    assert r1.status_code == 200
    assert "Update Available" in r1.text
    assert "git+https://github.com/user1/mirrordash-widget-ok@v1.1.0" in r1.text
    
    # Case 2: No newer release (v1.0.0)
    mock_resp_same = MagicMock()
    mock_resp_same.__enter__.return_value = mock_resp_same
    mock_resp_same.read.return_value = json.dumps({"tag_name": "v1.0.0"}).encode("utf-8")
    mock_urlopen.return_value = mock_resp_same

    # Within the cache TTL the earlier GitHub answer is reused (no new request per page view)
    calls = mock_urlopen.call_count
    r_cached = client.get("/admin/panels/modules/check-update/mirrordash-widget-ok", headers=headers)
    assert "Update Available" in r_cached.text
    assert mock_urlopen.call_count == calls

    from mirrordash_core import fetch
    fetch._remote_json_cache.clear()
    r2 = client.get("/admin/panels/modules/check-update/mirrordash-widget-ok", headers=headers)
    assert r2.status_code == 200
    assert r2.text == ""

    # Installed from the latest release tag: up to date, even if the package version lags the tag
    mock_dist.version = "0.1.0"
    mock_dist.read_text.return_value = json.dumps({
        "url": "https://github.com/user1/mirrordash-widget-ok",
        "vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "v1.0.0"}})
    r3 = client.get("/admin/panels/modules/check-update/mirrordash-widget-ok", headers=headers)
    assert r3.text == ""


@patch("mirrordash_core.features.modules.service.urllib.request.urlopen")
def test_get_panel_and_discover_modules(mock_urlopen, client):
    import mirrordash_core.features.modules.service as adm_mods
    adm_mods.LAST_SCAN_TIMESTAMP = None
    adm_mods.DISCOVERED_COMMUNITY_MODULES = []
    
    headers = {"X-API-Key": "secret"}
    
    # 1. Test get_panel_modules (loads instantly, doesn't query github/pypi community modules)
    r1 = client.get("/admin/panels/modules", headers=headers)
    assert r1.status_code == 200
    assert "Loading discoverable community modules..." in r1.text
    
    # 2. Test get_discover_modules (async HTMX endpoint)
    def urlopen_mock(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        resp.__enter__.return_value = resp
        if "pypi.org/simple" in url:
            resp.read.return_value = b""
            resp.info.return_value.get.return_value = None
        elif "api.github.com/search" in url:
            resp.read.return_value = json.dumps({
                "items": [
                    {
                        "name": "mirrordash-test-widget",
                        "owner": {"login": "testowner"},
                        "html_url": "https://github.com/testowner/mirrordash-test-widget",
                        "description": "Test community widget description",
                        "stargazers_count": 7
                    },
                    {   # installed already (its entry point is mirrordash_clock): not offered again
                        "name": "mirrordash-clock",
                        "owner": {"login": "testowner"},
                        "html_url": "https://github.com/testowner/mirrordash-clock",
                    }
                ]
            }).encode("utf-8")
        elif "releases/latest" in url:
            resp.read.return_value = json.dumps({"tag_name": "v1.2.3"}).encode("utf-8")
        else:
            resp.read.return_value = b""
        return resp

    mock_urlopen.side_effect = urlopen_mock
    
    installed = {"modules": {"mirrordash_clock": {"package_name": "mirrordash-clock"}}}
    with patch("mirrordash_core.features.modules.routes.list_modules", AsyncMock(return_value=installed)):
        r2 = client.get("/admin/panels/modules/discover", headers=headers)
    assert r2.status_code == 200
    assert "mirrordash-clock" not in r2.text
    assert "mirrordash-test-widget" in r2.text
    # The details come from the search response itself: no request per module
    assert "https://raw.githubusercontent.com/testowner/mirrordash-test-widget/HEAD/screenshot.png" in r2.text
    assert "<dd>testowner</dd>" in r2.text and "<dd>7</dd>" in r2.text
    assert 'href="https://github.com/testowner/mirrordash-test-widget#readme"' in r2.text
    assert "Test community widget description" in r2.text


def test_modules_map_switches_each_instance(client):
    cfg = {"modules": {"mirrordash-clock": {"position": "top_left"},
                       "mirrordash-clock-2": {"module": "mirrordash-clock", "position": "bottom_right", "enabled": False},
                       "mirrordash_gone": {"position": "middle_left"}}}
    clock = SimpleNamespace(name="mirrordash_clock", dist=None)
    with patch("mirrordash_core.features.modules.service.load_config", return_value=cfg), \
         patch("mirrordash_core.features.modules.service.find_entry_point",
               side_effect=lambda name, eps: None if "gone" in name else clock), \
         patch("mirrordash_core.features.modules.routes.list_modules", AsyncMock(return_value={"modules": {}})):
        html = client.get("/admin/panels/modules", headers={"X-API-Key": "secret"}).text
    # Each instance's switch flips that instance, and shows whether it is on
    for inst, on in (("mirrordash-clock", True), ("mirrordash-clock-2", False)):
        url = html.index(f"/admin/panels/modules/config/mirrordash_clock/toggle?instance_id={inst}")
        assert ("checked" in html[html.rindex("<input", 0, url):url]) == on
    assert "Clock 2" in html
    # An instance whose module is gone can still be removed from the mirror
    assert 'hx-post="/admin/panels/modules/config/mirrordash_gone/remove?instance_id=mirrordash_gone"' in html


def test_github_repo_from_install_urls():
    from mirrordash_core.features.modules.service import github_repo, github_screenshot
    assert github_repo("https://github.com/o/mirrordash-x.git") == "https://github.com/o/mirrordash-x"
    assert github_repo("https://github.com/o/mirrordash-x/") == "https://github.com/o/mirrordash-x"
    assert github_repo("file:///home/me/mirrordash-x") is None  # a local install has no screenshot
    assert github_screenshot(None) is None


def test_config_migration():
    from mirrordash_core.config import migrate_config
    cfg = {
        "modules": {
            "mirrordash-clock": {
                "enabled": True,
                "position": "top_left"
            }
        }
    }
    assert migrate_config(cfg) is True
    assert cfg["modules"]["mirrordash-clock"]["module"] == "mirrordash-clock"
    
    # Running it again should not make changes
    assert migrate_config(cfg) is False


@patch("mirrordash_core.features.modules.routes.load_config")
@patch("mirrordash_core.features.modules.routes.save_config")
@patch("mirrordash_core.features.modules.routes.module_loader.reload_modules", new_callable=AsyncMock)
def test_save_module_instance_config(mock_reload, mock_save, mock_load, client):
    headers = {"X-API-Key": "secret"}
    mock_load.return_value = {
        "globals": {},
        "modules": {}
    }
    
    # Form data prefixing the instance key modules[clock-one]
    payload = {
        "modules[clock-one][enabled]": "true",
        "modules[clock-one][position]": "top_right",
        "modules[clock-one][show_seconds]": "true"
    }
    
    # Post with instance_id query parameter
    r = client.post("/admin/panels/modules/config/mirrordash-clock/save?instance_id=clock-one", data=payload, headers=headers)
    assert r.status_code == 200
    
    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert "clock-one" in saved["modules"]
    assert saved["modules"]["clock-one"]["module"] == "mirrordash-clock"
    assert saved["modules"]["clock-one"]["position"] == "top_right"
    assert saved["modules"]["clock-one"]["enabled"] is True
    assert saved["modules"]["clock-one"]["show_seconds"] is True


@patch("mirrordash_core.features.modules.routes.load_config")
@patch("mirrordash_core.features.modules.routes.save_config")
@patch("mirrordash_core.features.modules.routes.module_loader.reload_modules", new_callable=AsyncMock)
def test_remove_module_instance_config(mock_reload, mock_save, mock_load, client):
    headers = {"X-API-Key": "secret"}
    mock_load.return_value = {
        "globals": {},
        "modules": {
            "clock-one": {"module": "mirrordash-clock", "position": "top_right"},
            "clock-two": {"module": "mirrordash-clock", "position": "bottom_left"}
        }
    }
    
    # Post to remove clock-one
    r = client.post("/admin/panels/modules/config/mirrordash-clock/remove?instance_id=clock-one", headers=headers)
    assert r.status_code == 200
    
    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert "clock-one" not in saved["modules"]
    assert "clock-two" in saved["modules"]


@patch("mirrordash_core.features.modules.routes.load_config")
@patch("mirrordash_core.features.modules.routes.save_config")
@patch("mirrordash_core.features.modules.routes.module_loader.reload_modules", new_callable=AsyncMock)
def test_toggle_module_instance_config(mock_reload, mock_save, mock_load, client):
    headers = {"X-API-Key": "secret"}
    mock_load.return_value = {
        "globals": {},
        "modules": {
            "clock-one": {"module": "mirrordash-clock", "position": "top_right", "enabled": True},
            "clock-two": {"module": "mirrordash-clock", "position": "bottom_left", "enabled": False}
        }
    }

    # Post to toggle clock-one (True -> False)
    r = client.post("/admin/panels/modules/config/mirrordash-clock/toggle?instance_id=clock-one", headers=headers)
    assert r.status_code == 200
    assert "disabled" in r.headers["HX-Trigger-After-Swap"]

    # Post to toggle clock-two (False -> True)
    r2 = client.post("/admin/panels/modules/config/mirrordash-clock/toggle?instance_id=clock-two", headers=headers)
    assert r2.status_code == 200
    assert "enabled" in r2.headers["HX-Trigger-After-Swap"]

    assert mock_save.call_count == 2

    # First save should have disabled clock-one
    saved_first = mock_save.call_args_list[0][0][0]
    assert saved_first["modules"]["clock-one"]["enabled"] is False

    # Second save should have enabled clock-two
    saved_second = mock_save.call_args_list[1][0][0]
    assert saved_second["modules"]["clock-two"]["enabled"] is True


@patch("mirrordash_core.features.dashboard.telemetry.get_undervoltage_detected", new_callable=AsyncMock)
@patch("mirrordash_core.features.dashboard.telemetry.get_wifi_info", new_callable=AsyncMock)
@patch("mirrordash_core.features.dashboard.telemetry.get_ntp_status", new_callable=AsyncMock)
@patch("mirrordash_core.features.dashboard.telemetry.get_ram_usage")
@patch("mirrordash_core.features.dashboard.telemetry.get_uptime_string")
@patch("mirrordash_core.features.updates.service.get_disk_usage")
@patch("mirrordash_core.features.dashboard.routes.module_loader")
def test_get_panel_dashboard(
    mock_loader, mock_disk_usage, mock_uptime, mock_ram, mock_ntp, mock_wifi, mock_under, client
):
    headers = {"X-API-Key": "secret"}
    mock_disk_usage.return_value = {
        "total_bytes": 10000000000,
        "used_bytes": 3000000000,
        "free_bytes": 7000000000,
        "percent_used": 30.0
    }
    
    mock_uptime.return_value = "5d 12h 30m"
    mock_ram.return_value = {
        "total_mb": 4096,
        "used_mb": 2048,
        "percent_used": 50.0
    }
    mock_ntp.return_value = True
    mock_wifi.return_value = {"ssid": "Home-WiFi", "signal": 85, "type": "wifi"}
    mock_under.return_value = True
    
    mock_inst = MagicMock()
    mock_inst.config = {"module": "mirrordash-clock", "position": "top_left"}
    mock_loader.instances = {"clock-one": mock_inst}
    mock_loader.tasks = {"clock-one": MagicMock()}

    cfg = {"modules": {"mirrordash-clock": {"position": "top_left"},
                       "mirrordash-clock-2": {"module": "mirrordash-clock", "position": "bottom_right", "enabled": False},
                       "mirrordash_news": {"position": "top_left"}}}
    clock = SimpleNamespace(name="mirrordash_clock", dist=None)
    with patch("mirrordash_core.features.dashboard.routes.load_config", return_value=cfg), \
         patch("mirrordash_core.features.modules.service.load_config", return_value=cfg), \
         patch("mirrordash_core.features.modules.service.find_entry_point",
               side_effect=lambda name, eps: clock if "clock" in name else None), \
         patch("mirrordash_core.features.dashboard.telemetry.get_cpu_load", return_value=42.5):
        response = client.get("/admin/panels/dashboard", headers=headers)
    assert response.status_code == 200
    assert "On the Screen" in response.text
    assert "System Analytics" in response.text
    # Each instance is named in its screen position (the -2 instance of the clock too); the turned-off
    # one is dimmed, and one in config whose module is not installed is dimmed and says so
    assert response.text.count('class="mirror-tile"') == 1
    assert response.text.count('class="mirror-tile off"') == 1
    assert response.text.count('class="mirror-tile missing"') == 1
    assert "/toggle" not in response.text and "/remove" not in response.text  # changes are on the Modules tab
    assert "Clock 2" in response.text
    assert response.text.count("Not installed") == 1
    assert "42.5% Load" in response.text
    assert "Memory (RAM)" in response.text
    assert "50.0% Used" in response.text
    assert "Home-WiFi" in response.text
    assert "5d 12h 30m" in response.text
    assert "In sync" in response.text
    assert "Power Warning: Under-voltage Detected" in response.text


def test_cpu_load_is_a_percentage_of_all_cores():
    from mirrordash_core.features.dashboard.telemetry import get_cpu_load
    with patch("os.getloadavg", return_value=(2.0, 0, 0)), patch("os.cpu_count", return_value=4):
        assert get_cpu_load() == 50.0
    with patch("os.getloadavg", return_value=(9.0, 0, 0)), patch("os.cpu_count", return_value=4):
        assert get_cpu_load() == 100.0
    with patch("os.getloadavg", side_effect=OSError):
        assert get_cpu_load() is None


@patch("mirrordash_core.features.dashboard.telemetry.check_all_updates")
def test_get_dashboard_updates_available(mock_check, client):
    headers = {"X-API-Key": "secret"}
    mock_check.return_value = {
        "core": {"update_available": True, "latest_version": "0.3.5", "current_version": "0.3.4"},
    }
    response = client.get("/admin/panels/dashboard/updates", headers=headers)
    assert response.status_code == 200
    assert "Update Available: MirrorDash Core" in response.text
    assert "v0.3.5" in response.text
    assert "page-tab-config" in response.text and "Go to Settings" in response.text  # core updates live in Settings


@patch("mirrordash_core.features.dashboard.telemetry.check_all_updates")
def test_get_dashboard_updates_none(mock_check, client):
    headers = {"X-API-Key": "secret"}
    mock_check.return_value = {
        "core": {"update_available": False, "latest_version": "0.3.4", "current_version": "0.3.4"},
    }
    response = client.get("/admin/panels/dashboard/updates", headers=headers)
    assert response.status_code == 200
    assert response.text.strip() == ""








def test_logs_viewer_escapes_html(client):
    """Log lines can contain anything (request paths, module output); they must never become markup."""
    with patch("mirrordash_core.features.logs.routes.get_logs", new_callable=AsyncMock,
               return_value={"logs": 'GET /<img src=x onerror=alert(1)> 404'}):
        response = client.get("/admin/panels/logs/viewer", headers={"X-API-Key": "secret"})
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in response.text


def test_module_settings_are_checked_against_the_module_schema():
    from mirrordash_core.features.settings import schema
    props = {"properties": {"count": {"type": "integer", "title": "Count"}, "on": {"type": "boolean"},
                            "unit": {"type": "string", "enum": ["C", "F"]}}}
    with patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_x": props}):
        schema.validate_config({"modules": {"x1": {"module": "mirrordash-x", "count": 3, "on": True, "unit": "C"}}})
        for bad, message in [({"count": True}, "'Count' must be an integer"), ({"on": "yes"}, "must be a boolean"),
                             ({"unit": "K"}, "must be one of")]:
            with pytest.raises(ValueError, match=message):
                schema.validate_config({"modules": {"x1": {"module": "mirrordash-x", **bad}}})


def test_module_config_drawer_shows_standard_then_own_settings(client):
    class Clock:
        config_schema = {"properties": {"format": {"type": "string", "enum": ["24h", "12h"], "title": "Format"},
                                        "position": {"type": "string"}}}  # a standard field it shouldn't declare
    ep = MagicMock()
    ep.load.return_value = Clock
    cfg = {"modules": {"mirrordash-clock": {"module": "mirrordash-clock", "position": "top_left"}}}
    with patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=ep), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value=cfg):
        html = client.get("/admin/panels/modules/config/mirrordash-clock?instance_id=mirrordash-clock",
                          headers={"X-API-Key": "secret"}).text
        new = client.get("/admin/panels/modules/config/mirrordash-clock", headers={"X-API-Key": "secret"}).text
    assert html.count("Screen Position") == 1 and "Module Settings" in html
    assert '<option value="top_left" selected>' in html
    assert "instance_id=mirrordash-clock-2" in new  # a second instance gets its own id
    assert "Save Configuration" in html and "Add to Mirror" not in html


def test_new_instance_gets_its_own_id_when_key_and_entry_point_are_spelled_differently(client):
    # As in a real config: the key is mirrordash-clock without a module field, the entry point mirrordash_clock
    ep = MagicMock()
    ep.load.return_value = type("Clock", (), {"config_schema": {"properties": {}}})
    cfg = {"modules": {"mirrordash-clock": {"position": "top_left"}}}
    with patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=ep), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value=cfg):
        new = client.get("/admin/panels/modules/config/mirrordash_clock", headers={"X-API-Key": "secret"}).text
    assert "save?instance_id=mirrordash_clock-2" in new
    assert "Add to Mirror" in new and "/remove" not in new  # nothing to remove before it's added


def test_saved_logs_can_be_seen_downloaded_and_deleted(client, tmp_path, monkeypatch):
    """Logs → Saved Logs: what failed updates left behind; download and delete only reach that folder."""
    from mirrordash_core.features.logs import saved
    monkeypatch.setattr(saved, "SAVED_LOG_DIR", tmp_path)
    (tmp_path / "os-trial-20261018-1200.log").write_text("trial boot log")
    key = {"X-API-Key": "secret"}
    with patch("mirrordash_core.features.logs.routes.get_logs", new_callable=AsyncMock, return_value={"logs": ""}):
        page = client.get("/admin/panels/logs", headers=key).text
    assert "OS update: the new version didn&#39;t start properly" in page or "OS update: the new version didn't start properly" in page
    assert "Delete All" in page
    assert client.get("/admin/logs/saved/os-trial-20261018-1200.log", headers=key).text == "trial boot log"
    assert client.get("/admin/logs/saved/..%2Fsecret.log", headers=key).status_code == 404
    assert client.post("/admin/panels/logs/saved/delete/os-trial-20261018-1200.log", headers=key).status_code == 200
    assert not list(tmp_path.iterdir())


def test_the_developer_logging_switch(client, tmp_path, monkeypatch):
    import time

    from mirrordash_core.features.settings import dev_logging
    monkeypatch.setattr(dev_logging, "FLAG", tmp_path / "dev-logging-until")
    monkeypatch.setattr(dev_logging, "JOURNAL_DIR", tmp_path / "journal")
    monkeypatch.setattr(dev_logging, "dev_logging_available", lambda: True)
    monkeypatch.setattr("mirrordash_core.features.settings.routes.dev_logging_available", lambda: True)
    key = {"X-API-Key": "secret"}

    def fake_run(action):
        if action == "on":
            (tmp_path / "dev-logging-until").write_text(str(int(time.time()) + 7 * 86400))
        else:
            (tmp_path / "dev-logging-until").unlink(missing_ok=True)

    with patch.object(dev_logging, "_run", AsyncMock(side_effect=fake_run)) as run:
        page = client.post("/admin/panels/system/dev-logging", data={"on": ["false", "true"]}, headers=key)
        assert run.call_args.args == ("on",) and "On until" in page.text
        (tmp_path / "journal").mkdir()
        (tmp_path / "journal" / "system.journal").write_bytes(b"x" * 10)
        page = client.post("/admin/panels/system/dev-logging", data={"on": "false"}, headers=key)
        assert run.call_args.args == ("off",) and "Delete saved system log (1 MB)" in page.text


def test_the_usb_switch(client):
    """Shown only where it works; off goes through sudo, and a refusal (Ethernet) comes back as the message."""
    from fastapi import HTTPException

    from mirrordash_core.features.power import usb
    key ={"X-API-Key": "secret"}
    with patch.object(usb, "usb_status", AsyncMock(return_value=None)):
        assert 'id="usb"' not in client.get("/admin/panels/power", headers=key).text
    with patch("mirrordash_core.features.power.routes.usb_status", AsyncMock(return_value="on")):
        assert 'id="sys-usb" name="on" value="true" checked' in client.get("/admin/panels/power", headers=key).text
    with patch.object(usb, "_run", AsyncMock(return_value="")) as run, \
         patch("mirrordash_core.features.power.routes.usb_status", AsyncMock(return_value="off")):
        page = client.post("/admin/panels/power/usb", data={"on": "false"}, headers=key)
        assert run.call_args.args == ("sudo", "-n", usb.HELPER, "off") and 'id="usb"' in page.text and "checked" not in page.text
    with patch.object(usb, "_run", AsyncMock(side_effect=HTTPException(400, "The mirror is connected by Ethernet"))):
        page = client.post("/admin/panels/power/usb", data={"on": "false"}, headers=key)
        assert page.status_code == 400 and "Ethernet" in page.json()["detail"]


def test_update_check_shows_mirrordash_and_the_system_apart(client):
    """One check, two sections; with an update for both, the system goes first and MirrorDash's button waits."""
    app = {"current_version": "0.6.2", "latest_version": "0.6.3", "update_available": True}
    system = {"supported": True, "installed_build": "20261001-0800", "failed_build": None, "update_available": True,
              "bundle": {"tag": "v0.6.3-os1", "build": "20261018-2237", "url": "https://example.com/b.raucb", "size": 880_000_000}}
    key = {"X-API-Key": "secret"}
    with patch("mirrordash_core.features.updates.routes.check_core_update", AsyncMock(return_value=app)), \
         patch("mirrordash_core.features.updates.routes.check_os_update", AsyncMock(return_value=system)):
        page = client.get("/admin/panels/system/update-check", headers=key).text
    core, oob = page.split('<div id="os-update-result" hx-swap-oob="innerHTML">')
    assert "Install the system update first" in core and "update-trigger" not in core
    assert "Install System Update" in oob and "2026-10-18 22:37 UTC" in oob

    with patch("mirrordash_core.features.updates.routes.check_core_update", AsyncMock(return_value=app)), \
         patch("mirrordash_core.features.updates.routes.check_os_update",
               AsyncMock(return_value={**system, "update_available": False, "bundle": None})):
        page = client.get("/admin/panels/system/update-check", headers=key).text
    assert "Install MirrorDash v0.6.3" in page and "The system is up-to-date (build 2026-10-01 08:00 UTC)" in page

    with patch("mirrordash_core.features.updates.routes.check_core_update", AsyncMock(return_value=app)), \
         patch("mirrordash_core.features.updates.routes.check_os_update", AsyncMock(return_value={"supported": False})):
        page = client.get("/admin/panels/system/update-check", headers=key).text
    assert "os-update-result" not in page  # no system section on mirrors without system updates



@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.features.updates.service.get_core_version", return_value="0.7.0")
@patch("mirrordash_core.venv.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.commit_venv_next", new_callable=AsyncMock)
def test_rebuild_in_safe_mode_reinstalls_the_modules_safe_mode_lacks(mock_commit, mock_prepare, mock_version,
                                                                     mock_create_task, mock_restart, tmp_path, monkeypatch):
    """Safe mode runs base_venv, which has no modules: the button reads them (and the MirrorDash
    version) from the venv that didn't start, instead of rebuilding an empty one over it."""
    import os

    from test_venv import fake_venv

    from mirrordash_core.features.backup import service as backup
    real = backup.installed_modules
    fake_venv(tmp_path / "venv_b")
    os.symlink("venv_b", tmp_path / "venv")
    mock_prepare.return_value = (tmp_path / "venv_b", tmp_path / "venv_a")
    monkeypatch.setenv("MIRRORDASH_BOOT_STATUS", "safe_mode")
    installed = []

    async def fake_uv_pip(python, command, *args):
        installed.append(args[-1])
        return 0, "", ""

    with patch("mirrordash_core.features.updates.service.get_venv_paths",
               return_value=(tmp_path / "venv", tmp_path / "venv_b", tmp_path / "venv_a")), \
         patch.object(backup, "installed_modules", side_effect=lambda venv_dir=None: [] if venv_dir is None else real(venv_dir)), \
         patch("mirrordash_core.features.updates.service.uv_pip", side_effect=fake_uv_pip):
        response = call(rebuild_venv)
    assert response.status_code == 200
    assert installed[0] == "mirrordash==0.9.0"
    assert sorted(installed[1:]) == ["git+https://github.com/someone/mirrordash-calendar@abc123", "mirrordash-news==1.2.0"]


def test_needed_python_reads_requires_python():
    import sys

    from mirrordash_core.features.updates.service import needed_python
    now = sys.version_info.minor
    assert needed_python(f">=3.{now}") is None and needed_python(None) is None and needed_python("") is None
    assert needed_python(f">=3.{now + 1}") == f"3.{now + 1}"
    assert needed_python(f">= 3.{now + 1}, <4") == f"3.{now + 1}"


@pytest.mark.parametrize("on_mirror", [True, False])
@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
def test_an_app_update_brings_the_newest_python_patch_on_a_mirror(mock_create_task, mock_restart, on_mirror, monkeypatch):
    from contextlib import asynccontextmanager

    from mirrordash_core.features.updates import service
    calls = []

    async def fake_run(*cmd, timeout=None):
        calls.append(cmd)
        return 0, "99.0.0", ""  # also the version the venv reports afterwards

    async def fake_uv_pip(python, command, *args, timeout=None):
        calls.append(("uv", "pip", command))
        return 0, "", ""

    @asynccontextmanager
    async def fake_swap(**kwargs):
        yield "/storage/mirrordash/venv_b/bin/python"

    if on_mirror:
        monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/storage/mirrordash/python")
    else:
        monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
    with patch.object(service, "check_core_update", AsyncMock(return_value={"update_available": True})), \
         patch.object(service, "run", side_effect=fake_run), patch.object(service, "uv_pip", side_effect=fake_uv_pip), \
         patch.object(service, "venv_swap", fake_swap):
        asyncio.run(service.update_core())
    upgrade = ("uv", "python", "upgrade", service.running_python())
    if on_mirror:
        assert calls.index(upgrade) < calls.index(("uv", "pip", "install"))
    else:
        assert upgrade not in calls


@pytest.mark.parametrize("case", ["all work", "one doesn't", "install anyway"])
@patch("mirrordash_core.features.updates.service.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.features.updates.service.asyncio.create_task", side_effect=lambda coro: coro.close())
@patch("mirrordash_core.venv.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.commit_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.venv.revert_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.features.backup.service.installed_modules", return_value=REBUILD_MODULES)
def test_an_update_to_a_newer_python_tries_every_module_first(mock_modules, mock_revert, mock_commit, mock_prepare,
                                                              mock_create_task, mock_restart, case):
    """A MirrorDash that needs Python 3.15: installed next to 3.14, a new venv gets it and every module,
    each module is loaded once; only when all work (or the admin turns the others off) is it switched in."""
    from pathlib import Path

    from mirrordash_core.features.updates import service
    mock_prepare.return_value = (Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))
    calls = []
    config = {"modules": {"news-1": {"module": "mirrordash-news", "enabled": True, "feed": "svt"}}}

    async def fake_run(*cmd, timeout=None):
        calls.append(cmd)
        if case == "one doesn't" and cmd[-1] == "mirrordash_news":  # installs, but import fails
            return 1, "", "Traceback ...\nModuleNotFoundError: No module named 'cgi'"
        return 0, "", ""

    async def fake_uv_pip(python, command, *args, timeout=None):
        return await fake_run("uv", "pip", command, *args)

    check = {"update_available": True, "needs_python": "3.15", "latest_version": "1.0.0"}
    skip = ("mirrordash_news",) if case == "install anyway" else ()
    service.incompatible.clear()
    with patch.object(service, "check_core_update", AsyncMock(return_value=check)), \
         patch.object(service, "run", side_effect=fake_run), patch.object(service, "uv_pip", side_effect=fake_uv_pip), \
         patch.object(service, "load_config", return_value=config), patch("mirrordash_core.config.save_config") as save:
        response = call(lambda: service.update_core(skip))
    assert calls[0] == ("uv", "python", "install", "3.15")
    mock_prepare.assert_awaited_once_with(force_clean=True, python="3.15")
    assert ("uv", "pip", "install", "mirrordash==1.0.0") in calls
    if case == "one doesn't":
        assert response.status_code == 409 and "mirrordash_news" in response.json()["detail"]
        assert service.incompatible == {"version": "1.0.0", "python": "3.15",
                                        "modules": {"mirrordash_news": "ModuleNotFoundError: No module named 'cgi'"}}
        mock_revert.assert_awaited_once() and mock_commit.assert_not_awaited()
        save.assert_not_called()
    else:
        assert response.status_code == 200
        mock_commit.assert_awaited_once()
        installed = [c[3] for c in calls if c[:3] == ("uv", "pip", "install")]
        assert ("mirrordash-news==1.2.0" in installed) is (case == "all work")
        if case == "install anyway":  # turned off, its settings kept
            assert config["modules"]["news-1"] == {"module": "mirrordash-news", "enabled": False, "feed": "svt",
                                                   "disabled_reason": "doesn't work with Python 3.15"}
            save.assert_called_once()


def test_the_update_card_offers_install_anyway_after_modules_stopped_it():
    from mirrordash_core.features.updates import routes, service
    data = {"current_version": "0.9.0", "latest_version": "1.0.0", "update_available": True, "needs_python": "3.15"}
    service.incompatible.clear()
    assert "checks that every module works" in routes._app_update_html(data, False)
    service.incompatible.update(version="1.0.0", python="3.15", modules={"mirrordash_news": "No module named 'cgi'<script>"})
    try:
        card = routes._app_update_html(data, False)
        # The reason is a module's own error text: escaped, never markup
        assert "mirrordash_news" in card and "No module named &#39;cgi&#39;&lt;script&gt;" in card and "<script>" not in card
        assert 'hx-post="/admin/panels/system/update-trigger?skip=mirrordash_news"' in card and "Install Anyway" in card
        with patch.object(routes, "start_job", return_value=None) as start, \
             patch.object(routes, "update_core", AsyncMock()) as update:
            asyncio.run(routes.trigger_system_update(skip="mirrordash_news"))
            asyncio.run(start.call_args.args[0]())
        update.assert_awaited_once_with(("mirrordash_news",))
    finally:
        service.incompatible.clear()


def test_a_packages_own_text_is_never_markup_in_the_admin_page():
    """Release notes, summary and version come from PyPI or a GitHub release, i.e. from anyone who
    publishes a mirrordash-* package: they must not be able to run script in the admin page."""
    from mirrordash_core.features.modules import routes
    evil = '</textarea><script>alert(1)</script>"\''
    ep = SimpleNamespace(name="mirrordash_x", dist=SimpleNamespace(name="mirrordash-x", version="0.1.0",
                                                                   read_text=lambda name: None))
    pypi = {"info": {"summary": evil, "description": evil, "version": "9.9.9"}}
    with patch.object(routes, "find_entry_point", return_value=ep), \
         patch.object(routes, "fetch_json_cached", AsyncMock(return_value=pypi)):
        notes = asyncio.run(routes.get_module_notes("mirrordash_x")).body.decode()
        badge = asyncio.run(routes.check_module_update_route("mirrordash_x")).body.decode()
    for page in (notes, badge):
        assert "<script>" not in page and "</textarea><" not in page
    assert notes.count("&lt;script&gt;") == 2  # the title (summary) and the notes themselves
    assert "renderNotesMarkdown" not in notes  # rendered by admin.js after the swap, not by a <script> (rule 8c)
    assert 'hx-vals=\'{"package_name": "mirrordash-x"}\'' in badge and "Notes" in badge


@pytest.mark.parametrize("case", ["off", "busy", "system first", "skipped build", "mirrordash", "waiting", "up to date"])
def test_the_nightly_update_does_what_the_buttons_would(case):
    """Off → nothing; the system before MirrorDash; a system build whose trial failed isn't retried
    by itself; a newer Python some modules don't work with waits for the owner's choice."""
    from mirrordash_core.features.updates import service
    system = {"auto_update": case != "off"}
    if case == "skipped build":
        system["auto_update_skip_build"] = "20261020-0300"
    os_info = {"update_available": case in ("system first", "skipped build"), "failed_build": None,
               "bundle": {"build": "20261020-0300"}}
    app = {"update_available": case in ("skipped build", "mirrordash", "waiting"), "latest_version": "1.0.0",
           "needs_python": "3.15" if case == "waiting" else None}
    started = []
    service.incompatible.clear()
    if case == "waiting":
        service.incompatible.update(version="1.0.0", python="3.15", modules={"x": "y"})
    with patch.object(service, "load_config", return_value={"system": system}), \
         patch.object(service, "check_os_update", AsyncMock(return_value=os_info)), \
         patch.object(service, "check_core_update", AsyncMock(return_value=app)), \
         patch("mirrordash_core.admin.job_status", return_value={"state": "running" if case == "busy" else "done"}), \
         patch("mirrordash_core.admin.start_job", side_effect=lambda work: started.append(work)):
        result = asyncio.run(service.auto_update())
    service.incompatible.clear()
    assert result == {"off": "off", "busy": "busy", "system first": "system", "skipped build": "mirrordash",
                      "mirrordash": "mirrordash", "waiting": "waiting for a choice", "up to date": "up to date"}[case]
    assert len(started) == (1 if result in ("system", "mirrordash") else 0)


def test_a_failed_system_build_is_remembered_for_the_nights_to_come():
    from mirrordash_core.features.updates import service
    config = {"system": {}}
    os_info = {"update_available": True, "failed_build": "20261020-0300", "bundle": {"build": "20261020-0300"}}
    with patch.object(service, "load_config", return_value=config), \
         patch("mirrordash_core.config.save_config") as save, \
         patch.object(service, "check_os_update", AsyncMock(return_value=os_info)), \
         patch.object(service, "check_core_update", AsyncMock(return_value={"update_available": False})), \
         patch("mirrordash_core.admin.job_status", return_value={"state": "done"}), \
         patch("mirrordash_core.admin.start_job") as start:
        assert asyncio.run(service.auto_update()) == "up to date"
    assert config["system"]["auto_update_skip_build"] == "20261020-0300"
    save.assert_called_once() and start.assert_not_called()


def test_only_the_mirror_itself_can_start_the_nightly_update(client):
    with patch("mirrordash_core.features.updates.routes.auto_update", AsyncMock(return_value="up to date")) as run:
        assert client.post("/admin/auto-update").status_code == 403  # TestClient's address is "testclient"
        with patch("starlette.requests.Request.client", new=type("C", (), {"host": "127.0.0.1"})()):
            assert client.post("/admin/auto-update").json() == {"started": "up to date"}
    run.assert_awaited_once()


def test_reset_mirror_needs_reset_typed():
    from mirrordash_core.features.settings import routes
    with patch.object(routes, "start_job", return_value="job1") as start, \
         patch.object(routes, "job_response", return_value="following") as follow:
        assert "Type RESET" in asyncio.run(routes.factory_reset(confirm="reset me")).headers["HX-Trigger-After-Swap"]
        start.assert_not_called()
        assert asyncio.run(routes.factory_reset(confirm=" RESET ")) == "following"
    start.assert_called_once() and follow.assert_called_once()


@patch("mirrordash_core.features.auth.routes.load_config", return_value=MOCK_CONFIG)
@patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG)
def test_a_login_is_a_session_not_the_password_in_every_request(mock_admin_load, mock_auth_load, client):
    """The password is sent once; the session cookie (HttpOnly, SameSite=Strict) does the rest, survives
    a restart, ends at logout, and can't be used from another site."""
    from mirrordash_core import admin
    assert client.get("/admin/jobs/current").status_code == 401
    r = client.post("/admin/auth/login", json={"password": "secret"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "mirrordash_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.get("/admin/jobs/current").status_code == 200  # no X-API-Key: the cookie
    assert client.get("/admin/auth/status").json()["logged_in"] is True
    admin._sessions = None  # a restart: read back from sessions.json
    assert client.get("/admin/jobs/current").status_code == 200
    # Another site can't use the browser's session to change something
    assert client.post("/admin/restart", headers={"Origin": "http://evil.example"}).status_code == 403
    client.post("/admin/auth/logout")
    assert client.get("/admin/jobs/current").status_code == 401
    assert client.get("/admin/jobs/current", headers={"X-API-Key": "secret"}).status_code == 200  # scripts, as before


@patch("mirrordash_core.features.auth.routes.load_config", return_value=MOCK_CONFIG)
@patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG)
def test_guessing_the_password_is_stopped_after_five_tries(mock_admin_load, mock_auth_load, client):
    for _ in range(5):
        assert client.post("/admin/auth/login", json={"password": "guess"}).status_code == 401
    r = client.post("/admin/auth/login", json={"password": "secret"})  # even the right one, for a minute
    assert r.status_code == 429 and "Try again in" in r.json()["detail"]
    assert client.get("/admin/jobs/current", headers={"X-API-Key": "secret"}).status_code == 429


def test_a_new_password_logs_every_other_browser_out():
    from mirrordash_core import admin
    other, mine = admin.new_session(), admin.new_session()
    assert admin.session_valid(other) and admin.session_valid(mine)
    admin.end_all_sessions()
    assert not admin.session_valid(other) and not admin.session_valid(mine)
