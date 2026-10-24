from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import call
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules.service import update_modules

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

@pytest.fixture(autouse=True)
def mock_admin_shared_load_config():
    with patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG):
        yield


@patch("mirrordash_core.features.modules.service.module_host.check_venv", new_callable=AsyncMock, return_value=(0, ""))
@patch("mirrordash_core.features.modules.service.load_config")
@patch("asyncio.create_subprocess_exec")
@patch("importlib.metadata.version")
def test_failsafe_update_success(mock_version, mock_exec, mock_load, mock_check, client):
    mock_load.return_value = MOCK_CONFIG
    mock_version.return_value = "0.2.1"
    
    # Upgrade process mock
    mock_proc_upgrade = MagicMock()
    mock_proc_upgrade.returncode = 0
    mock_proc_upgrade.communicate = AsyncMock(return_value=(b"Successfully upgraded", b""))
    
    # Check process mock
    mock_proc_check = MagicMock()
    mock_proc_check.returncode = 0
    mock_proc_check.communicate = AsyncMock(return_value=(b"Check passed", b""))
    
    # Clean process mock
    mock_proc_clean = MagicMock()
    mock_proc_clean.returncode = 0
    mock_proc_clean.communicate = AsyncMock(return_value=(b"", b""))
    
    mock_exec.side_effect = [mock_proc_upgrade, mock_proc_check, mock_proc_clean]
    
    # Call the endpoint
    response = call(update_modules, "mirrordash")
    
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify all three commands were run, and the modules were imported by the module host, not as the core
    assert mock_exec.call_count == 3
    assert mock_exec.call_args_list[1].args[-1] == "import mirrordash_core.app"
    mock_check.assert_awaited_once()

@patch("mirrordash_core.features.modules.service.module_host.check_venv", new_callable=AsyncMock,
       return_value=(1, "ImportError: Broken module"))
@patch("mirrordash_core.features.modules.service.load_config")
@patch("asyncio.create_subprocess_exec")
@patch("importlib.metadata.version")
def test_failsafe_update_rollback(mock_version, mock_exec, mock_load, mock_check, client):
    mock_load.return_value = MOCK_CONFIG
    mock_version.return_value = "0.2.1"
    
    # 1. Upgrade process mock (succeeds)
    mock_proc_upgrade = MagicMock()
    mock_proc_upgrade.returncode = 0
    mock_proc_upgrade.communicate = AsyncMock(return_value=(b"Successfully upgraded", b""))
    
    # 2. The core still imports; the module host's check of the modules fails
    mock_proc_check = MagicMock()
    mock_proc_check.returncode = 0
    mock_proc_check.communicate = AsyncMock(return_value=(b"", b""))
    
    mock_exec.side_effect = [mock_proc_upgrade, mock_proc_check]
    
    # Call the endpoint
    response = call(update_modules, "mirrordash")
    
    assert response.status_code == 500
    assert "Verification failed. Rolled back successfully. Error: ImportError: Broken module" in response.json()["detail"]
    
    # Verify both commands were run
    assert mock_exec.call_count == 2
