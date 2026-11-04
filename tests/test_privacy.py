"""Privacy mode (features/power/privacy.py, ARCHITECTURE #45): its state, the screen's message, and the
three ways to switch it: the Settings tab, the mirror's button and Home Assistant."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.mqtt import bridge as mqtt
from mirrordash_core.features.mqtt.bridge import MqttBridge
from mirrordash_core.features.power import privacy

SALT = "0123456789abcdef"
AUTH = {"admin_auth": {"hash": hash_password("secret", SALT), "salt": SALT}}


@pytest.fixture
def saved():
    """A config that set_privacy saves to and the screen's broadcast, both faked."""
    config = {"globals": {"privacy_shows": "nothing"}}
    with patch.object(privacy, "load_config", return_value=config), \
         patch.object(privacy, "save_config") as save, \
         patch("mirrordash_core.features.kiosk.ws.manager.broadcast", AsyncMock()) as broadcast:
        yield config, save, broadcast


def test_the_screens_message_with_defaults_for_what_isnt_set_or_known():
    assert privacy.screen_message({}) == {"type": "screen", "privacy": False, "privacy_shows": "monogram",
                                          "cards_over_photo": "see_through", "scheduled_out": [], "guard": 0, "guard_message": None}
    config = {"privacy": {"on": True}, "globals": {"privacy_shows": "nothing", "cards_over_photo": "halo"}}
    assert privacy.screen_message(config) == {"type": "screen", "privacy": True, "privacy_shows": "nothing",
                                              "cards_over_photo": "halo", "scheduled_out": [], "guard": 0, "guard_message": None}
    junk = {"privacy": {"on": "yes"}, "globals": {"privacy_shows": "<b>", "cards_over_photo": "<script>"}}
    assert privacy.screen_message(junk) == privacy.screen_message({})


@pytest.mark.asyncio
async def test_switching_it_saves_it_apart_from_the_settings_form_and_tells_the_screen(saved):
    config, save, broadcast = saved
    await privacy.set_privacy(True)
    assert config["privacy"] == {"on": True} and save.call_count == 1
    broadcast.assert_awaited_once_with({"type": "screen", "privacy": True, "privacy_shows": "nothing",
                                        "cards_over_photo": "see_through", "scheduled_out": [], "guard": 0, "guard_message": None})
    await privacy.set_privacy(True)  # already on: the screen hears it again, the card isn't written again
    assert save.call_count == 1
    await privacy.toggle_privacy()
    assert config["privacy"] == {"on": False}


@pytest.mark.asyncio
async def test_the_buttons_privacy_action_switches_it(saved):
    from mirrordash_core.features.hardware.devices import BUTTON_ACTIONS
    from mirrordash_core.features.hardware.inputs import run_button_action
    assert "privacy" in BUTTON_ACTIONS
    await run_button_action("privacy")
    assert saved[0]["privacy"] == {"on": True}


def test_the_settings_tabs_switch(saved):
    client = TestClient(app)
    with patch("mirrordash_core.admin.load_config", return_value=AUTH):
        assert client.post("/admin/panels/config/privacy", data={"on": "true"}).status_code == 401
        assert client.post("/admin/panels/config/privacy", data={"on": "true"}, headers={"X-API-Key": "secret"}).status_code == 200
    assert saved[0]["privacy"] == {"on": True}


@pytest.mark.asyncio
async def test_home_assistant_sees_it_and_switches_it_when_the_owner_allows_the_screen():
    config = {"mqtt": {"id": "m1", "control_screen": True}, "modules": {}, "privacy": {"on": True}}
    b = MqttBridge()
    with patch.object(mqtt, "load_config", return_value=config):
        assert b._mirror_components(config["mqtt"], {}, {})["privacy"]["command_topic"] == "mirrordash/m1/privacy/set"
        assert "command_topic" not in b._mirror_components({"id": "m1"}, {}, {})["privacy"]  # shown, not switched
        assert b._states(config, cpu=False)["mirrordash/m1/privacy/state"] == "ON"
    with patch.object(mqtt, "load_config", return_value=config), \
         patch.object(privacy, "set_privacy", AsyncMock()) as switch:
        await b._on_message("mirrordash/m1/privacy/set", b"OFF", False)
        switch.assert_awaited_once_with(False)
        await b._on_message("mirrordash/m1/privacy/set", b"maybe", False)
        assert switch.await_count == 1
    config["mqtt"]["control_screen"] = False
    with patch.object(mqtt, "load_config", return_value=config), \
         patch.object(privacy, "set_privacy", AsyncMock()) as switch:
        await b._on_message("mirrordash/m1/privacy/set", b"ON", False)
        assert not switch.await_count


@pytest.mark.asyncio
async def test_a_module_hears_when_its_card_is_hidden_and_shown_again():
    """Privacy mode hides the instances not marked for it, a schedule with no day its own; only the instances
    whose state changed are told, and the module's on_shown runs once per change (the screen counts too)."""
    from mirrordash_core.features.modules.client import ModuleHost
    from mirrordash_core.features.modules.loader import ModuleLoader
    config = {"privacy": {"on": True}, "modules": {"clock": {"show_in_privacy": True}, "radio": {},
                                                   "news": {"schedule": {"days": []}}}}
    assert privacy.hidden_instances(config) == {"radio", "news"}
    assert privacy.hidden_instances({"modules": config["modules"]}) == {"news"}

    host = ModuleHost.__new__(ModuleHost)
    host._hidden, host._instances = {"news"}, {"radio": "r", "news": "n", "clock": "c"}
    host._send_quietly = AsyncMock()
    await host.set_hidden({"radio", "news"})
    host._send_quietly.assert_awaited_once_with("r", {"t": "hidden", "on": True})

    heard = []
    loader = ModuleLoader(link=None)
    loader.instances = {"radio": type("Radio", (), {"on_shown": lambda self, on: heard.append(on)})()}
    for on in (True, True, False, True):
        await loader.shown(on)
    assert heard == [True, False, True]
