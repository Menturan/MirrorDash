import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch, AsyncMock

from mirrordash_core.app import app
from mirrordash_core.api.admin import hash_password

mock_salt = "0123456789abcdef"
mock_hash = hash_password("secret", mock_salt)

MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    }
}

@pytest.fixture
def client():
    return TestClient(app)

@patch("mirrordash_core.api.admin.load_config")
@patch("mirrordash_core.api.admin.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin.remount_ro", new_callable=AsyncMock)
@patch("asyncio.create_subprocess_exec")
@patch("importlib.metadata.version")
def test_failsafe_update_success(mock_version, mock_exec, mock_ro, mock_rw, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    mock_version.return_value = "0.2.0"
    
    # Upgrade process mock
    mock_proc_upgrade = MagicMock()
    mock_proc_upgrade.returncode = 0
    mock_proc_upgrade.communicate = AsyncMock(return_value=(b"Successfully upgraded", b""))
    
    # Check process mock
    mock_proc_check = MagicMock()
    mock_proc_check.returncode = 0
    mock_proc_check.communicate = AsyncMock(return_value=(b"Check passed", b""))
    
    mock_exec.side_effect = [mock_proc_upgrade, mock_proc_check]
    
    headers = {"X-API-Key": "secret"}
    # Call the endpoint
    response = client.post("/admin/update", json={"package_name": "mirrordash"}, headers=headers)
    
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify both commands were run
    assert mock_exec.call_count == 2

@patch("mirrordash_core.api.admin.load_config")
@patch("mirrordash_core.api.admin.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin.remount_ro", new_callable=AsyncMock)
@patch("asyncio.create_subprocess_exec")
@patch("importlib.metadata.version")
def test_failsafe_update_rollback(mock_version, mock_exec, mock_ro, mock_rw, mock_load, client):
    mock_load.return_value = MOCK_CONFIG
    mock_version.return_value = "0.2.0"
    
    # 1. Upgrade process mock (succeeds)
    mock_proc_upgrade = MagicMock()
    mock_proc_upgrade.returncode = 0
    mock_proc_upgrade.communicate = AsyncMock(return_value=(b"Successfully upgraded", b""))
    
    # 2. Check process mock (fails)
    mock_proc_check = MagicMock()
    mock_proc_check.returncode = 1
    mock_proc_check.communicate = AsyncMock(return_value=(b"", b"ImportError: Broken module"))
    
    mock_exec.side_effect = [mock_proc_upgrade, mock_proc_check]
    
    headers = {"X-API-Key": "secret"}
    # Call the endpoint
    response = client.post("/admin/update", json={"package_name": "mirrordash"}, headers=headers)
    
    assert response.status_code == 500
    assert "Verification failed. Rolled back successfully. Error: ImportError: Broken module" in response.json()["detail"]
    
    # Verify both commands were run
    assert mock_exec.call_count == 2
