import asyncio
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app

AUTH = {"admin_auth": {"hash": hash_password("secret", "salt"), "salt": "salt"}}


def make_backup(tmp_path, password: str | None, wifi: list | None) -> bytes:
    """A minimal .mirror file: plain, or encrypted with openssl like create_backup does."""
    from mirrordash_core.features.backup.service import _openssl
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("backup_manifest.json", json.dumps({"backup_version": "2.0", "modules": []}))
        if wifi is not None:
            zf.writestr("wifi.json", json.dumps(wifi))
    if not password:
        return plain.read_bytes()
    out = tmp_path / "locked.mirror"
    assert asyncio.run(_openssl(password, "-salt", "-in", str(plain), "-out", str(out)))
    return out.read_bytes()


@pytest.fixture
def setup_client(tmp_path):
    """The setup page's API on a mirror without an admin password; backups land in tmp_path."""
    with patch("mirrordash_core.features.wifi.routes.load_config", return_value={}) as load, \
         patch("mirrordash_core.features.backup.service.BACKUPS_DIR", str(tmp_path / "backups")), \
         patch("mirrordash_core.features.wifi.routes.connect_wifi", new_callable=AsyncMock,
               return_value=(True, "ok")):
        yield TestClient(app), load


def test_setup_restore_needs_the_admin_password_once_the_mirror_has_one(setup_client, tmp_path):
    client, load = setup_client
    load.return_value = AUTH
    with patch("mirrordash_core.admin.load_config", return_value=AUTH):
        files = {"file": ("b.mirror", make_backup(tmp_path, "pw", [{"ssid": "Home", "psk": "x"}]))}
        assert client.post("/api/wifi/restore", files=files, data={"password": "pw"}).status_code == 401
        assert client.post("/api/wifi/restore", files=files, data={"password": "pw"},
                           headers={"X-API-Key": "wrong"}).status_code == 401


def test_setup_restore_answers_before_it_leaves_the_hotspot(setup_client, tmp_path):
    """Wrong password and a backup without Wi-Fi are told to the phone; a good one names the network."""
    client, _ = setup_client
    locked = make_backup(tmp_path, "pw", [{"ssid": "Home", "psk": "x"}])
    r = client.post("/api/wifi/restore", files={"file": ("b.mirror", locked)}, data={"password": "nope"})
    assert r.status_code == 400 and "Wrong password" in r.json()["detail"]
    r = client.post("/api/wifi/restore", files={"file": ("b.mirror", make_backup(tmp_path, None, None))})
    assert r.status_code == 400 and "no Wi-Fi" in r.json()["detail"]
    with patch("mirrordash_core.features.wifi.routes.start_job"):
        r = client.post("/api/wifi/restore", files={"file": ("b.mirror", locked)}, data={"password": "pw"})
    assert r.status_code == 200 and r.json()["networks"] == ["Home"]


def test_wifi_api_counts_wrong_passwords_like_the_login():
    """The Wi-Fi API used to compare the hash itself, without the lockout after wrong tries."""
    with patch("mirrordash_core.features.wifi.routes.load_config", return_value=AUTH), \
         patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.wifi.routes.scan_wifi_networks", new_callable=AsyncMock, return_value=[]):
        client = TestClient(app)
        codes = [client.get("/api/wifi/scan", headers={"X-API-Key": f"guess{i}"}).status_code for i in range(8)]
    assert codes[0] == 401 and 429 in codes


def test_a_failed_uninstall_keeps_the_module_settings():
    from mirrordash_core.features.modules import service
    config = {"modules": {"clock-one": {"module": "mirrordash-clock"}}}
    with patch.object(service, "load_config", return_value=config), \
         patch.object(service, "save_config") as save, \
         patch.object(service, "_change_packages", new_callable=AsyncMock,
                      side_effect=HTTPException(status_code=500, detail="Uninstall failed")), \
         pytest.raises(HTTPException):
        asyncio.run(service.uninstall_module("mirrordash-clock"))
    save.assert_not_called()
