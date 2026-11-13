"""The cards fade in when the screen wakes and out before it rests (features/power/wake_fade.py)."""

import asyncio
from unittest.mock import patch

import pytest

from mirrordash_core.features.power.display_power import DisplayPowerManager
from mirrordash_core.features.power.privacy import screen_message
from mirrordash_core.features.power.wake_fade import fade_message, rest_wait
from mirrordash_core.features.settings.system_settings import SystemSettings


def test_the_styles_become_the_numbers_the_page_reads():
    one_by_one = fade_message({})  # the defaults
    assert one_by_one == {"style": "stagger", "rise": 8, "step": 450, "duration": 2500, "delay": 0,
                          "rest_step": 225, "rest_duration": 1500}
    assert fade_message({"fade": {"style": "stagger_still"}})["rise"] == 0
    together = fade_message({"fade": {"style": "together"}})
    assert together["step"] == 0 and together["rise"] == 0
    assert fade_message({"fade": {"style": "none"}}) == {"style": "none"}
    assert fade_message({"fade": {"style": "wobble", "step_ms": -1, "duration_ms": "x"}}) == one_by_one  # not right: the defaults
    off = fade_message({"fade": {"fade_out": False, "start_delay_ms": 3000}})
    assert off["rest_step"] == off["rest_duration"] == 0 and off["delay"] == 3000


def test_the_output_waits_for_the_last_card_to_fade():
    fade = fade_message({})
    assert rest_wait(fade, 5) == (4 * 225 + 1500) / 1000
    assert rest_wait(fade, 0) == rest_wait(fade, 1) == 1.5
    assert rest_wait(fade_message({"fade": {"fade_out": False}}), 5) == 0
    assert rest_wait(fade_message({"fade": {"style": "none"}}), 5) == 0


def test_the_settings_are_kept_to_their_ranges():
    def fade(**values):
        return SystemSettings.model_validate({"display_control": {"fade": values}})
    fade(style="stagger_still", step_ms=0, duration_ms=5000, start_delay_ms=10000, fade_out=False)
    for bad in ({"style": "wobble"}, {"step_ms": 1001}, {"duration_ms": 199}, {"start_delay_ms": -1}, {"fade_out": "yes"}):
        with pytest.raises(ValueError):
            fade(**bad)


def test_every_screen_message_says_whether_the_screen_rests_and_how_the_cards_fade():
    from mirrordash_core.features.power.display_power import display_power_manager
    message = screen_message({"system": {"display_control": {"fade": {"style": "together"}}}})
    assert message["resting"] is False and message["fade"]["style"] == "together"
    display_power_manager.resting = True
    try:
        assert screen_message({})["resting"] is True and screen_message({})["fade"]["style"] == "stagger"
    finally:
        display_power_manager.resting = False


def run_set_state(on: bool, *, output_works=True, is_on=True, resting=False) -> list:
    """What set_state(on) does, in order: what the page is told and what the output and the TV are asked."""
    calls = []
    manager = DisplayPowerManager()
    manager.is_on, manager.resting = is_on, resting
    config = {"system": {"display_control": {"fade": {"step_ms": 100, "duration_ms": 200}}}}

    async def broadcast(message):  # the message itself reads the app's own manager: see its test above
        calls.append(("page", "resting" if manager.resting else "awake"))

    async def power(state):
        calls.append(("output", state))
        return output_works

    async def sleep(seconds):
        calls.append(("wait", seconds))

    async def tv(state):
        calls.append(("tv", state))

    with patch("mirrordash_core.features.power.display_power.load_config", return_value=config), \
         patch("mirrordash_core.features.power.privacy.load_config", return_value=config), \
         patch("mirrordash_core.features.power.display_power.manager.broadcast", broadcast), \
         patch("mirrordash_core.features.power.display_power.set_screen_power", power), \
         patch("mirrordash_core.features.power.display_power.set_tv_power", tv), \
         patch("mirrordash_core.features.power.display_power.asyncio.sleep", sleep), \
         patch("mirrordash_core.features.power.display_power.run_in_background", lambda coroutine: coroutine.close()), \
         patch("mirrordash_core.features.modules.client.module_host") as host:
        host.running = []
        asyncio.run(manager.set_state(on))
    return calls


def test_rest_tells_the_page_waits_for_the_fade_then_the_tv_then_the_output():
    assert run_set_state(False) == [("page", "resting"), ("wait", 0.12), ("tv", False), ("output", False)]


def test_wake_tells_the_page_only_when_the_output_is_on():
    assert run_set_state(True, is_on=False, resting=True) == [("output", True), ("page", "awake")]  # nothing told before it is lit
    assert run_set_state(True, is_on=None) == [("output", True)]  # a start: the page is awake already, nothing to tell


def test_an_output_that_wont_go_off_gives_the_cards_back():
    assert run_set_state(False, output_works=False)[-1] == ("page", "awake")


def test_a_start_with_the_screen_off_doesnt_wait_for_a_fade():
    assert run_set_state(False, is_on=None) == [("page", "resting"), ("tv", False), ("output", False)]
