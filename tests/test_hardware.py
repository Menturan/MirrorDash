from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core import hardware
from mirrordash_core.app import app
from mirrordash_core.config import migrate_config
from mirrordash_core.hardware import PressClassifier


def presses(events):
    """Feed (time, 'down'|'up') events, polling every 50 ms like the event loop timers would."""
    c, out, t, i = PressClassifier(), [], 0.0, 0
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
    # long fires while still held, and the release afterwards adds nothing
    assert presses([(0, "down"), (2.0, "up")]) == ["long"]
    # two presses further apart than the window are two singles
    assert presses([(0, "down"), (0.1, "up"), (1.0, "down"), (1.1, "up")]) == ["single", "single"]
    # a release after the threshold counts as long even if poll() never ran in between
    c = PressClassifier()
    c.down(0.0)
    assert c.up(1.5) == "long"


def test_read_dht11_from_sysfs(tmp_path, monkeypatch):
    dev = tmp_path / "iio:device0"
    dev.mkdir()
    (dev / "name").write_text("dht11@4\n")
    (dev / "in_temp_input").write_text("22500\n")
    (dev / "in_humidityrelative_input").write_text("41000\n")
    monkeypatch.setattr(hardware.glob, "glob", lambda pattern: [str(dev)] if "iio" in pattern else [])
    assert hardware._read_dht11_blocking() == {"temperature_c": 22.5, "humidity": 41}


def test_old_display_button_mode_is_migrated():
    cfg = {"system": {"display_control": {"mode": "button", "button": {"pin": 23}}}, "modules": {}}
    assert migrate_config(cfg)
    assert cfg["system"]["display_control"] == {"mode": "manual"}
    assert cfg["system"]["button"]["pin"] == 23
    assert cfg["system"]["button"]["actions"]["single"] == "toggle_display"


@pytest.fixture
def client():
    from mirrordash_core.api.admin_shared import require_api_key
    app.dependency_overrides[require_api_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@patch("mirrordash_core.api.admin_system_panels.save_config")
@patch("mirrordash_core.api.admin_system_panels.load_config")
def test_gpio_settings_validation(mock_load, mock_save, client):
    mock_load.return_value = {"system": {"display_control": {"mode": "pir", "pir": {"pin": 18}},
                                         "gpio_overlays": [None, None, 18]}}

    def message(form):
        return client.post("/admin/panels/system/gpio", data=form).headers["HX-Trigger-After-Swap"]

    assert "same GPIO" in message({"button_pin": "17", "dht11_pin": "17"})
    assert "motion sensor can't use the same GPIO" in message({"button_pin": "18"})
    assert "can't be used" in message({"button_pin": "1"})
    assert "Unknown button action" in message({"action_single": "rm -rf"})
    mock_save.assert_not_called()

    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write, \
         patch("mirrordash_core.hardware.kernel_boot_id", return_value="boot-1"), \
         patch("mirrordash_core.api.admin_system_panels.remount_rw", new_callable=AsyncMock), \
         patch("mirrordash_core.api.admin_system_panels.remount_ro", new_callable=AsyncMock):
        r = client.post("/admin/panels/system/gpio", data={"button_pin": "17", "dht11_pin": "4", "action_long": "shutdown"})
    assert "Restart the mirror" in r.headers["HX-Trigger-After-Swap"]
    write.assert_awaited_once_with(17, 4, 18)  # the PIR pin from the Power tab is kept
    saved = mock_save.call_args[0][0]["system"]
    assert saved["button"] == {"pin": 17, "actions": {"single": "none", "double": "none", "triple": "none", "long": "shutdown"}}
    assert saved["dht11"] == {"pin": 4}
    assert saved["gpio_overlays"] == [17, 4, 18] and saved["gpio_pending_boot_id"] == "boot-1"


def test_sync_only_writes_when_pins_change():
    import asyncio
    cfg = {"display_control": {"mode": "manual", "pir": {"pin": 18}}}  # nothing ever written
    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write:
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (False, None)  # PIR pin only counts in PIR mode
        cfg["display_control"]["mode"] = "pir"
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (True, None)
        assert asyncio.run(hardware.sync_gpio_overlays(cfg)) == (False, None)
    write.assert_awaited_once_with(None, None, 18)


def test_gpio_inputs_parse_button_and_motion_events():
    """Raw struct input_event bytes -> button press / PIR motion state."""
    import asyncio
    import os

    async def scenario():
        inputs = hardware.GpioInputs()
        r, w = os.pipe()
        os.set_blocking(r, False)
        inputs.fds["dev"] = r
        dispatched = []
        inputs._dispatch = lambda press: press and dispatched.append(press)
        ev = hardware.GpioInputs.EVENT
        os.write(w, ev.pack(0, 0, 4, 4, 1234) + ev.pack(0, 0, 1, hardware.BUTTON_KEYCODE, 1)  # MSC_SCAN, key down
                 + ev.pack(0, 0, 0, 0, 0))                                                       # SYN_REPORT
        inputs._on_readable("dev")
        await asyncio.sleep(0.05)
        os.write(w, ev.pack(0, 0, 1, hardware.BUTTON_KEYCODE, 0) + ev.pack(0, 0, 1, hardware.PIR_KEYCODE, 1))
        inputs._on_readable("dev")
        motion_during = inputs.motion_active
        await asyncio.sleep(hardware.PressClassifier.MULTI_PRESS_WINDOW + 0.1)
        os.write(w, ev.pack(0, 0, 1, hardware.PIR_KEYCODE, 0))
        inputs._on_readable("dev")
        os.close(r)
        os.close(w)
        return dispatched, motion_during, inputs.motion_active, inputs.last_motion_at

    dispatched, motion_during, motion_after, last_motion_at = asyncio.run(scenario())
    assert dispatched == ["single"]
    assert motion_during is True and motion_after is False and last_motion_at > 0


def test_gpio_problem_does_not_block_other_settings():
    """Old OS image (no helper) + a migrated button pin: brightness must still save."""
    import asyncio
    from mirrordash_core.api.admin_system import update_system_settings
    cfg = {"system": {"button": {"pin": 23}, "display_control": {"mode": "pir", "pir": {"pin": 18}}}}
    with patch("mirrordash_core.api.admin_system.load_config", return_value=cfg), \
         patch("mirrordash_core.api.admin_system.save_config") as save, \
         patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock), \
         patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock), \
         patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock), \
         patch("mirrordash_core.hardware.os.path.exists", return_value=False):  # helper missing
        res = asyncio.run(update_system_settings(settings={"brightness": 40}))
        assert res["gpio_error"] is None and save.called  # Hardware tab: pins not touched
        res = asyncio.run(update_system_settings(settings={"display_control": {"mode": "pir"}}))
        assert "too old" in res["gpio_error"] and save.call_count == 2  # Power tab: saved, warned
