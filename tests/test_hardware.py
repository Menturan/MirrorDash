import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core import hardware
from mirrordash_core.app import app
from mirrordash_core.event_bus import event_bus
from mirrordash_core.hardware import PressClassifier


def presses(events, long_press=1.0):
    """Feed (time, 'down'|'up') events, polling every 50 ms like the event loop timers would."""
    c, out, t, i = PressClassifier(long_press), [], 0.0, 0
    while t <= events[-1][0] + 2:
        while i < len(events) and events[i][0] <= t:
            kind = c.down(t) if events[i][1] == "down" else c.up(t)
            out += [kind] if kind else []
            i += 1
        p = c.poll(t)
        out += [p] if p else []
        t = round(t + 0.05, 2)
    return out


def test_press_classifier():
    assert presses([(0, "down"), (0.1, "up")]) == ["single"]
    assert presses([(0, "down"), (0.1, "up"), (0.3, "down"), (0.4, "up")]) == ["double"]
    assert presses([(0, "down"), (0.1, "up"), (0.3, "down"), (0.4, "up"), (0.6, "down"), (0.7, "up")]) == ["triple"]
    assert presses([(0, "down"), (2.0, "up")]) == ["long"]  # fires while held, release adds nothing
    assert presses([(0, "down"), (0.1, "up"), (1.0, "down"), (1.1, "up")]) == ["single", "single"]
    c = PressClassifier(1.0)
    c.down(0.0)
    assert c.up(1.5) == "long"  # release after the threshold, even if poll() never ran


def test_long_press_time_is_chosen():
    assert hardware.DEFAULT_LONG_PRESS == 1.5
    assert presses([(0, "down"), (1.2, "up")], long_press=1.5) == ["single"]
    assert presses([(0, "down"), (1.6, "up")], long_press=1.5) == ["long"]
    assert presses([(0, "down"), (2.5, "up")], long_press=3.0) == ["single"]


def test_validate_devices():
    v = hardware.validate_devices
    assert v([{"type": "button", "pin": 17}, {"type": "pir", "pin": 18}, {"type": "light", "address": "0x23"}]) is None
    assert "already uses GPIO 17" in v([{"type": "button", "pin": 17}, {"type": "dht11", "pin": 17}])
    assert "Only one" in v([{"type": "pir", "pin": 17}, {"type": "pir", "pin": 18}])
    assert v([{"type": "button", "pin": 17}, {"type": "button_2", "pin": 22}]) is None
    assert "already uses GPIO 17" in v([{"type": "button", "pin": 17}, {"type": "button_2", "pin": 17}])
    assert "can't be used" in v([{"type": "button", "pin": 1}])
    assert "needs address" in v([{"type": "light", "address": "0x40"}])
    assert "needed for I²C" in v([{"type": "button", "pin": 3}, {"type": "light", "address": "0x23"}])
    assert "Unknown device type" in v([{"type": "heater", "pin": 12}])
    assert v([{"type": "fan", "pin": 14, "temperature": 60}]) is None
    assert "between 40 and 80" in v([{"type": "pwm_fan", "pin": 18, "temperature": 90}])
    assert "Only one fan" in v([{"type": "fan", "pin": 14, "temperature": 60}, {"type": "pwm_fan", "pin": 18, "temperature": 60}])
    assert hardware.overlay_args([{"type": "fan", "pin": 14, "temperature": 60}]) == ["fan:14:60"]


def test_read_fan_state(tmp_path, monkeypatch):
    fan = tmp_path / "cooling_device0"
    fan.mkdir()
    for f, v in (("type", "pwm-fan"), ("cur_state", "2"), ("max_state", "4")):
        (fan / f).write_text(v + "\n")
    zone = tmp_path / "thermal_zone0"
    zone.mkdir()
    (zone / "temp").write_text("62500\n")
    monkeypatch.setattr(hardware.glob, "glob", lambda pattern: [str(fan)] if "cooling" in pattern else [])
    real_join = os.path.join
    monkeypatch.setattr(hardware.os.path, "join",
                        lambda a, *b: real_join(str(zone), *b) if a == "/sys/class/thermal/thermal_zone0" else real_join(a, *b))
    assert hardware.read_fan_state() == {"level": 2, "max_level": 4, "cpu_temperature_c": 62.5}


def test_sync_only_writes_when_devices_change():
    cfg = {"devices": []}
    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write, \
         patch("mirrordash_core.hardware.kernel_boot_id", return_value="boot-1"):
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (False, None)  # nothing, nothing written
        cfg["devices"] = [{"type": "mmwave", "pin": 22}, {"type": "light", "address": "0x5c"}]
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (True, None)
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (False, None)
    write.assert_awaited_once_with(["mmwave:22", "light:0x5c"])
    assert cfg["gpio_pending_boot_id"] == "boot-1"


def test_read_iio_sensors(tmp_path, monkeypatch):
    for name, files in (("dht11@4", {"in_temp_input": "22500", "in_humidityrelative_input": "41000"}),
                        ("bh1750", {"in_illuminance_raw": "300", "in_illuminance_scale": "0.833333"})):
        d = tmp_path / name
        d.mkdir()
        (d / "name").write_text(name + "\n")
        for f, v in files.items():
            (d / f).write_text(v + "\n")
    monkeypatch.setattr(hardware.glob, "glob", lambda pattern: [str(p) for p in tmp_path.iterdir()] if "iio" in pattern else [])
    assert hardware._read_sensor_blocking("dht11") == {"temperature_c": 22.5, "humidity": 41}
    assert hardware._read_sensor_blocking("light") == {"lux": 250.0}


def _pipe_inputs():
    inputs = hardware.GpioInputs()
    r, w = os.pipe()
    os.set_blocking(r, False)
    inputs.fds["dev"] = r
    return inputs, r, w


def test_gpio_inputs_button_and_presence_events():
    """Raw struct input_event bytes -> button press, combined presence state and events."""
    ev = hardware.GpioInputs.EVENT
    key = lambda name, value: ev.pack(0, 0, 1, hardware.KEYCODES[name], value)

    async def scenario():
        received = []
        handler = lambda data: received.append(data)
        event_bus.subscribe("hardware.motion", handler)
        inputs, r, w = _pipe_inputs()
        dispatched = []
        inputs._dispatch = lambda button, press: press and dispatched.append(press)
        try:
            os.write(w, ev.pack(0, 0, 4, 4, 1234) + key("button", 1) + ev.pack(0, 0, 0, 0, 0))  # MSC, down, SYN
            inputs._on_readable("dev")
            await asyncio.sleep(0.05)
            os.write(w, key("button", 0) + key("pir", 1) + key("mmwave", 1) + key("pir", 0))
            inputs._on_readable("dev")
            still_present = inputs.motion_active  # mmWave still sees someone
            os.write(w, key("mmwave", 0))
            inputs._on_readable("dev")
            await asyncio.sleep(hardware.PressClassifier.MULTI_PRESS_WINDOW + 0.1)
        finally:
            event_bus.unsubscribe("hardware.motion", handler)
            os.close(r)
            os.close(w)
        return dispatched, still_present, inputs.motion_active, received

    dispatched, still_present, present_after, received = asyncio.run(scenario())
    assert dispatched == ["single"]
    assert still_present is True and present_after is False
    assert received == [{"motion": True, "sensor": "pir"}, {"motion": True, "sensor": "mmwave"},
                        {"motion": True, "sensor": "pir"}, {"motion": False, "sensor": "mmwave"}]


def test_sensor_readings_are_published():
    async def scenario():
        received = []
        handlers = {e: (lambda e: lambda data: received.append((e, data)))(e) for e in ("hardware.climate", "hardware.light")}
        for e, h in handlers.items():
            event_bus.subscribe(e, h)
        inputs = hardware.GpioInputs()
        readings = {"dht11": {"temperature_c": 21.5, "humidity": 40}, "light": {"lux": 120.0}}
        cfg = {"system": {"devices": [{"type": "dht11", "pin": 4}, {"type": "light", "address": "0x23"}]}}
        try:
            with patch("mirrordash_core.hardware.load_config", return_value=cfg), \
                 patch("mirrordash_core.hardware.read_sensor", new_callable=AsyncMock, side_effect=lambda t: readings[t]):
                task = asyncio.create_task(inputs._publish_sensors())
                await asyncio.sleep(0.02)
                task.cancel()
        finally:
            for e, h in handlers.items():
                event_bus.unsubscribe(e, h)
        return received

    assert asyncio.run(scenario()) == [("hardware.climate", {"temperature_c": 21.5, "humidity": 40}),
                                       ("hardware.light", {"lux": 120.0})]


@pytest.fixture
def client():
    from mirrordash_core.api.admin_shared import require_api_key
    app.dependency_overrides[require_api_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _message(response) -> str:
    return json.loads(response.headers["HX-Trigger-After-Swap"])["md-notify"]["message"]


@patch("mirrordash_core.api.admin_system_panels.save_config")
@patch("mirrordash_core.api.admin_system_panels.load_config")
def test_add_and_remove_devices(mock_load, mock_save, client):
    mock_load.return_value = {"system": {"devices": [{"type": "button", "pin": 3, "actions": {}}], "gpio_overlays": ["button:3"]}}

    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write:
        r = client.post("/admin/panels/system/devices/add", data={"type": "light", "address": "0x23"})
        assert "needed for I²C" in _message(r) and 'id="devices-card"' in r.text  # card stays on errors
        write.assert_not_called()
        mock_save.assert_not_called()

        r = client.post("/admin/panels/system/devices/add", data={"type": "mmwave", "pin": "22"})
        assert "added" in _message(r) and "Restart the mirror" in _message(r)
        write.assert_awaited_once_with(["button:3", "mmwave:22"])
        assert mock_save.call_args[0][0]["system"]["devices"][-1] == {"type": "mmwave", "pin": 22}
        assert "mmWave presence sensor" in r.text
        assert "Push button 2" in r.text and "Push button 3" not in r.text  # one more button at a time

    mock_load.return_value = mock_save.call_args[0][0]
    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write:
        r = client.post("/admin/panels/system/devices/remove", data={"type": "button"})
    assert "removed" in _message(r)
    write.assert_awaited_once_with(["mmwave:22"])


@patch("mirrordash_core.api.admin_system_panels.save_config")
@patch("mirrordash_core.api.admin_system_panels.load_config")
def test_button_actions(mock_load, mock_save, client):
    mock_load.return_value = {"system": {"devices": [{"type": "button", "pin": 17, "actions": {}},
                                                     {"type": "button_2", "pin": 22, "actions": {}}]}}
    url = "/admin/panels/system/devices/button-actions"
    assert "Unknown button action" in _message(client.post(url, data={"action_long": "rm -rf"}))
    assert "how long" in _message(client.post(url, data={"long_press": "0.1"}))
    assert "isn't connected" in _message(client.post(url, data={"type": "button_3"}))
    r = client.post(url, data={"action_long": "shutdown", "long_press": "2.0"})
    assert _message(r) == "Saved."
    saved = mock_save.call_args[0][0]["system"]["devices"][0]
    assert saved["actions"] == {"single": "none", "double": "none", "triple": "none", "long": "shutdown"}
    assert saved["long_press"] == 2.0
    client.post(url, data={"type": "button_2", "action_single": "wake", "long_press": "3"})
    assert mock_save.call_args[0][0]["system"]["devices"][1]["actions"]["single"] == "wake"
    assert mock_save.call_args[0][0]["system"]["devices"][1]["long_press"] == 3.0


def test_device_problem_does_not_block_other_settings():
    """An old OS image (no helper) must never stop the display settings from saving."""
    from mirrordash_core.api.admin_system import update_system_settings
    cfg = {"system": {"devices": [{"type": "button", "pin": 23}], "display_control": {"mode": "wake"}}}
    with patch("mirrordash_core.api.admin_system.load_config", return_value=cfg), \
         patch("mirrordash_core.api.admin_system.save_config") as save, \
         patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock), \
         patch("mirrordash_core.hardware.os.path.exists", return_value=False):
        asyncio.run(update_system_settings(settings={"brightness": 40}))
        asyncio.run(update_system_settings(settings={"display_control": {"mode": "wake"}}))
    assert save.call_count == 2


def test_each_button_has_its_own_presses_and_actions():
    ev = hardware.GpioInputs.EVENT
    key = lambda name, value: ev.pack(0, 0, 1, hardware.KEYCODES[name], value)
    cfg = {"system": {"devices": [
        {"type": "button", "pin": 17, "actions": {"single": "wake"}},
        {"type": "button_2", "pin": 22, "actions": {"single": "toggle_display"}, "long_press": 1.0}]}}

    async def scenario():
        received = []
        handler = lambda data: received.append(data)
        event_bus.subscribe("hardware.button", handler)
        inputs, r, w = _pipe_inputs()
        try:
            with patch("mirrordash_core.hardware.load_config", return_value=cfg), \
                 patch("mirrordash_core.hardware.run_button_action", new_callable=AsyncMock):
                os.write(w, key("button", 1) + key("button_2", 1))  # both held at once
                inputs._on_readable("dev")
                await asyncio.sleep(0.05)
                os.write(w, key("button", 0))
                inputs._on_readable("dev")
                await asyncio.sleep(1.1)  # button_2 is still held: past its 1 s long press
                os.write(w, key("button_2", 0))
                inputs._on_readable("dev")
                await asyncio.sleep(hardware.PressClassifier.MULTI_PRESS_WINDOW + 0.1)
        finally:
            event_bus.unsubscribe("hardware.button", handler)
            os.close(r)
            os.close(w)
        return received, inputs.classifiers["button_2"].long_press

    received, long_press = asyncio.run(scenario())
    assert sorted(received, key=lambda e: e["button"]) == [
        {"press": "single", "action": "wake", "button": "button"},
        {"press": "long", "action": "none", "button": "button_2"}]
    assert long_press == 1.0
