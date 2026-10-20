"""A module's video window (features/video, ARCHITECTURE #34)."""
import asyncio
from unittest.mock import patch

import pytest

from mirrordash_core.features.kiosk.ws import ConnectionManager
from mirrordash_core.features.video import window

BASE_RC = ('<?xml version="1.0"?><labwc_config><mouse><context name="Root"><mousebind button="Right" '
           'action="Press"><action name="None" /></mousebind></context></mouse></labwc_config>')
WLR_RANDR = """HDMI-A-1 "Samsung Electric Company S24D300 0x30353230 (HDMI-A-1)"
  Enabled: yes
  Modes:
    1920x1080 px, 60.000000 Hz (preferred, current)
    1920x1080 px, 50.000000 Hz
    1280x720 px, 50.000000 Hz
  Transform: normal
"""


def test_the_rule_comes_and_goes_without_touching_the_rest():
    placed = window.with_rule(BASE_RC, [1440, 810, 480, 270])
    assert 'identifier="mirrordash-video"' in placed and 'x="1440" y="810"' in placed and 'width="480"' in placed
    assert window.with_rule(window.with_rule(placed, [0, 0, 10, 10]), None) == BASE_RC  # one rule at a time


def test_the_screen_switches_to_50_hz_and_back():
    assert window.refresh_switch(WLR_RANDR, 50) == ("HDMI-A-1", "1920x1080@50Hz", "1920x1080@60Hz")
    assert window.refresh_switch(WLR_RANDR, 60) is None                          # already there
    assert window.refresh_switch(WLR_RANDR.replace("1920x1080 px, 50", "1600x900 px, 50"), 50) is None  # no such mode
    assert window.refresh_switch("", 50) is None                                 # not on a mirror


@pytest.mark.asyncio
async def test_video_window_places_the_player_and_cleans_up(tmp_path):
    """The page says where the card's video window is; the rule, 50 Hz and video_is_playing hold while the
    block runs, and all go when it ends, also by an error or a cancel."""
    rc_xml = tmp_path / "rc.xml"
    rc_xml.write_text(BASE_RC)
    manager = ConnectionManager()
    commands = []

    async def page(message):  # the mirror's page, as core.js answers
        if message.get("type") == "video_window":
            asyncio.get_running_loop().call_soon(window.window_reported, message["module"], [1440, 810, 480, 270])

    async def run(*command):
        commands.append(command)
        return WLR_RANDR if command == ("wlr-randr",) else ""

    with patch.object(window, "manager", manager), patch.object(manager, "broadcast", side_effect=page), \
         patch.object(window, "RC_XML", rc_xml), patch.object(window, "_run", side_effect=run), \
         patch.object(window, "_one_at_a_time", asyncio.Lock()):
        async with window.video_window("tv", hz=50) as placed:
            assert placed == window.VideoWindow("mirrordash-video", [1440, 810, 480, 270], False)
            assert manager.video_players == {"tv"} and 'identifier="mirrordash-video"' in rc_xml.read_text()
            assert ("wlr-randr", "--output", "HDMI-A-1", "--mode", "1920x1080@50Hz") in commands
        assert rc_xml.read_text() == BASE_RC and manager.video_players == set()
        assert commands[-2:] == [("wlr-randr", "--output", "HDMI-A-1", "--mode", "1920x1080@60Hz"),
                                 ("pkill", "-HUP", "-x", "labwc")]

        with pytest.raises(RuntimeError):
            async with window.video_window("tv", fullscreen=True) as placed:
                assert placed.rect is None and placed.fullscreen
                raise RuntimeError("the player failed")
        assert rc_xml.read_text() == BASE_RC and manager.video_players == set()


def test_only_the_mirrors_own_screen_says_where_the_window_is():
    """A phone showing the mirror page must not move the video (kiosk/routes.py checks on_screen)."""
    from types import SimpleNamespace

    from mirrordash_core.features.kiosk.ws import on_screen
    assert on_screen(SimpleNamespace(host="127.0.0.1")) and not on_screen(SimpleNamespace(host="192.168.1.20"))
    loop = asyncio.new_event_loop()
    future = window._asked["tv"] = loop.create_future()
    window.window_reported("tv", [0, 0, -5, 10])  # nonsense counts as no window
    assert future.result() is None
    window._asked.clear()
    loop.close()
