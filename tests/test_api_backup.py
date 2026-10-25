import asyncio
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from conftest import call
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.backup.service import (
    create_backup,
    list_backups,
    restore_backup,
)

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
    with patch("mirrordash_core.admin.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.features.backup.service.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.features.backup.service.save_config") as mock_save:
        yield mock_save

@pytest.fixture
def mock_backup_dirs(tmp_path):
    backups_dir = tmp_path / "backups"  # not made: a new mirror's storage has no backups folder yet
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    
    with patch("mirrordash_core.features.backup.service.BACKUPS_DIR", str(backups_dir)), \
         patch("mirrordash_core.features.backup.service.DATA_DIR", str(data_dir)), \
         patch.dict("os.environ", {"MIRRORDASH_MODULE_DIR": str(tmp_path / "modules")}):
        yield backups_dir, data_dir

@pytest.fixture
def client():
    return TestClient(app)

def test_backup_list_unauthorized(client):
    # Disable X-API-Key header to trigger unauthorized
    response = client.get("/admin/panels/backup/list")
    assert response.status_code == 401

def test_backup_list_authorized_empty(mock_backup_dirs, client):
    response = call(list_backups)
    assert response.status_code == 200
    assert response.json() == {"backups": []}

def test_backup_list_with_files(mock_backup_dirs, client):
    backups_dir, _ = mock_backup_dirs
    backups_dir.mkdir()  # a mirror with saved backups
    
    # Create a mock zip backup file
    backup_file = backups_dir / "test_backup.mirror"
    with zipfile.ZipFile(backup_file, "w") as zf:
        zf.writestr("backup_manifest.json", json.dumps({"encrypted": False}))
        
    response = call(list_backups)
    assert response.status_code == 200
    backups = response.json()["backups"]
    assert len(backups) == 1
    assert backups[0]["filename"] == "test_backup.mirror"
    assert backups[0]["encrypted"] is False

@patch("mirrordash_core.features.backup.service.asyncio.create_subprocess_exec")
def test_create_backup_success(mock_subproc, mock_backup_dirs, client):
    backups_dir, data_dir = mock_backup_dirs
    
    # Mock subprocess success
    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"", b""))
    mock_subproc.return_value = mock_process
    
    response = call(create_backup)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "mirrordash_backup_" in response.json()["filename"]

@patch("mirrordash_core.features.backup.service.asyncio.create_subprocess_exec")
def test_create_backup_encryption_failure(mock_subproc, mock_backup_dirs, client):
    """openssl (and nmcli, for the Wi-Fi) failing: an error, and no half-written backup left."""
    backups_dir, _ = mock_backup_dirs
    mock_process = MagicMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(return_value=(b"", b"openssl error"))
    mock_subproc.return_value = mock_process

    response = call(create_backup, "secret")
    assert response.status_code == 500
    assert "Failed to write backup archive" in response.json()["detail"]
    assert not list(backups_dir.glob("*.mirror"))

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

def test_delete_backup_success(mock_backup_dirs, client):
    backups_dir, _ = mock_backup_dirs
    backups_dir.mkdir()
    
    # Create file
    backup_file = backups_dir / "test_backup.mirror"
    backup_file.touch()
    
    headers = {"X-API-Key": "secret"}
    r = client.post("/admin/panels/backup/delete/test_backup.mirror", headers=headers)
    assert r.status_code == 200
    assert not backup_file.exists()

def test_upload_backup_invalid_extension(mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    
    files = {"file": ("test.txt", b"hello", "text/plain")}
    r = client.post("/admin/panels/backup/upload", files=files, headers=headers)
    assert "extension" in r.text

def test_upload_backup_corrupt_zip(mock_backup_dirs, client):
    headers = {"X-API-Key": "secret"}
    
    files = {"file": ("test.mirror", b"corrupt data", "application/octet-stream")}
    r = client.post("/admin/panels/backup/upload", files=files, headers=headers)
    assert "Invalid or corrupt backup archive" in r.text

@patch("mirrordash_core.features.backup.service.asyncio.create_subprocess_exec")
def test_restore_backup_success(mock_subproc, mock_backup_dirs, module_site, client):
    backups_dir, data_dir = mock_backup_dirs
    backups_dir.mkdir()
    
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

    with patch("mirrordash_core.features.backup.service.tempfile.TemporaryDirectory", FakeTempDirectory), \
         patch("mirrordash_core.features.backup.service.run_restart", new_callable=AsyncMock), \
         patch("mirrordash_core.features.backup.service.save_config") as mock_save, \
         patch("mirrordash_core.features.backup.service.zipfile.ZipFile"):
         
        # Send raw json=None (since password is str | None = Body(default=None))
        r = call(restore_backup)
        assert r.status_code == 200
        assert r.json()["status"] == "success"
        
        # Verify current system's admin auth was preserved and restored into config.json
        mock_save.assert_called_once()
        restored_cfg = mock_save.call_args[0][0]
        assert restored_cfg["admin_auth"] == MOCK_CONFIG["admin_auth"]
        assert restored_cfg["globals"]["language"] == "sv" # from the backup config

def test_get_modules_dir(tmp_path):
    from mirrordash_core.features.backup.service import get_modules_dir
    
    # 1. Dev mode: if ROOT_DIR/modules exists and is writeable, it should be used
    fake_root = tmp_path / "root"
    fake_dev_modules = fake_root / "modules"
    fake_dev_modules.mkdir(parents=True)
    
    with patch("mirrordash_core.features.backup.service.ROOT_DIR", fake_root):
        assert get_modules_dir() == fake_dev_modules
        
    # 2. PyPI mode: if ROOT_DIR/modules does not exist (or is not writeable), the module host's folder,
    # where it can build them
    with patch("mirrordash_core.features.backup.service.ROOT_DIR", tmp_path / "nonexistent"), \
         patch.dict("os.environ", {"MIRRORDASH_MODULE_DIR": str(tmp_path / "modules")}):
        assert get_modules_dir() == tmp_path / "modules" / "local"

def test_find_local_module_dir(tmp_path):
    from mirrordash_core.features.backup.service import find_local_module_dir
    
    fake_root = tmp_path / "root"
    fake_dev_modules = fake_root / "modules"
    fake_dev_modules.mkdir(parents=True)
    
    fake_home_modules = tmp_path / "modules" / "local"
    fake_home_modules.mkdir(parents=True)
    
    # Create a dev module
    dev_module = fake_dev_modules / "mirrordash_weather"
    dev_module.mkdir()
    
    # Create a home module
    home_module = fake_home_modules / "mirrordash_calendar"
    home_module.mkdir()
    
    with patch("mirrordash_core.features.backup.service.ROOT_DIR", fake_root), \
         patch.dict("os.environ", {"MIRRORDASH_MODULE_DIR": str(tmp_path / "modules")}):

        # Should find dev module
        assert find_local_module_dir("mirrordash_weather") == dev_module
        # Normalized check (hyphen vs underscore)
        assert find_local_module_dir("mirrordash-weather") == dev_module
        
        # Should find home module
        assert find_local_module_dir("mirrordash_calendar") == home_module
        assert find_local_module_dir("mirrordash-calendar") == home_module
        
        # Should return None for nonexistent
        assert find_local_module_dir("mirrordash_nonexistent") is None

def test_a_folder_swapped_for_a_link_during_a_restore_doesnt_reach_the_core(tmp_path, monkeypatch):
    """The module host keeps running while pi restores into its folder: a folder on the way swapped for a
    link to the core's data after any check must still not be written through, nor emptied."""
    from mirrordash_core.features.backup import service
    host_dir, core_data = tmp_path / "modules", tmp_path / "data"
    (host_dir / "data" / "weather").mkdir(parents=True)
    core_data.mkdir()
    (core_data / "config.json").write_text("secret")
    src = tmp_path / "backup" / "weather"
    src.mkdir(parents=True)
    (src / "state.json").write_text("{}")
    monkeypatch.setattr(service, "get_module_dir", lambda: host_dir)
    real_rmtree = shutil.rmtree

    def swap_then_rmtree(*args, **kwargs):  # the host, between pi's look and pi's writes
        (host_dir / "data").rename(host_dir / "data-old")
        (host_dir / "data").symlink_to(core_data)
        real_rmtree(*args, **kwargs)

    monkeypatch.setattr(service.shutil, "rmtree", swap_then_rmtree)
    assert service._replace_in_module_folder(src, host_dir / "data" / "weather")
    assert sorted(p.name for p in core_data.iterdir()) == ["config.json"]
    assert (host_dir / "data-old" / "weather" / "state.json").read_text() == "{}"  # where the folder went


def test_backup_round_trip_to_fresh_mirror(mock_backup_dirs, module_site, client):
    """A real backup (real zip) restored on a freshly flashed mirror: settings, module data and
    GitHub modules come back, the new mirror's admin password stays, GPIO overlays get written."""
    backups_dir, data_dir = mock_backup_dirs
    old_config = {
        "admin_auth": {"hash": "old-hash", "salt": "old-salt"},
        "globals": {"language": "sv", "timezone": "Europe/Oslo"},
        "system": {"rotation": "left", "devices": [{"type": "button", "pin": 17}],
                   "gpio_overlays": ["button:17"]},
        "modules": {"mirrordash-weather": {"module": "mirrordash-weather", "position": "top_right"}},
    }
    # The old mirror: the live config.json in the core's data dir, module data in the module host's
    (data_dir / "config.json").write_text(json.dumps(old_config))
    module_data = data_dir.parent / "modules" / "data"
    (module_data / "mirrordash-weather").mkdir(parents=True)
    (module_data / "mirrordash-weather" / "state.json").write_text('{"city": "Oslo"}')
    # The module host owns that folder: links it put there must not carry the core's data into the backup
    (module_data / "mirrordash-weather" / "stolen.json").symlink_to(data_dir / "config.json")
    (module_data / "mirrordash-weather" / "stolen").symlink_to(data_dir)

    commit = "a" * 40
    dist = MagicMock(version="1.2.0")
    dist.name = "mirrordash-weather"
    dist.read_text.return_value = json.dumps({
        "url": "https://github.com/someone/mirrordash-weather.git",
        "vcs_info": {"vcs": "git", "commit_id": commit}})
    ep = MagicMock(dist=dist)
    ep.name = "mirrordash-weather"
    source = f"git+https://github.com/someone/mirrordash-weather.git@{commit}"

    with patch("mirrordash_core.features.backup.service.find_local_module_dir", return_value=None), \
         patch("mirrordash_core.features.backup.service.module_entry_points", return_value=[ep]):
        with patch("mirrordash_core.features.backup.service.load_config", return_value=old_config):
            r = call(create_backup)
        assert r.status_code == 200, r.json()
        archive = backups_dir / r.json()["filename"]

        with zipfile.ZipFile(archive) as zf:
            assert not any(b"old-hash" in zf.read(n) for n in zf.namelist() if not n.endswith("/"))
            assert json.loads(zf.read("backup_manifest.json"))["modules"][0]["source"] == source

        # A freshly flashed mirror: only the new admin password is set up
        shutil.rmtree(data_dir)
        shutil.rmtree(module_data)
        data_dir.mkdir()
        module_data.mkdir()
        (module_data / "mirrordash-weather").symlink_to(data_dir)  # the restore must not empty the core's data dir
        fresh_config = json.dumps({"admin_auth": MOCK_CONFIG["admin_auth"]})
        (data_dir / "config.json").write_text(fresh_config)
        shutil.copy(archive, backups_dir / "tmp_upload.mirror")

        with patch("mirrordash_core.features.hardware.devices.write_gpio_overlays", new_callable=AsyncMock,
                   return_value=None) as mock_gpio, \
             patch("mirrordash_core.features.backup.service.reboot_system", new_callable=AsyncMock) as mock_reboot, \
             patch("mirrordash_core.features.backup.service.run_restart", new_callable=AsyncMock) as mock_restart, \
             patch("mirrordash_core.features.backup.service.save_config") as mock_save:
            r = call(restore_backup)
            assert r.status_code == 200, r.json()

    restored = mock_save.call_args[0][0]
    assert restored["admin_auth"] == MOCK_CONFIG["admin_auth"]
    assert restored["globals"]["timezone"] == "Europe/Oslo"
    assert restored["system"]["rotation"] == "left"
    assert restored["modules"]["mirrordash-weather"]["position"] == "top_right"
    # The data copy must not put the old credentials back over the saved config
    assert (data_dir / "config.json").read_text() == fresh_config
    state = module_data / "mirrordash-weather" / "state.json"
    assert state.read_text() == '{"city": "Oslo"}'
    assert state.stat().st_mode & 0o060 == 0o060  # the module host (the group) can write what pi put back
    assert sorted(p.name for p in (module_data / "mirrordash-weather").iterdir()) == ["state.json"]
    assert module_site.calls == [("pip", "install", source)] and module_site.kept  # by the module host
    mock_gpio.assert_awaited_once_with(["button:17"])
    mock_reboot.assert_called_once()
    mock_restart.assert_not_called()


@pytest.mark.asyncio
async def test_read_manifest_tells_a_locked_backup_from_a_wrong_password(tmp_path):
    from mirrordash_core.features.backup.service import (
        _openssl,
        is_encrypted,
        read_manifest,
    )
    plain = tmp_path / "plain.mirror"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("backup_manifest.json", '{"backup_version": "2.0"}')
    locked = str(tmp_path / "locked.mirror")
    assert await _openssl("my secret", "-salt", "-in", str(plain), "-out", locked)
    assert is_encrypted(locked) and not is_encrypted(str(plain))
    assert await read_manifest(locked) is None
    assert all([await read_manifest(locked, f"wrong{i}") is None for i in range(3)])
    assert await read_manifest(locked, "my secret") == {"backup_version": "2.0"}
    assert await read_manifest(str(plain)) == {"backup_version": "2.0"}
    with pytest.raises(zipfile.BadZipFile):
        await read_manifest(__file__)  # not a backup at all


@pytest.mark.skipif(shutil.which("zip") is None, reason="needs the zip tool to make an old backup")
@pytest.mark.asyncio
async def test_an_old_zipcrypto_backup_says_so(tmp_path):
    import subprocess

    from mirrordash_core.features.backup.service import read_manifest
    (tmp_path / "backup_manifest.json").write_text('{"backup_version": "1.0"}')
    subprocess.run(["zip", "-q", "-P", "secret", "old.mirror", "backup_manifest.json"], cwd=tmp_path, check=True)
    with pytest.raises(ValueError, match="older"):
        await read_manifest(str(tmp_path / "old.mirror"), "secret")


def test_an_encrypted_backup_carries_the_logins_and_wifi(mock_backup_dirs, module_site, client):
    """With a password: one encrypted file with the admin password, the pi password and the Wi-Fi;
    never the login sessions or the config's last good copy. It restores them on a fresh mirror."""
    backups_dir, data_dir = mock_backup_dirs
    old_config = {"admin_auth": {"hash": "old-hash", "salt": "old-salt"}, "globals": {"language": "sv"}}
    for name, text in {"config.json": "{}", "config.json.bak": "old-hash", "sessions.json": "cookie",
                       "pi_password.hash": "$6$pi"}.items():
        (data_dir / name).write_text(text)
    wifi = [{"ssid": "Home", "psk": "wifi pass"}]
    password = "a pass with spaces"

    with patch("mirrordash_core.features.backup.service.load_config", return_value=old_config), \
         patch("mirrordash_core.features.backup.service.saved_wifi", new_callable=AsyncMock, return_value=wifi), \
         patch("mirrordash_core.features.backup.service.installed_modules", return_value=[]):
        r = call(create_backup, password)
    assert r.status_code == 200, r.json()
    archive = backups_dir / r.json()["filename"]
    raw = archive.read_bytes()
    assert raw.startswith(b"Salted__") and b"wifi pass" not in raw and b"old-hash" not in raw

    async def contents(pw):
        from mirrordash_core.features.backup.service import plain_zip
        async with plain_zip(str(archive), pw) as zip_path:
            if zip_path is None:
                return None
            with zipfile.ZipFile(zip_path) as zf:
                return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}
    assert asyncio.run(contents("wrong")) is None
    files = asyncio.run(contents(password))
    assert json.loads(files["config.json"])["admin_auth"]["hash"] == "old-hash"
    assert json.loads(files["wifi.json"]) == wifi and files["data/pi_password.hash"] == b"$6$pi"
    assert not {"data/sessions.json", "data/config.json.bak", "data/config.json"} & set(files)

    # A fresh mirror being set up: no admin password yet
    shutil.copy(archive, backups_dir / "tmp_upload.mirror")
    (data_dir / "sessions.json").write_text("new")
    with patch("mirrordash_core.features.backup.service.load_config", return_value={}), \
         patch("mirrordash_core.features.backup.service.add_wifi", new_callable=AsyncMock) as mock_wifi, \
         patch("mirrordash_core.features.backup.service.sync_gpio_overlays", new_callable=AsyncMock,
               return_value=(False, None)), \
         patch("mirrordash_core.features.backup.service.run_restart", new_callable=AsyncMock), \
         patch("mirrordash_core.features.backup.service.save_config") as mock_save:
        assert call(restore_backup, "wrong").status_code == 500
        r = call(restore_backup, password)
        assert r.status_code == 200, r.json()
    assert mock_save.call_args[0][0]["admin_auth"]["hash"] == "old-hash"
    mock_wifi.assert_awaited_once_with(wifi)
    assert (data_dir / "sessions.json").read_text() == "new"


def test_a_backup_cannot_name_a_folder_outside_the_modules(tmp_path):
    """A local module's folder_name from the manifest is emptied before the copy: never a path."""
    from mirrordash_core.features.backup.service import _restore_module
    victim = tmp_path / "victim"
    victim.mkdir()
    (tmp_path / "local_modules").mkdir()
    for folder in (str(victim), "../victim", "..", None):
        asyncio.run(_restore_module({"type": "local", "package_name": "x", "folder_name": folder}, tmp_path, "python"))
    assert victim.exists()


def test_a_backup_file_can_only_show_text_never_markup():
    """The summary shows what an uploaded file says (module names, versions, its own name, the
    password typed): a crafted backup must not run script in the admin page."""
    from mirrordash_core.features.backup import routes
    evil = '"><script>alert(1)</script>'
    manifest = {"timestamp": evil, "modules": [{"package_name": evil, "version": evil, "type": "local"}]}
    with patch.object(routes, "read_manifest", new_callable=AsyncMock, return_value=manifest):
        page = asyncio.run(routes._summary_or_password_prompt(f"x{evil}.mirror", is_local=False, password=evil)).body.decode()
    assert "<script>" not in page and page.count("&lt;script&gt;") == 5  # timestamp, name, version, file name, password
    assert "Local source" in page and 'hx-disabled-elt="find button"' in page
    with patch.object(routes, "read_manifest", new_callable=AsyncMock, return_value=None):  # encrypted, wrong password
        page = asyncio.run(routes._summary_or_password_prompt(f"x{evil}.mirror", is_local=True, password="nope")).body.decode()
    assert "Invalid backup password." in page and "<script>" not in page and 'name="is_local" value="true"' in page
