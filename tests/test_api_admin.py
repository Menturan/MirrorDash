import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock, ANY, mock_open

from mirrordash_core.app import app
from mirrordash_core.api.admin import hash_password

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
    with patch("mirrordash_core.api.admin_shared.load_config", return_value=MOCK_CONFIG):
        yield


@patch("mirrordash_core.api.admin_auth.load_config")
@patch("mirrordash_core.api.admin_auth.save_config")
@patch("mirrordash_core.api.admin_auth.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_auth.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_auth.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_auth_status_setup_required(mock_hotspot, mock_ro, mock_rw, mock_save, mock_load, client):
    # Setup not complete: "admin_auth" not in config
    mock_load.return_value = {}
    mock_hotspot.return_value = True
    
    response = client.get("/admin/auth/status")
    assert response.status_code == 200
    assert response.json() == {"setup_required": True, "wifi_hotspot_active": True}

@patch("mirrordash_core.api.admin_auth.load_config")
@patch("mirrordash_core.api.admin_auth.save_config")
@patch("mirrordash_core.api.admin_auth.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_auth.remount_ro", new_callable=AsyncMock)
def test_auth_setup_success(mock_ro, mock_rw, mock_save, mock_load, client):
    mock_load.return_value = {}
    
    response = client.post("/admin/auth/setup", json={"password": "my_new_password"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    mock_save.assert_called_once()
    saved_config = mock_save.call_args[0][0]
    assert "admin_auth" in saved_config
    assert "hash" in saved_config["admin_auth"]
    assert "salt" in saved_config["admin_auth"]

@patch("mirrordash_core.api.admin_auth.load_config")
@patch("mirrordash_core.api.admin_auth.save_config")
@patch("mirrordash_core.api.admin_auth.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_auth.remount_ro", new_callable=AsyncMock)
def test_auth_setup_failures(mock_ro, mock_rw, mock_save, mock_load, client):
    # Password too short
    response = client.post("/admin/auth/setup", json={"password": "123"})
    assert response.status_code == 400
    assert "must be at least 4 characters" in response.json()["detail"]

    # Password already set
    mock_load.return_value = {"admin_auth": {"hash": "abc", "salt": "123"}}
    response = client.post("/admin/auth/setup", json={"password": "validpassword"})
    assert response.status_code == 400
    assert "is already set" in response.json()["detail"]

@patch("mirrordash_core.api.admin_shared.load_config")
@patch("mirrordash_core.api.admin_system.load_config")
def test_auth_headers_required(mock_sys_load, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    
    # Missing header -> 401
    response = client.get("/admin/system")
    assert response.status_code == 401
    
    # Invalid password -> 401
    response = client.get("/admin/system", headers={"X-API-Key": "wrongpassword"})
    assert response.status_code == 401
    
    # Correct password -> 200
    with patch("mirrordash_core.api.admin_system.get_available_resolutions", new_callable=AsyncMock) as mock_res:
        mock_res.return_value = ["1920x1080"]
        response = client.get("/admin/system", headers={"X-API-Key": "secret"})
        assert response.status_code == 200

@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.system.set_ssh_status", new_callable=AsyncMock)
def test_update_system_settings_success(mock_set_ssh, mock_ro, mock_rw, mock_apply, mock_save, mock_load, client):
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

@patch("mirrordash_core.api.admin_system.load_config")
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

@patch("mirrordash_core.display_power.display_power_manager.set_state", new_callable=AsyncMock)
def test_update_screen_state(mock_power, client):
    # This route is unauthenticated
    
    # Valid state
    r = client.post("/admin/screen", json={"state": "on"})
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    mock_power.assert_called_once_with(True)

    # Valid state off
    mock_power.reset_mock()
    r = client.post("/admin/screen", json={"state": "off"})
    assert r.status_code == 200
    mock_power.assert_called_once_with(False)

    # Invalid state value
    r = client.post("/admin/screen", json={"state": "maybe"})
    assert r.status_code == 400

@patch("mirrordash_core.app.module_loader")
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

@patch("mirrordash_core.api.admin_config.load_config")
@patch("mirrordash_core.api.admin_config.save_config")
@patch("mirrordash_core.api.admin_config.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_config.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_config.module_loader.reload_modules", new_callable=AsyncMock)
def test_update_config_positions_validation(mock_reload, mock_ro, mock_rw, mock_save, mock_load, client):
    headers = {"X-API-Key": "secret"}
    mock_load.return_value = MOCK_CONFIG
    
    # 1. Valid new positions should pass
    for pos in ["top_center", "middle_left", "middle_right", "bottom_center"]:
        payload = {
            "modules": {
                "mirrordash-clock": {
                    "enabled": True,
                    "position": pos
                }
            }
        }
        r = client.post("/admin/config", json=payload, headers=headers)
        assert r.status_code == 200
        
    # 2. Invalid position should fail with 422
    payload_invalid = {
        "modules": {
            "mirrordash-clock": {
                "enabled": True,
                "position": "middle_top"
            }
        }
    }
    r = client.post("/admin/config", json=payload_invalid, headers=headers)
    assert r.status_code == 422

@patch("mirrordash_core.api.admin_modules.load_config")
@patch("mirrordash_core.api.admin_modules.save_config")
@patch("mirrordash_core.api.admin_modules.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_modules.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_modules.asyncio.create_subprocess_exec")
def test_uninstall_module_success(mock_subproc, mock_ro, mock_rw, mock_save, mock_load, client):
    headers = {"X-API-Key": "secret"}
    config = MOCK_CONFIG.copy()
    config["modules"] = {"mirrordash-clock": {"enabled": True, "position": "top_left"}}
    mock_load.return_value = config
    
    # Mock subprocess return
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate.return_value = (b"output", b"")
    mock_subproc.return_value = mock_process
    
    response = client.post("/admin/uninstall", json={"package_name": "mirrordash-clock"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Assert that config was saved and clock module deleted
    mock_save.assert_called_once()
    saved_config = mock_save.call_args[0][0]
    assert "mirrordash-clock" not in saved_config["modules"]
    
    # Assert subprocess executed uninstall
    mock_subproc.assert_called_once_with(
        "uv", "pip", "uninstall", "-y", "mirrordash-clock",
        stdout=-1, stderr=-1, env=ANY
    )

@patch("mirrordash_core.api.admin_modules.load_config")
def test_uninstall_module_invalid_name(mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    response = client.post("/admin/uninstall", json={"package_name": "invalid; rm -rf /"}, headers=headers)
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


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.system.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_without_password_rejected(
    mock_get_ssh, mock_screen, mock_apply, mock_ro, mock_rw, mock_save, mock_load, client
):
    """Enabling SSH without providing a password must return 400."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True}

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.system.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_with_short_password_rejected(
    mock_get_ssh, mock_screen, mock_apply, mock_ro, mock_rw, mock_save, mock_load, client
):
    """Enabling SSH with a password shorter than 8 chars must return 400."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True, "pi_password": "short"}

    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "password" in response.json()["detail"].lower()


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec")
@patch("mirrordash_core.system.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.system.get_ssh_status", new_callable=AsyncMock)
def test_enable_ssh_with_valid_password_calls_chpasswd(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_ro, mock_rw, mock_save, mock_load, client
):
    """Enabling SSH with a valid password must call chpasswd and enable SSH."""
    mock_get_ssh.return_value = False
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    payload = {**SYSTEM_PAYLOAD_BASE, "ssh": True, "pi_password": "SecurePass1!"}

    # Mock subprocesses success (first chpasswd, then openssl)
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(side_effect=[(b"", b""), (b"hashed_pass\n", b"")])
    mock_subproc.return_value = mock_process

    with patch("builtins.open", mock_open()):
        response = client.post("/admin/system", json=payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    # Verify both chpasswd and openssl were called
    assert mock_subproc.call_count == 2
    mock_subproc.assert_any_call(
        "sudo", "chpasswd",
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
    mock_process.communicate.assert_any_call(input=b"pi:SecurePass1!\n")
    mock_process.communicate.assert_any_call(input=b"SecurePass1!")


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec")
@patch("mirrordash_core.system.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.system.get_ssh_status", new_callable=AsyncMock)
def test_disable_ssh_does_not_call_chpasswd(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_ro, mock_rw, mock_save, mock_load, client
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


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.set_screen_power", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec")
@patch("mirrordash_core.system.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.system.get_ssh_status", new_callable=AsyncMock)
def test_ssh_already_enabled_does_not_require_password(
    mock_get_ssh, mock_set_ssh, mock_subproc, mock_screen, mock_apply,
    mock_ro, mock_rw, mock_save, mock_load, client
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


@patch("mirrordash_core.api.admin_modules.load_config")
def test_list_community_modules(mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/community-modules", headers=headers)
    assert response.status_code == 200
    modules = response.json()
    assert isinstance(modules, list)
    assert len(modules) > 0
    assert any(m["name"] == "mirrordash-clock" for m in modules)


@patch("mirrordash_core.api.admin_config.load_config")
def test_get_globals_schema(mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/globals-schema", headers=headers)
    assert response.status_code == 200
    schema = response.json()
    assert isinstance(schema, dict)
    assert schema["title"] == "Global Settings"
    assert "properties" in schema
    assert "language" in schema["properties"]


# ---------------------------------------------------------------------------
# Core self-update tests
# ---------------------------------------------------------------------------

@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", side_effect=lambda pkg: "1.0.0" if "mirrordash" in pkg else (_ for _ in ()).throw(Exception("not found")))
def test_core_update_check_up_to_date(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns update_available=False when versions match."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.return_value = "1.0.0"  # PyPI returns same version

    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/core-update-check", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == "1.0.0"
    assert data["latest_version"] == "1.0.0"
    assert data["update_available"] is False


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", side_effect=lambda pkg: "1.0.0" if "mirrordash" in pkg else (_ for _ in ()).throw(Exception("not found")))
def test_core_update_check_update_available(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns update_available=True when PyPI has a newer version."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.return_value = "1.2.0"  # PyPI has a newer version

    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/core-update-check", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == "1.0.0"
    assert data["latest_version"] == "1.2.0"
    assert data["update_available"] is True


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.asyncio.to_thread", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", side_effect=lambda pkg: "1.0.0" if "mirrordash" in pkg else (_ for _ in ()).throw(Exception("not found")))
def test_core_update_check_pypi_error(mock_version, mock_to_thread, mock_load, client):
    """Core update check returns 502 when PyPI is unreachable."""
    mock_load.return_value = MOCK_CONFIG
    mock_to_thread.side_effect = RuntimeError("PyPI request failed: connection timeout")

    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/core-update-check", headers=headers)
    assert response.status_code == 502
    assert "PyPI request failed" in response.json()["detail"]


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_task")
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", return_value="1.0.0")
def test_core_update_success(mock_version, mock_create_task, mock_exec, mock_restart,
                              mock_ro, mock_rw, mock_load, client):
    """POST /admin/core-update succeeds, triggers restart, and returns success."""
    mock_load.return_value = MOCK_CONFIG

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_exec.return_value = mock_proc

    headers = {"X-API-Key": "secret"}
    response = client.post("/admin/core-update", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "Restarting" in data["message"]
    mock_rw.assert_awaited_once()
    mock_ro.assert_awaited_once()
    mock_create_task.assert_called_once()


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_task")
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", return_value="1.0.0")
def test_core_update_failure(mock_version, mock_create_task, mock_exec,
                              mock_ro, mock_rw, mock_load, client):
    """POST /admin/core-update returns 500 when uv pip install fails."""
    mock_load.return_value = MOCK_CONFIG

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"pip install failed"))
    mock_exec.return_value = mock_proc

    headers = {"X-API-Key": "secret"}
    response = client.post("/admin/core-update", headers=headers)
    assert response.status_code == 500
    assert "Upgrade failed" in response.json()["detail"]
    mock_create_task.assert_not_called()
    mock_ro.assert_awaited_once()  # remount_ro must still be called in finally block


@patch("mirrordash_core.api.admin_system.load_config")
@patch("shutil.disk_usage")
def test_disk_usage_auth_required(mock_disk_usage, mock_load, client):
    """GET /admin/disk-usage requires API key."""
    mock_load.return_value = MOCK_CONFIG
    response = client.get("/admin/disk-usage")
    assert response.status_code == 401


@patch("mirrordash_core.api.admin_system.load_config")
@patch("shutil.disk_usage")
def test_disk_usage_success(mock_disk_usage, mock_load, client):
    """GET /admin/disk-usage returns correct calculated metrics."""
    mock_load.return_value = MOCK_CONFIG
    mock_disk_usage.return_value = (10000000000, 3000000000, 7000000000)

    headers = {"X-API-Key": "secret"}
    response = client.get("/admin/disk-usage", headers=headers)
    assert response.status_code == 200

    data = response.json()
    assert data["total_bytes"] == 10000000000
    assert data["used_bytes"] == 3000000000
    assert data["free_bytes"] == 7000000000
    assert data["percent_used"] == 30.0
    mock_disk_usage.assert_called_once_with("/")


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.run_restart", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.asyncio.create_task")
@patch("mirrordash_core.api.admin_system.importlib.metadata.version", return_value="1.0.0")
@patch("mirrordash_core.api.admin_system.prepare_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.commit_venv_next", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.revert_venv_next", new_callable=AsyncMock)
def test_rebuild_venv_success(mock_revert, mock_commit, mock_prepare, mock_version,
                               mock_create_task, mock_exec, mock_restart,
                               mock_ro, mock_rw, mock_load, client):
    """POST /admin/rebuild-venv succeeds, rebuilds venv, and triggers restart."""
    mock_load.return_value = MOCK_CONFIG
    mock_prepare.return_value = ("/storage/mirrordash/venv_a", "/storage/mirrordash/venv_b")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_exec.return_value = mock_proc

    headers = {"X-API-Key": "secret"}
    response = client.post("/admin/rebuild-venv", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "rebuilt successfully" in data["message"]
    mock_prepare.assert_awaited_once_with(force_clean=True)
    mock_commit.assert_awaited_once_with("/storage/mirrordash/venv_a", "/storage/mirrordash/venv_b")
    mock_rw.assert_awaited_once()
    mock_ro.assert_awaited_once()
    mock_create_task.assert_called_once()


@patch("mirrordash_core.api.admin_system_panels.load_config")
@patch("mirrordash_core.api.admin_system.get_available_resolutions", new_callable=AsyncMock)
def test_get_panel_system(mock_res, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    mock_res.return_value = ["1920x1080"]
    headers = {"X-API-Key": "secret"}
    
    response = client.get("/admin/panels/system", headers=headers)
    assert response.status_code == 200
    assert "System Settings" in response.text
    assert "sys-rotation" in response.text


@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.system.set_ssh_status", new_callable=AsyncMock)
def test_save_system_settings_route_flat_conversion(mock_set_ssh, mock_ro, mock_rw, mock_apply, mock_save, mock_load, client):
    mock_load.return_value = MOCK_CONFIG.copy()
    headers = {"X-API-Key": "secret"}
    
    # Send flat data with start_h/start_m/end_h/end_m
    form_data = {
        "brightness": "70",
        "volume": "60",
        "rotation": "left",
        "resolution": "auto",
        "ssh": "false",
        "display_control[mode]": "interval",
        "display_control[interval][start_h]": "8",
        "display_control[interval][start_m]": "15",
        "display_control[interval][end_h]": "22",
        "display_control[interval][end_m]": "45"
    }
    
    response = client.post("/admin/panels/system/save", data=form_data, headers=headers)
    assert response.status_code == 200
    assert "System settings applied successfully" in response.text
    
    mock_save.assert_called_once()
    saved_cfg = mock_save.call_args[0][0]
    assert saved_cfg["system"]["brightness"] == 70
    assert saved_cfg["system"]["volume"] == 60
    assert saved_cfg["system"]["rotation"] == "left"
    assert saved_cfg["system"]["display_control"]["interval"]["start"] == "08:15"
    assert saved_cfg["system"]["display_control"]["interval"]["end"] == "22:45"



