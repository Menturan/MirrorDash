"""The screen's crash guard (kiosk/guard.py, ARCHITECTURE #47): crashes counted from the page's loads, the steps
it pauses modules in, and the ways back."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mirrordash_core.features.kiosk import guard
from mirrordash_core.features.kiosk.ws import ConnectionManager

MODULES = {
    "mirrordash-clock": {"module": "mirrordash-clock", "position": "top_right"},
    "weather": {"module": "mirrordash-weather", "position": "top_left"},
    "photos": {"module": "mirrordash_photos", "position": "background"},
    "theme": {"module": "mirrordash_foreground_theme", "position": "foreground"},
    "tv": {"module": "mirrordash_tv", "position": "middle_center"},
}


def config(**guard_state) -> dict:
    return {"modules": MODULES, "globals": {"language": "sv", "timezone": "Europe/Stockholm"}, guard.KEY: guard_state}


def no_dists(name):
    return None  # no installed packages: only the saves count


@pytest.fixture(autouse=True)
def _no_entry_points():
    with patch("mirrordash_core.features.modules.loader.find_entry_point", side_effect=no_dists):
        yield


def test_two_crashes_in_ten_minutes_pause_the_module_changed_last():
    start = config(changed={"weather": 100.0, "theme": 500.0})
    once = guard.after_crash(start, 1000.0, set())
    assert once["level"] == 0 if "level" in once else True
    assert not once["paused"]  # one crash is no loop
    twice = guard.after_crash({**start, guard.KEY: once}, 1300.0, set())
    assert twice["level"] == 1 and twice["paused"] == ["theme"] and twice["count"] == 2
    late = guard.after_crash({**start, guard.KEY: once}, 1000.0 + guard.WINDOW + 1, set())
    assert not late["paused"]  # more than ten minutes apart


def test_each_crash_within_ten_minutes_of_a_step_takes_the_next():
    step1 = guard.after_crash(config(changed={"theme": 1.0}, crashes=[900.0]), 1000.0, set())
    step2 = guard.after_crash(config(**step1), 1100.0, {"tv"})
    assert step2["level"] == 2 and set(step2["paused"]) == {"theme", "photos", "tv"}  # layers and video too
    assert step2["by_step"] == {"1": ["theme"], "2": ["photos", "tv"]}
    step3 = guard.after_crash(config(**step2), 1200.0, set())
    assert step3["level"] == 3 and "mirrordash-clock" not in step3["paused"] and len(step3["paused"]) == 4
    calm = guard.after_crash(config(**step2), 1100.0 + guard.WINDOW + 1, set())
    assert calm["level"] == 2  # a crash long after the step: noted, no next step


def test_with_no_module_changed_last_step_one_is_skipped():
    skipped = guard.after_crash(config(crashes=[900.0]), 1000.0, set())
    assert skipped["level"] == 2 and set(skipped["paused"]) == {"photos", "theme"}


def test_the_module_changed_last_counts_installs_by_package_name():
    dists = {"mirrordash_foreground_theme": "mirrordash-foreground-theme", "mirrordash-weather": "mirrordash-weather"}

    def entry_point(name):
        return SimpleNamespace(dist=SimpleNamespace(name=dists[name])) if name in dists else None

    with patch("mirrordash_core.features.modules.loader.find_entry_point", side_effect=entry_point):
        installed = config(changed={"weather": 100.0},
                           installed={"git+https://github.com/Menturan/mirrordash-foreground-theme.git@v2.0.2": 200.0})
        assert guard.last_changed(installed) == "theme"
        everything = config(installed={"mirrordash-weather": 300.0, "mirrordash-foreground-theme": 300.0})
        assert guard.last_changed(everything) is None  # updated together: can't tell


def test_the_screen_hears_the_step_and_at_the_top_its_message_in_the_mirrors_language():
    assert guard.screen_part(config()) == {"guard": 0, "guard_message": None}
    top = guard.screen_part(config(level=3))
    assert top["guard"] == 3 and top["guard_message"]["title"] == "Skärmen kraschade flera gånger"


def test_the_dashboards_banner_says_what_each_step_paused_and_when():
    assert guard.banner(config()) is None
    shown = guard.banner(config(level=2, by_step={"1": ["theme"], "2": ["photos"]}, crashes=[1762280000.0], count=3,
                                since=1762279000.0))
    assert shown["level"] == 2 and shown["first"] == ["Theme"] and shown["more"] == ["Photos"]
    assert shown["count"] == 3 and len(shown["crashes"][0]) == 5  # HH:MM on the owner's clock


@pytest.mark.asyncio
async def test_a_new_page_the_core_didnt_ask_for_is_a_crash():
    from mirrordash_core.admin import BOOT_ID
    manager = ConnectionManager()
    with patch.object(guard, "page_crashed", new_callable=AsyncMock) as crashed:
        await manager.page_loaded("first", None)  # the screen's first load since the core started
        await manager.page_loaded("first", BOOT_ID)  # the same page reconnecting
        assert crashed.await_count == 0
        await manager.page_loaded("second", None)  # loaded anew: Cog restarted after the renderer crashed
        assert crashed.await_count == 1
        restarted = ConnectionManager()  # the core started again: the old page reconnects ...
        await restarted.page_loaded("old", "an-older-start")
        await restarted.page_loaded("fresh", None)  # ... and reloads itself on the new boot id: no crash
        assert crashed.await_count == 1
        await manager.broadcast({"action": "reload"})  # the core asked for it (a settings save)
        await manager.page_loaded("fifth", None)
        assert crashed.await_count == 1
        await manager.page_loaded(None, None)
        await manager.page_loaded("x" * 65, None)  # not an id
        assert crashed.await_count == 1


@pytest.mark.asyncio
async def test_saving_a_paused_module_or_show_again_brings_them_back():
    from mirrordash_core.config import load_config, save_config
    saved = load_config()
    try:
        save_config({**saved, guard.KEY: {"level": 2, "paused": ["theme", "photos"], "crashes": [1.0], "count": 3}})
        assert guard.release("theme") and guard.paused(load_config()) == {"photos"}
        assert not guard.release("weather")  # not paused
        assert guard.level(load_config()) == 2  # the guard holds while something stays paused
        with patch("mirrordash_core.features.modules.client.module_host.reload_modules", new_callable=AsyncMock), \
             patch("mirrordash_core.features.power.privacy.broadcast_screen", new_callable=AsyncMock):
            await guard.show_again()
        after = load_config()
        assert guard.level(after) == 0 and not guard.paused(after)
    finally:
        save_config(saved)
