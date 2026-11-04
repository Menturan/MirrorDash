# SPDX-License-Identifier: AGPL-3.0-only
"""A list or search setting's choices (ARCHITECTURE #48): the module's settings_list_<key> /
settings_search_<key>, called from the admin's unsaved form, in a process of the module."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mirrordash_core.features.modules import choices, settings_process
from mirrordash_core.features.modules.client import ModuleHost


class Schools:
    def __init__(self, config):
        self.config = config

    async def settings_list_school(self, item):
        if not self.config.get("token"):
            raise ValueError("Enter the API key first")
        if item.get("district") == "crash":
            raise KeyError("district")
        return [{"id": 913, "name": "Fridhemsskolan", "detail": item["district"]},
                {"name": "no id"}, "not a dict", {"id": "x" * 500, "name": "Long"}]

    async def settings_search_city(self, item, query):
        return [{"id": query, "name": query.title()}]

    def settings_list_sync(self, item):
        return []


def ask(request, cfg=None):
    with patch.object(choices, "find_entry_point", return_value=SimpleNamespace(load=lambda: Schools, dist=None)), \
         patch.object(choices, "package_dir", return_value=None):
        return asyncio.run(choices.setting_choices("skolmat", cfg or {"module": "skolmat", "token": "t"}, {},
                                                   SimpleNamespace(awake=asyncio.Event()), request))


def test_a_list_setting_gets_its_row_and_answers_checked_choices():
    answer = ask({"kind": "list", "key": "school", "item": {"district": "Gävle"}, "query": "ignored"})
    assert answer["choices"][0] == {"id": "913", "name": "Fridhemsskolan", "detail": "Gävle"}
    assert len(answer["choices"]) == 2 and len(answer["choices"][1]["id"]) == choices.MAX_TEXT  # bad ones left out, cut


def test_a_search_setting_gets_what_was_typed():
    assert ask({"kind": "search", "key": "city", "item": {}, "query": "gävle"})["choices"] == [
        {"id": "gävle", "name": "Gävle", "detail": ""}]


def test_errors_say_what_is_wrong_without_the_modules_internals(caplog):
    assert ask({"kind": "list", "key": "school", "item": {}}, {"module": "skolmat"}) == {"error": "Enter the API key first"}
    assert ask({"kind": "list", "key": "school", "item": {"district": "crash"}}) == {"error": "Couldn't load the choices (see Logs)."}
    assert "KeyError" in caplog.text
    assert "no async settings_list_sync()" in ask({"kind": "list", "key": "sync", "item": {}})["error"]
    assert "no async settings_search_school()" in ask({"kind": "search", "key": "school", "item": {}})["error"]
    for bad in ({"kind": "list", "key": "School", "item": {}}, {"kind": "list", "key": "a-b", "item": {}},
                {"kind": "run", "key": "school", "item": {}}, {"kind": "list", "key": "school", "item": "x"}):
        assert ask(bad) == {"error": "Not a list or search setting."}


@pytest.mark.asyncio
async def test_a_settings_process_starts_once_stays_while_used_and_stops_when_idle(monkeypatch):
    monkeypatch.setattr(settings_process, "SETTINGS_IDLE", 0.05)
    ended = asyncio.Event()
    conn = MagicMock(close=MagicMock(side_effect=ended.set))
    opened = AsyncMock(return_value=conn)
    processes = settings_process.SettingsProcesses(opened, lambda c: ended.wait())
    assert await processes.get("md_school") is conn and await processes.get("md_school") is conn
    opened.assert_awaited_once_with("settings-md_school")
    assert conn.module == "md_school"
    await asyncio.wait_for(ended.wait(), 1)  # closed SETTINGS_IDLE after the last get
    await asyncio.sleep(0)
    assert await processes.get("md_school") is conn and opened.await_count == 2  # started again when asked again


@pytest.mark.asyncio
async def test_the_drawer_asks_the_instance_then_its_module_then_a_settings_process():
    core = ModuleHost()
    core._control = object()
    core._settings = MagicMock(get=AsyncMock(return_value="settings process"))
    own, other = SimpleNamespace(module="md_school"), SimpleNamespace(module="md_school")
    core._instances = {"school": own, "school_2": other, "clock": SimpleNamespace(module="md_clock")}
    assert await core._connection_for("school", {"module": "md-school"}) is own
    assert await core._connection_for("school_3", {"module": "md_school"}) in (own, other)  # any of the module
    assert await core._connection_for("clock", {"module": "md_weather"}) == "settings process"
    core._settings.get.assert_awaited_once_with("md_weather")
