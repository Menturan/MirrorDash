"""The kept answer of the update check (features/updates/known.py): shared by the Dashboard and Settings,
used for 30 minutes, asked again on force, never kept with a failed part. The clock is faked."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.features.updates import known

KEY = {"X-API-Key": "secret"}
NEWS = {"module_name": "mirrordash_news", "package_name": "mirrordash-news", "current_version": "1.2.0",
        "latest_version": "1.2.1", "target": "mirrordash-news", "notes": False}
CORE = {"current_version": "0.9.0", "latest_version": "0.9.0", "update_available": False}
SYSTEM_UPDATE = {"supported": True, "installed_build": "20261001-0800", "failed_build": None, "update_available": True,
                 "bundle": {"tag": "v0.6.3-os1", "build": "20261018-2237", "url": "https://example.com/b.raucb", "size": 1}}


@pytest.fixture
def clock(monkeypatch):
    """Only known.py's clock: the real one stays for asyncio."""
    now = [1000.0]
    monkeypatch.setattr(known, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1_700_000_000.0))
    return now


@pytest.fixture
def checks(clock):
    """The three checks, as mocks on the known module."""
    with patch.object(known, "check_core_update", AsyncMock(return_value=CORE)) as core, \
         patch.object(known, "check_os_update", AsyncMock(return_value={"supported": False})) as os_update, \
         patch.object(known, "module_updates", AsyncMock(return_value=([NEWS], None))) as modules:
        yield core, os_update, modules


@pytest.fixture
def client():
    return TestClient(app)


def test_dashboard_then_settings_check_once(client, checks):
    with patch("mirrordash_core.admin.load_config", return_value=_auth()):
        banner = client.get("/admin/panels/dashboard/updates", headers=KEY).text
        page = client.get("/admin/panels/system/update-check", headers=KEY).text
    assert "1 Module Update Available" in banner
    for check in checks:
        check.assert_awaited_once()
    assert "mirrordash-news: v1.2.0 → v1.2.1" in page


def _auth():
    from tests.test_api_admin import MOCK_CONFIG
    return MOCK_CONFIG


def test_force_checks_again_with_the_modules_floor(checks):
    core, os_update, modules = checks
    asyncio.run(known.known_updates())
    assert modules.call_args.kwargs == {}
    asyncio.run(known.known_updates())
    assert core.await_count == 1
    asyncio.run(known.known_updates(force=True))
    assert (core.await_count, os_update.await_count, modules.await_count) == (2, 2, 2)
    assert modules.call_args.kwargs == {"max_age": 60}


def test_answer_is_used_for_thirty_minutes(checks, clock):
    core, _, _ = checks
    asyncio.run(known.known_updates())
    clock[0] += 1799
    asyncio.run(known.known_updates())
    assert core.await_count == 1
    clock[0] += 2
    asyncio.run(known.known_updates())
    assert core.await_count == 2


def test_a_failed_part_is_not_kept(checks):
    core, _, _ = checks
    core.side_effect = [OSError("offline"), CORE]
    first = asyncio.run(known.known_updates())
    assert isinstance(first["core"], OSError)
    second = asyncio.run(known.known_updates())
    assert second["core"] == CORE and core.await_count == 2
    asyncio.run(known.known_updates())
    assert core.await_count == 2  # now kept


def test_two_askers_at_once_run_one_check(checks):
    core, _, _ = checks

    async def slow(*args, **kwargs):
        await asyncio.sleep(0.01)
        return CORE
    core.side_effect = slow

    async def both():
        return await asyncio.gather(known.known_updates(), known.known_updates())
    first, second = asyncio.run(both())
    assert core.await_count == 1 and first is second


@pytest.mark.parametrize("new, forgotten", [(True, True), (False, False)])
def test_only_a_changed_test_versions_setting_forgets(checks, new, forgotten):
    from mirrordash_core.features.settings import system_settings
    core, _, _ = checks
    asyncio.run(known.known_updates())
    config = {"system": {"prerelease": False}}
    with patch.object(system_settings, "load_config", return_value=config), \
         patch.object(system_settings, "save_config"):
        asyncio.run(system_settings.update_system_settings({"prerelease": new}))
    asyncio.run(known.known_updates())
    assert core.await_count == (2 if forgotten else 1)


def test_dashboard_shows_the_system_banner(client, checks):
    _, os_update, _ = checks
    os_update.return_value = SYSTEM_UPDATE
    with patch("mirrordash_core.admin.load_config", return_value=_auth()):
        banner = client.get("/admin/panels/dashboard/updates", headers=KEY).text
    assert "Update Available: System" in banner and "<strong>v0.6.3-os1</strong>" in banner
