import pytest
import os
import json
import zipfile
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.api.admin import hash_password

mock_salt = "0123456789abcdef"
mock_hash = hash_password("secret", mock_salt)
MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    },
    "globals": {"language": "en"},
    "modules": {}
}

@pytest.fixture(autouse=True)
def mock_load_save_config():
    with patch("mirrordash_core.api.admin.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.backup.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.backup.save_config") as mock_save:
        yield mock_save

@pytest.fixture
def mock_backup_dirs(tmp_path):
    backups_dir = tmp_path / "backups"
    data_dir = tmp_path / "data"
    backups_dir.mkdir()
    data_dir.mkdir()
    
    with patch("mirrordash_core.api.backup.BACKUPS_DIR", str(backups_dir)), \
         patch("mirrordash_core.api.backup.DATA_DIR", str(data_dir)):
        yield backups_dir, data_dir

@pytest.fixture
def client():
    return TestClient(app)

def test_backup_list_unauthorized(client):
    # Disable X-API-Key header to trigger unauthorized
    response = client.get("/admin/backup/list")
    assert response.status_code == 401

def test_backup_list_authorized_empty(mock_backup_dirs, client):
    response = client.get("/admin/backup/list", headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    assert response.json() == {"backups": []}

def test_backup_list_with_files(mock_backup_dirs, client):
    backups_dir, _ = mock_backup_dirs
    
    # Create a mock zip backup file
    backup_file = backups_dir / "test_backup.mirror"
    with zipfile.ZipFile(backup_file, "w") as zf:
        zf.writestr("backup_manifest.json", json.dumps({"encrypted": False}))
        
    response = client.get("/admin/backup/list", headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    backups = response.json()["backups"]
    assert len(backups) == 1
    assert backups[0]["filename"] == "test_backup.mirror"
    assert backups[0]["encrypted"] is False

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.asyncio.create_subprocess_exec")
def test_create_backup_success(mock_subproc, mock_ro, mock_rw, mock_backup_dirs, client):
    backups_dir, data_dir = mock_backup_dirs
    
    # Mock subprocess success
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"", b""))
    mock_subproc.return_value = mock_process
    
    response = client.post("/admin/backup/create", json={}, headers={"X-API-Key": "secret"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "mirrordash_backup_" in response.json()["filename"]

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.asyncio.create_subprocess_exec")
def test_create_backup_zip_failure(mock_subproc, mock_ro, mock_rw, mock_backup_dirs, client):
    # Mock subprocess failure
    mock_process = MagicMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(return_value=(b"", b"zip error"))
    mock_subproc.return_value = mock_process
    
    response = client.post("/admin/backup/create", json={}, headers={"X-API-Key": "secret"})
    assert response.status_code == 500
    assert "Failed to write backup archive" in response.json()["detail"]

def test_download_backup_path_traversal(mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    # Use a name containing '..' without '/' to avoid client-side normalization, or URL encode
    r = client.get("/admin/backup/download/backup_.._test.mirror", headers=headers)
    assert r.status_code == 400
    assert "Invalid backup filename" in r.json()["detail"]

def test_download_backup_not_found(mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    r = client.get("/admin/backup/download/nonexistent.mirror", headers=headers)
    assert r.status_code == 404
    assert "Backup file not found" in r.json()["detail"]

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
def test_delete_backup_success(mock_ro, mock_rw, mock_backup_dirs, client):
    backups_dir, _ = mock_backup_dirs
    
    # Create file
    backup_file = backups_dir / "test_backup.mirror"
    backup_file.touch()
    
    headers = {"X-API-Key": "secret"}
    r = client.delete("/admin/backup/delete/test_backup.mirror", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "success"
    assert not backup_file.exists()

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
def test_upload_backup_invalid_extension(mock_ro, mock_rw, mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    
    files = {"file": ("test.txt", b"hello", "text/plain")}
    r = client.post("/admin/backup/upload", files=files, headers=headers)
    assert r.status_code == 400
    assert "extension" in r.json()["detail"]

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
def test_upload_backup_corrupt_zip(mock_ro, mock_rw, mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    
    files = {"file": ("test.mirror", b"corrupt data", "application/octet-stream")}
    r = client.post("/admin/backup/upload", files=files, headers=headers)
    assert r.status_code == 400
    assert "Invalid or corrupt backup archive" in r.json()["detail"]

@patch("mirrordash_core.api.backup.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.remount_ro", new_callable=AsyncMock)
@patch("mirrordash_core.api.backup.asyncio.create_subprocess_exec")
def test_restore_backup_success(mock_subproc, mock_ro, mock_rw, mock_backup_dirs, client):
    backups_dir, data_dir = mock_backup_dirs
    
    # Create the tmp_upload.mirror file that restore expects to find
    tmp_upload = backups_dir / "tmp_upload.mirror"
    tmp_upload.touch()
    
    # Mock extract manifest config content
    mock_manifest = {
        "backup_version": "1.0",
        "modules": []
    }
    mock_config = {
        "globals": {"language": "sv"}
    }
    
    # Mock unzip subprocess success
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"", b""))
    mock_subproc.return_value = mock_process
    
    # Mock file reading of config.json and backup_manifest.json inside the temp directory
    def mock_extract(extract_dir_path):
        manifest_path = Path(extract_dir_path) / "backup_manifest.json"
        config_path = Path(extract_dir_path) / "config.json"
        manifest_path.write_text(json.dumps(mock_manifest))
        config_path.write_text(json.dumps(mock_config))
        
    real_temp_dir = tempfile.TemporaryDirectory
    class FakeTempDirectory:
        def __init__(self, dir=None):
            self.temp_dir = real_temp_dir(dir=dir)
            self.name = self.temp_dir.name
        def __enter__(self):
            mock_extract(self.name)
            return self.name
        def __exit__(self, exc_type, exc_val, exc_tb):
            self.temp_dir.__exit__(exc_type, exc_val, exc_tb)

    with patch("mirrordash_core.api.backup.tempfile.TemporaryDirectory", FakeTempDirectory), \
         patch("mirrordash_core.api.backup.run_restart", new_callable=AsyncMock) as mock_restart, \
         patch("mirrordash_core.api.backup.save_config") as mock_save:
         
        # Send raw json=None (since password is str | None = Body(default=None))
        r = client.post("/admin/backup/restore", json=None, headers={"X-API-Key": "secret"})
        assert r.status_code == 200
        assert r.json()["status"] == "success"
        
        # Verify current system's admin auth was preserved and restored into config.json
        mock_save.assert_called_once()
        restored_cfg = mock_save.call_args[0][0]
        assert restored_cfg["admin_auth"] == MOCK_CONFIG["admin_auth"]
        assert restored_cfg["globals"]["language"] == "sv" # from the backup config

def test_get_modules_dir(tmp_path):
    from mirrordash_core.api.backup import get_modules_dir
    
    # 1. Dev mode: if ROOT_DIR/modules exists and is writeable, it should be used
    fake_root = tmp_path / "root"
    fake_dev_modules = fake_root / "modules"
    fake_dev_modules.mkdir(parents=True)
    
    with patch("mirrordash_core.api.backup.ROOT_DIR", fake_root):
        assert get_modules_dir() == fake_dev_modules
        
    # 2. PyPI mode: if ROOT_DIR/modules does not exist (or is not writeable), use ~/.mirrordash/modules
    fake_home = tmp_path / "home"
    expected_home_modules = fake_home / ".mirrordash" / "modules"
    
    with patch("mirrordash_core.api.backup.ROOT_DIR", tmp_path / "nonexistent"), \
         patch("os.path.expanduser", return_value=str(fake_home)):
        assert get_modules_dir() == expected_home_modules

def test_find_local_module_dir(tmp_path):
    from mirrordash_core.api.backup import find_local_module_dir
    
    fake_root = tmp_path / "root"
    fake_dev_modules = fake_root / "modules"
    fake_dev_modules.mkdir(parents=True)
    
    fake_home = tmp_path / "home"
    fake_home_modules = fake_home / ".mirrordash" / "modules"
    fake_home_modules.mkdir(parents=True)
    
    # Create a dev module
    dev_module = fake_dev_modules / "mirrordash_weather"
    dev_module.mkdir()
    
    # Create a home module
    home_module = fake_home_modules / "mirrordash_calendar"
    home_module.mkdir()
    
    with patch("mirrordash_core.api.backup.ROOT_DIR", fake_root), \
         patch("os.path.expanduser", return_value=str(fake_home)):
        
        # Should find dev module
        assert find_local_module_dir("mirrordash_weather") == dev_module
        # Normalized check (hyphen vs underscore)
        assert find_local_module_dir("mirrordash-weather") == dev_module
        
        # Should find home module
        assert find_local_module_dir("mirrordash_calendar") == home_module
        assert find_local_module_dir("mirrordash-calendar") == home_module
        
        # Should return None for nonexistent
        assert find_local_module_dir("mirrordash_nonexistent") is None
