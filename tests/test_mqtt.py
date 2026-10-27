"""MQTT (ARCHITECTURE #42): what a module may declare, what a command may carry, and that nothing runs
that the owner didn't turn on. MQTT says nothing about the sender, so these checks are the boundary."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from mirrordash_core.features.mqtt import bridge as mqtt
from mirrordash_core.features.mqtt.bridge import (
    MqttBridge,
    command_value,
    declarations,
    module_components,
    state_payload,
)

TV = {"exposes": {
    "now_playing": {"type": "sensor", "title": "Now playing"},
    "play_url": {"type": "text", "format": "url", "maxLength": 9999, "control": True},
    "channel": {"type": "select", "options": ["SVT1", "SVT2"], "control": True},
    "volume": {"type": "number", "min": 0, "max": 100, "control": True},
    "power": {"type": "switch", "control": True},
    "stop": {"type": "button", "control": True},
    "ding": {"type": "event", "event_types": ["ring"]},
    "secret": {"type": "sensor", "control": True},  # a sensor takes no commands, whatever it says
    "bad/topic": {"type": "sensor"}, "#": {"type": "sensor"}, "+": {"type": "switch", "control": True},
    "shell": {"type": "exec"}, "empty": {"type": "select", "options": []},
    "nan": {"type": "number", "min": "nan"}, "unit": {"type": "sensor", "unit": "°C\nx"},
}}


def test_a_module_declares_only_what_it_may():
    found = declarations(TV)
    assert set(found) == {"now_playing", "play_url", "channel", "volume", "power", "stop", "ding", "secret", "unit"}
    assert found["play_url"]["max"] == 255 and found["play_url"]["url"]  # Home Assistant's text limit
    assert not found["secret"]["control"] and not found["now_playing"]["control"]
    assert "unit" not in found["unit"]  # a unit with a line break is left out
    assert declarations({"exposes": {f"e{i}": {"type": "sensor"} for i in range(100)}}).keys() == {f"e{i}" for i in range(32)}
    assert declarations(None) == declarations({"exposes": "x"}) == {}


def test_a_command_carries_only_what_its_entity_takes():
    e = declarations(TV)
    assert command_value(e["play_url"], "https://example.com/v.m3u8") == "https://example.com/v.m3u8"
    assert command_value(e["channel"], "SVT2") == "SVT2"
    assert command_value(e["volume"], "40") == 40 and command_value(e["power"], "ON") is True
    assert command_value(e["stop"], "PRESS") is None
    for entity, payload in (("play_url", "javascript:alert(1)"), ("play_url", "file:///etc/passwd"),
                            ("play_url", "https://a.b/" + "x" * 300), ("play_url", "https://a.b/\nx"),
                            ("channel", "SVT3"), ("volume", "101"), ("volume", "nan"), ("volume", "inf"),
                            ("power", "on"), ("stop", "ON")):
        with pytest.raises(ValueError):
            command_value(e[entity], payload)


def test_a_modules_state_is_published_only_as_its_entity():
    e = declarations(TV)
    assert state_payload(e["now_playing"], "News") == "News" and state_payload(e["now_playing"], "x" * 256) is None
    assert state_payload(e["power"], True) == "ON" and state_payload(e["power"], "yes") is None
    assert state_payload(e["ding"], "ring") == '{"event_type": "ring"}' and state_payload(e["ding"], "boom") is None
    assert state_payload(e["channel"], "SVT9") is None and state_payload(e["volume"], float("nan")) is None
    assert state_payload(e["now_playing"], {"a": 1}) is None


def test_what_the_owner_didnt_allow_is_shown_not_controlled():
    comps = module_components("mirrordash/m1", "m1", "tv", declarations(TV), {"channel"})
    assert comps["channel"]["platform"] == "select" and comps["channel"]["command_topic"] == "mirrordash/m1/m/tv/channel/set"
    assert comps["play_url"]["platform"] == "sensor" and "command_topic" not in comps["play_url"]
    assert comps["power"]["platform"] == "binary_sensor" and "stop" not in comps  # a button nobody may press is left out


def _bridge(cfg, modules=None):
    b = MqttBridge()
    b._modules = {"tv": declarations(TV)}
    config = {"mqtt": {"id": "m1", **cfg}, "modules": modules or {"tv": {"module": "tv"}}}
    return b, patch.object(mqtt, "load_config", return_value=config)


@pytest.mark.asyncio
async def test_nothing_runs_that_the_owner_didnt_turn_on():
    screen, enabled, control = AsyncMock(), AsyncMock(), AsyncMock(return_value=True)
    with patch("mirrordash_core.features.power.routes.update_screen_state", screen), \
         patch("mirrordash_core.features.modules.routes.set_instances_enabled", enabled), \
         patch("mirrordash_core.features.modules.client.module_host.control", control):
        b, cfg = _bridge({"modules": {"tv": {"share": True, "control": ["channel"]}}})
        with cfg:
            for topic, payload in (("screen/set", b"OFF"), ("enabled/tv/set", b"OFF"), ("m/tv/play_url/set", b"https://a.b"),
                                   ("m/tv/now_playing/set", b"x"), ("m/other/channel/set", b"SVT1"), ("../screen/set", b"OFF")):
                await b._on_message(f"mirrordash/m1/{topic}", payload, False)
            await b._on_message("mirrordash/m2/screen/set", b"OFF", False)  # another mirror's
            await b._apply_pending(1e9)
            assert not screen.await_count and not enabled.await_count and not control.await_count
            await b._on_message("mirrordash/m1/m/tv/channel/set", b"SVT2", False)
            control.assert_awaited_once_with("tv", "channel", "SVT2")
            await b._on_message("mirrordash/m1/m/tv/channel/set", b"SVT3", False)  # not an option
            assert control.await_count == 1
            control.side_effect = ConnectionError("the module went away")  # never ends the connection
            await b._on_message("mirrordash/m1/m/tv/channel/set", b"SVT1", False)

        b, cfg = _bridge({"control_screen": True, "control_modules": True})
        with cfg:
            await b._on_message("mirrordash/m1/screen/set", b"OFF", True)  # retained: replayed at a reconnect
            assert not screen.await_count
            await b._on_message("mirrordash/m1/screen/set", b"OFF", False)
            screen.assert_awaited_once_with({"state": "off"})
            await b._on_message("mirrordash/m1/enabled/gone/set", b"OFF", False)  # no such instance
            b._commands.clear()
            for _ in range(4):  # flipped back and forth: one reload, with the last of each
                await b._on_message("mirrordash/m1/enabled/tv/set", b"ON", False)
                await b._on_message("mirrordash/m1/enabled/tv/set", b"OFF", False)
            b._commands.clear()  # the ones above count against COMMAND_RATE (tested on its own below)
            for payload in (b"5", b"\xc2\xb2", b"40", b"55"):  # below the screen's 10, a superscript 2, then a slider
                await b._on_message("mirrordash/m1/brightness/set", payload, False)
            with patch("mirrordash_core.system_settings.update_system_settings", AsyncMock()) as brightness:
                await b._apply_pending(1e9)
                await b._apply_pending(1e9 + 0.5)
                brightness.assert_awaited_once_with(settings={"brightness": 55})
            enabled.assert_awaited_once_with({"tv": False})
            await b._on_message("mirrordash/m1/enabled/tv/set", b"ON", False)
            await b._apply_pending(1e9 + 2)  # within MODULE_CHANGE_INTERVAL of the last change: waits
            assert enabled.await_count == 1
            await b._apply_pending(1e9 + mqtt.MODULE_CHANGE_INTERVAL)
            enabled.assert_awaited_with({"tv": True})


@pytest.mark.asyncio
async def test_a_flood_of_commands_is_cut_to_the_rate_and_logs_one_line(caplog):
    screen = AsyncMock()
    with patch("mirrordash_core.features.power.routes.update_screen_state", screen):
        b, cfg = _bridge({"control_screen": True})
        with cfg, caplog.at_level("WARNING", logger="mirrordash.core.mqtt"):
            await asyncio.gather(*(b._on_message("mirrordash/m1/screen/set", b"ON", False) for _ in range(50)))
            await b._on_message("mirrordash/m1/enabled/x\nINFO mirrordash.core.auth: forged/set", b"ON", False)
    assert screen.await_count == mqtt.COMMAND_RATE
    assert len(caplog.records) == 1  # refused ones at most every 10 s, with a count
    assert "\n" not in caplog.text.strip()  # a topic's line break is shown, never a line of its own


@pytest.mark.asyncio
async def test_a_packet_over_the_limit_ends_the_connection_unread():
    """paho would read a packet of any size (up to 256 MB) whatever MaximumPacketSize says; a broker that
    ignores it, or someone in between on plain MQTT, ends the connection instead (guard_packet_size)."""
    import aiomqtt

    async def broker(reader, writer):
        await reader.read(1024)  # CONNECT
        writer.write(b"\x20\x03\x00\x00\x00")  # CONNACK
        writer.write(b"\x30\x80\x80\x40\x00\x01t")  # PUBLISH of 1 MB (remaining length 0x100000)
        sent = 0
        try:
            while sent < 1024 * 1024:
                writer.write(b"x" * 65536)
                await writer.drain()
                sent += 65536
        except ConnectionError:
            pass
        received.append(sent)

    received = []
    server = await asyncio.start_server(broker, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    b = MqttBridge()
    with patch.object(mqtt, "load_config", return_value={"mqtt": {"id": "m1", "client_id": "secret-id"}}):
        client = b._client_for({"host": "127.0.0.1", "port": port, "id": "m1", "client_id": "secret-id"})
    assert client._client._client_id == b"secret-id"  # not the id every topic shows
    with pytest.raises(aiomqtt.MqttError):
        async with client:
            async with asyncio.timeout(5):
                async for _ in client.messages:
                    pass
    server.close()
    assert client._client._in_packet["remaining_length"] == 1024 * 1024
    assert len(client._client._in_packet["packet"]) == 0  # not one byte of it was kept


@pytest.mark.asyncio
async def test_a_modules_state_reaches_mqtt_only_when_shared_and_declared():
    b, cfg = _bridge({})
    with cfg:
        b.module_state("tv", "now_playing", "News")
        b.module_state("tv", "undeclared", "x")
        b.module_state("other", "now_playing", "x")
        b.module_state("tv", "ding", "ring")
    assert b._pending == {"mirrordash/m1/m/tv/now_playing/state": ("News", True),
                          "mirrordash/m1/m/tv/ding/state": ('{"event_type": "ring"}', False)}  # an event isn't retained


@pytest.mark.asyncio
async def test_what_a_module_no_longer_shares_leaves_nothing_on_the_broker():
    """Retained values stay on the broker for any client to read: a module the owner stops sharing has its
    device removed and its values cleared, a control allowed later replaces its sensor (Home Assistant never
    changes a component's platform), and turning MQTT off clears everything the mirror left."""
    from types import SimpleNamespace
    sent = []

    class Broker:
        async def publish(self, topic, payload, qos=0, retain=False, **_):
            sent.append((topic, payload.decode() if isinstance(payload, bytes) else payload, retain))

    shared = {"id": "m1", "modules": {"tv": {"share": True, "control": []}}}
    config = {"mqtt": shared, "modules": {"tv": {"module": "tv"}}, "system": {}}
    b = MqttBridge()
    with patch.object(mqtt, "load_config", return_value=config), \
         patch("mirrordash_core.features.modules.loader.find_entry_point", return_value=SimpleNamespace()), \
         patch("mirrordash_core.features.settings.schema.get_module_schema", return_value=TV):
        await b._discover(Broker(), config, 1000.0)
        b.module_state("tv", "now_playing", "Secret meeting")
        await b._publish(Broker(), *next(iter((t, v[0]) for t, v in b._pending.items())))
        topic = "homeassistant/device/m1/tv/config"
        assert json.loads(sent[-2][1])["components"]["play_url"]["platform"] == "sensor"

        shared["modules"]["tv"]["control"] = ["play_url"]
        await b._discover(Broker(), config, 1001.0)
        stub, now = (json.loads(p) for t, p, _ in sent[-2:])
        assert stub["components"]["play_url"] == {"platform": "sensor"} and now["components"]["play_url"]["platform"] == "text"

        b.module_state("tv", "now_playing", "Another secret")  # sent just before the owner stops sharing
        shared["modules"]["tv"]["share"] = False
        await b._discover(Broker(), config, 1002.0)
        assert (topic, "", True) in sent and ("mirrordash/m1/m/tv/now_playing/state", "", True) in sent
        assert not b._pending

        b._client = Broker()
        b._published = {"mirrordash/m1/screen/state": "ON"}
        assert await b.forget()
    assert ("mirrordash/m1/screen/state", "", True) in sent and sent[-1] == ("mirrordash/m1/status", "offline", True)


def test_the_card_keeps_the_saved_password_and_checks_the_broker():
    from fastapi.testclient import TestClient

    from mirrordash_core.admin import require_api_key
    from mirrordash_core.app import app
    from mirrordash_core.features.mqtt import routes
    saved = {}
    config = {"mqtt": {"id": "m1", "password": "s3cr3t-pw"}, "modules": {}}
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with patch.object(routes, "load_config", return_value=config) as load, \
             patch.object(routes, "save_config", side_effect=lambda c: saved.update(c)), \
             patch.object(routes.mqtt_bridge, "restart", AsyncMock()):
            post = lambda form: TestClient(app).post("/admin/panels/system/mqtt/save", data=form).status_code  # noqa: E731
            assert post({"host": "ha.local; rm -rf /"}) == 400
            assert post({"host": "ha.local", "password": "a\nb"}) == 400
            assert post({"host": "ha.local", "username": "mirror"}) == 200
            load.return_value = saved
            card = TestClient(app).get("/admin/panels/system/mqtt").text
            # The lines to lock the broker down, for this mirror's id; the password never goes back to the page
            assert "user mirror\ntopic write mirrordash/m1/#" in card and "s3cr3t-pw" not in card
    finally:
        app.dependency_overrides.clear()
    assert saved["mqtt"]["password"] == "s3cr3t-pw" and saved["mqtt"]["id"] == "m1"
    assert not saved["mqtt"]["control_screen"] and not saved["mqtt"]["control_modules"]  # read-only unless ticked


@pytest.mark.asyncio
async def test_a_plain_backup_leaves_the_broker_password_out_and_a_restore_keeps_this_mirrors_id(tmp_path):
    from mirrordash_core.features.backup import service
    (tmp_path / "config.json").write_text(json.dumps({"mqtt": {"id": "other", "host": "h", "enabled": True}}))
    saved = {}
    with patch.object(service, "load_config", return_value={"mqtt": {"id": "mine"}}), \
         patch.object(service, "save_config", side_effect=lambda c: saved.update(c)), \
         patch.object(service, "sync_gpio_overlays", AsyncMock(return_value=(False, None))):
        await service._restore_config(tmp_path)
    assert saved["mqtt"]["id"] == "mine"
