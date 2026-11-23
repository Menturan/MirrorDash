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
    """A minimal .mirror file: plain, or encrypted like create_backup does."""
    from mirrordash_core.features.backup.archive import encrypt_zip
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("backup_manifest.json", json.dumps({"backup_version": "2.0", "modules": []}))
        if wifi is not None:
            zf.writestr("wifi.json", json.dumps(wifi))
    if not password:
        return plain.read_bytes()
    out = tmp_path / "locked.mirror"
    asyncio.run(encrypt_zip(password, str(plain), str(out)))
    return out.read_bytes()


@pytest.fixture
def setup_client(tmp_path):
    """The setup page's API on a mirror without an admin password; backups land in tmp_path."""
    with patch("mirrordash_core.features.wifi.routes.load_config", return_value={}) as load, \
         patch("mirrordash_core.features.backup.service.BACKUPS_DIR", str(tmp_path / "backups")), \
         patch("mirrordash_core.features.backup.setup_restore.connect_wifi", new_callable=AsyncMock,
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
    with patch("mirrordash_core.features.backup.setup_restore.start_job"):
        r = client.post("/api/wifi/restore", files={"file": ("b.mirror", locked)}, data={"password": "pw"})
    assert r.status_code == 200 and r.json()["networks"] == ["Home"]
    # An older MirrorDash's backup and a changed one: told apart from a wrong password, nothing joined
    r = client.post("/api/wifi/restore", files={"file": ("b.mirror", b"Salted__" + bytes(64))}, data={"password": "pw"})
    assert r.status_code == 400 and "older MirrorDash" in r.json()["detail"]
    changed = bytearray(locked)
    changed[-5] ^= 1
    r = client.post("/api/wifi/restore", files={"file": ("b.mirror", bytes(changed))}, data={"password": "pw"})
    assert r.status_code == 400 and "damaged or has been changed" in r.json()["detail"]


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


def _uninstall_with(venv_has, uv, paths=("/storage/mirrordash/venv", None, None), package="mirrordash-clock"):
    from pathlib import Path

    from mirrordash_core.features.modules import service
    config = {"modules": {"clock-one": {"module": package}}}
    order = []

    async def change(*args, **kwargs):
        order.append("site")

    async def fake_uv(python, command, *args, timeout=None):
        order.append(("venv", python, command, args))
        return await uv()

    with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config") as save, \
         patch.object(service, "_change_packages", side_effect=change), patch.object(service, "allow_scripts"), \
         patch.object(service, "get_venv_paths", return_value=paths and (Path(paths[0]), None, None)), \
         patch.object(service, "venv_distributions",
               return_value=iter([object()] if venv_has else [])), \
         patch.object(service, "uv_pip", fake_uv):
        try:
            asyncio.run(service.uninstall_module(package))
            error = None
        except HTTPException as e:
            error = e
    return order, save, error


async def _ok():
    return 0, "", ""


def test_uninstalling_the_clock_removes_it_from_the_venv_then_the_site():
    order, _, error = _uninstall_with(True, _ok)
    assert error is None
    assert order == [("venv", "/storage/mirrordash/venv/bin/python", "uninstall", ("mirrordash-clock",)), "site"]


def test_a_failed_venv_uninstall_changes_nothing_else():
    async def fail():
        return 1, "", "boom"

    order, save, error = _uninstall_with(True, fail)
    assert error.status_code == 500 and "Uninstall failed: boom" in error.detail
    assert [o for o in order if o == "site"] == []
    save.assert_not_called()


def test_another_module_is_not_uninstalled_from_the_venv():
    order, _, _ = _uninstall_with(True, _ok, package="mirrordash-ha")
    assert order == ["site"]


def test_not_on_a_mirror_the_venv_is_left_alone():
    order, _, _ = _uninstall_with(True, _ok, paths=None)
    assert order == ["site"]
