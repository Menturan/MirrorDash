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
    mock_load.return_value = {"system": {"display_control": {"mode": "pir", "pir": {"pin": 18}}}}
    def message(form):
        return client.post("/admin/panels/system/gpio", data=form).headers["HX-Trigger-After-Swap"]

    assert "same GPIO" in message({"button_pin": "17", "dht11_pin": "17"})
    assert "already used by the motion sensor" in message({"button_pin": "18"})
    assert "can't be used" in message({"button_pin": "1"})
    assert "Unknown button action" in message({"action_single": "rm -rf"})
    mock_save.assert_not_called()

    with patch("mirrordash_core.hardware.write_gpio_overlays", new_callable=AsyncMock, return_value=None) as write, \
         patch("mirrordash_core.api.admin_system_panels.remount_rw", new_callable=AsyncMock), \
         patch("mirrordash_core.api.admin_system_panels.remount_ro", new_callable=AsyncMock):
        r = client.post("/admin/panels/system/gpio", data={"button_pin": "17", "dht11_pin": "4", "action_long": "shutdown"})
    assert "Restart the mirror" in r.headers["HX-Trigger-After-Swap"]
    write.assert_awaited_once_with(17, 4)
    saved = mock_save.call_args[0][0]["system"]
    assert saved["button"] == {"pin": 17, "actions": {"single": "none", "double": "none", "triple": "none", "long": "shutdown"}}
    assert saved["dht11"] == {"pin": 4}


def test_button_manager_parses_evdev_events():
    """Raw struct input_event bytes -> press -> configured action."""
    import asyncio
    import os

    async def scenario():
        mgr = hardware.ButtonManager()
        r, w = os.pipe()
        os.set_blocking(r, False)
        mgr.fd = r
        dispatched = []
        mgr._dispatch = lambda press: press and dispatched.append(press)
        ev = hardware.ButtonManager.EVENT
        os.write(w, ev.pack(0, 0, 4, 4, 1234) + ev.pack(0, 0, 1, hardware.BUTTON_KEYCODE, 1)  # MSC_SCAN, key down
                 + ev.pack(0, 0, 0, 0, 0))                                                       # SYN_REPORT
        mgr._on_readable()
        await asyncio.sleep(0.05)
        os.write(w, ev.pack(0, 0, 1, hardware.BUTTON_KEYCODE, 0))  # key up
        mgr._on_readable()
        await asyncio.sleep(hardware.PressClassifier.MULTI_PRESS_WINDOW + 0.1)
        os.close(r)
        os.close(w)
        return dispatched

    assert asyncio.run(scenario()) == ["single"]
