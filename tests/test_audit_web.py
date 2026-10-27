# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Checks for the web-side audit fixes: password hashing off the event loop, SSH results, system
settings validation, form booleans, and a stalled screen not delaying the rest."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from mirrordash_core import admin, system_settings
from mirrordash_core.forms import cast_values_by_schema, parse_flat_form_data


def test_check_password_is_async_and_throttled():
    auth = {"hash": admin.hash_password("secret", "salt"), "salt": "salt"}
    with patch.object(admin, "load_config", return_value={"admin_auth": auth}):
        assert asyncio.run(admin.check_password("secret", "audit-ok")) is True
        assert asyncio.run(admin.check_password("wrong", "audit-bad")) is False
        for _ in range(admin.MAX_FAILURES):
            asyncio.run(admin.check_password("wrong", "audit-locked"))
        with pytest.raises(HTTPException) as e:
            asyncio.run(admin.check_password("secret", "audit-locked"))
        assert e.value.status_code == 429


def test_ssh_failure_is_reported():
    from mirrordash_core.features.settings import ssh
    proc = AsyncMock(wait=AsyncMock(return_value=1))
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        assert asyncio.run(ssh.set_ssh_status(False)) is False
    with patch.object(ssh, "set_ssh_status", AsyncMock(return_value=False)), \
         patch.object(ssh.os, "remove"), pytest.raises(HTTPException) as e:
        asyncio.run(ssh.apply_ssh(False, None))
    assert e.value.status_code == 500


@pytest.mark.parametrize("body", [
    {"display_control": "x"}, {"display_control": {"wake": 5}}, {"display_control": {"interval": []}},
    {"ssh": "false", "pi_password": "x"}, {"resolution": 5}, {"resolution": "a b;c"},
])
def test_system_settings_reject_wrong_types(body):
    with patch.object(system_settings, "load_config", return_value={"system": {}}), \
         patch.object(system_settings, "save_config"), pytest.raises(HTTPException) as e:
        asyncio.run(system_settings.update_system_settings(settings=body))
    assert e.value.status_code == 400


def test_system_settings_keep_what_was_saved_during_the_slow_apply():
    configs = [{"system": {}}, {"system": {}, "other": "saved meanwhile"}]
    saved = {}
    with patch.object(system_settings, "load_config", side_effect=lambda: configs.pop(0)), \
         patch.object(system_settings, "save_config", side_effect=saved.update), \
         patch.object(system_settings, "apply_ssh", new_callable=AsyncMock):
        asyncio.run(system_settings.update_system_settings(settings={"ssh": False}))
    assert saved["other"] == "saved meanwhile"


def test_a_text_value_true_stays_text_and_booleans_follow_the_schema():
    parsed = parse_flat_form_data({"name": "true", "on": ["false", "true"], "off": "false"})
    assert parsed["name"] == "true" and parsed["on"] is True
    schema = {"properties": {"off": {"type": "boolean"}, "name": {"type": "string"}}}
    assert cast_values_by_schema(parsed, schema)["off"] is False
    assert cast_values_by_schema(parsed, schema)["name"] == "true"


def test_a_stalled_screen_does_not_delay_the_others():
    from mirrordash_core.features.kiosk import ws
    good, stalled = AsyncMock(), AsyncMock()

    async def hang(message):
        await asyncio.sleep(60)

    stalled.send_json.side_effect = hang
    manager = ws.ConnectionManager()
    manager.active_connections = [stalled, good]

    async def go():
        with patch.object(ws, "SEND_TIMEOUT", 0.05):
            await manager._send({"x": 1})

    asyncio.run(go())
    good.send_json.assert_awaited_once_with({"x": 1})
    assert manager.active_connections == [good]


@pytest.mark.parametrize("package_name", ["-r/storage/requirements.txt", "--editable", "../evil", "a b"])
def test_a_package_name_cant_be_an_option_or_a_path_out(package_name):
    """uv reads a name starting with - as an option: "-r/file" would install whatever that file lists."""
    from fastapi import HTTPException

    from mirrordash_core.features.modules.service import check_package_name
    with pytest.raises(HTTPException):
        check_package_name(package_name)
