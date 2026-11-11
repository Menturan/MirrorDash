from datetime import datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.features.power.display_power import (
    DisplayPowerManager,
    is_scheduled_out,
    local_now,
    scheduled_out,
)


@pytest.fixture
def client():
    return TestClient(app)

def test_time_in_range_normal():
    manager = DisplayPowerManager()
    
    # 07:00 to 22:00
    assert manager._is_time_in_range("07:00", "22:00", time(8, 0)) is True
    assert manager._is_time_in_range("07:00", "22:00", time(21, 59)) is True
    assert manager._is_time_in_range("07:00", "22:00", time(6, 59)) is False
    assert manager._is_time_in_range("07:00", "22:00", time(22, 1)) is False
    assert manager._is_time_in_range("07:00", "22:00", time(7, 0)) is True
    assert manager._is_time_in_range("07:00", "22:00", time(22, 0)) is True

def test_time_in_range_crossover():
    manager = DisplayPowerManager()
    
    # 22:00 to 06:00 (over midnight)
    assert manager._is_time_in_range("22:00", "06:00", time(23, 0)) is True
    assert manager._is_time_in_range("22:00", "06:00", time(2, 0)) is True
    assert manager._is_time_in_range("22:00", "06:00", time(5, 59)) is True
    assert manager._is_time_in_range("22:00", "06:00", time(6, 1)) is False
    assert manager._is_time_in_range("22:00", "06:00", time(21, 59)) is False
    assert manager._is_time_in_range("22:00", "06:00", time(22, 0)) is True
    assert manager._is_time_in_range("22:00", "06:00", time(6, 0)) is True

def test_time_in_range_invalid():
    manager = DisplayPowerManager()
    # Should default to True on parse error to avoid locking screen permanently
    assert manager._is_time_in_range("invalid", "22:00", time(8, 0)) is True

@patch("mirrordash_core.features.updates.service.load_config")
@patch("mirrordash_core.system_settings.save_config")
@patch("mirrordash_core.system_settings.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock)
@patch("mirrordash_core.features.settings.ssh.get_ssh_status", new_callable=AsyncMock)
def test_system_settings_display_control_validation(
    mock_get_ssh, mock_set_ssh, mock_apply, mock_save, mock_load, client
):
    mock_load.return_value = {
        "admin_auth": {"hash": "dummy", "salt": "dummy"}
    }
    
    # Mock require_api_key dependency to bypass authentication in testing
    from mirrordash_core.admin import require_api_key
    app.dependency_overrides[require_api_key] = lambda: None
    
    # Valid payload
    payload = {
        "rotation": "normal",
        "resolution": "auto",
        "brightness": 100,
        "volume": 80,
        "ssh": False,
        "display_control": {
            "mode": "interval",
            "interval": {"start": "07:00", "end": "22:00"},
            "pir": {"pin": 18, "timeout_minutes": 5},
            "button": {"pin": 23}
        }
    }
    
    headers = {"X-API-Key": "secret"}
    
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 200
    mock_save.assert_called_once()
    
    # Reset mock
    mock_save.reset_mock()
    
    # Invalid display control mode
    payload["display_control"]["mode"] = "invalid_mode"
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid display power mode" in response.json()["detail"]
    
    # Invalid time format for interval mode
    payload["display_control"]["mode"] = "interval"
    payload["display_control"]["interval"]["start"] = "7:00" # missing leading zero
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid interval time format" in response.json()["detail"]
    
    # Invalid screen timeout for waking the screen
    payload["display_control"]["mode"] = "wake"
    payload["display_control"]["wake"] = {"timeout_minutes": 0}  # too small
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "screen timeout" in response.json()["detail"]

    # The old display "button" mode is gone; the GPIO button has its own settings now
    payload["display_control"]["mode"] = "button"
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid display power mode" in response.json()["detail"]
    
    # Clear overrides
    app.dependency_overrides.clear()

def test_local_now_uses_the_configured_timezone():
    assert local_now({"globals": {"timezone": "America/New_York"}}).tzinfo.key == "America/New_York"


def test_a_module_schedule_hides_it_outside_its_days_and_times():
    friday = datetime(2026, 11, 6)  # a Friday
    at = lambda day, hour, minute=0: day.replace(hour=hour, minute=minute)
    weekdays = {"days": ["mon", "tue", "wed", "thu", "fri"], "from": "06:00", "to": "09:00"}
    assert not is_scheduled_out(weekdays, at(friday, 6))
    assert not is_scheduled_out(weekdays, at(friday, 8, 59))
    assert is_scheduled_out(weekdays, at(friday, 9))  # to is when it goes
    assert is_scheduled_out(weekdays, at(friday, 5, 59))
    assert is_scheduled_out(weekdays, at(friday + timedelta(days=1), 7))  # Saturday
    # Over midnight: belongs to the day it starts
    friday_night = {"days": ["fri"], "from": "22:00", "to": "02:00"}
    assert not is_scheduled_out(friday_night, at(friday, 23))
    assert not is_scheduled_out(friday_night, at(friday + timedelta(days=1), 1))  # Saturday 01:00
    assert is_scheduled_out(friday_night, at(friday, 1))  # Friday 01:00 is Thursday's night
    assert is_scheduled_out(friday_night, at(friday, 12))
    # Blank times are midnight; no days ticked hides it; none saved or unreadable shows it
    assert not is_scheduled_out({"days": ["fri"], "from": "", "to": ""}, at(friday, 23, 59))
    assert not is_scheduled_out({"from": "", "to": "18:00"}, at(friday, 0))
    assert is_scheduled_out({"days": [], "from": "", "to": ""}, at(friday, 12))
    assert not is_scheduled_out(None, at(friday, 12))
    assert not is_scheduled_out({"from": "noon"}, at(friday, 12))
    config = {"modules": {"news": {"schedule": weekdays}, "clock": {}}}
    assert scheduled_out(config, at(friday, 12)) == ["news"]


def test_validate_config_checks_a_module_schedule():
    from mirrordash_core.features.modules.schema import validate_config
    validate_config({"modules": {"clock": {"schedule": {"days": ["mon"], "from": "06:00", "to": ""}}}})
    for bad in ({"days": ["monday"]}, {"from": "6:00"}, {"to": 900}, {"days": [{}]}, "always"):
        with pytest.raises(ValueError, match="schedule"):
            validate_config({"modules": {"clock": {"schedule": bad}}})


WAKE_CFG = {"mode": "wake", "wake": {"timeout_minutes": 5, "extend": True, "presence": True}}
NOON = time(12, 0)


def make_manager(display_cfg):
    m = DisplayPowerManager()
    patcher = patch("mirrordash_core.features.power.display_power.load_config", return_value={"system": {"display_control": display_cfg}})
    patcher.start()
    return m, patcher


def test_wake_extends_with_every_activity_and_holds_while_present():
    m, p = make_manager(WAKE_CFG)
    try:
        assert m.desired_state(WAKE_CFG, 0, NOON, present=False) is False  # off until woken
        m.wake(now=0)                                                        # e.g. the API
        assert m.desired_state(WAKE_CFG, 299, NOON, present=False) is True
        m.wake(now=200)                                                      # another call restarts the countdown
        assert m.desired_state(WAKE_CFG, 499, NOON, present=False) is True
        assert m.desired_state(WAKE_CFG, 501, NOON, present=False) is False
        # someone arrives at 600 and stays an hour: on the whole time, off 5 min after they leave
        assert m.desired_state(WAKE_CFG, 600, NOON, present=True) is True
        assert m.desired_state(WAKE_CFG, 4200, NOON, present=True) is True
        assert m.desired_state(WAKE_CFG, 4201, NOON, present=False) is True
        assert m.desired_state(WAKE_CFG, 4200 + 301, NOON, present=False) is False
    finally:
        p.stop()


def test_fixed_time_ignores_new_activity():
    cfg = {"mode": "wake", "wake": {"timeout_minutes": 5, "extend": False, "presence": True}}
    m, p = make_manager(cfg)
    try:
        assert m.desired_state(cfg, 0, NOON, present=True) is True      # arrival wakes
        m.wake(now=200)                                                   # ignored: already awake
        assert m.desired_state(cfg, 299, NOON, present=True) is True
        assert m.desired_state(cfg, 301, NOON, present=True) is False    # off although still there
    finally:
        p.stop()


def test_per_call_timeout_and_off():
    m, p = make_manager(WAKE_CFG)
    try:
        m.wake(timeout_minutes=1, now=0)
        assert m.desired_state(WAKE_CFG, 59, NOON, present=False) is True
        assert m.desired_state(WAKE_CFG, 61, NOON, present=False) is False
        m.wake(now=100)
        m.turn_off()
        assert m.desired_state(WAKE_CFG, 101, NOON, present=False) is False
    finally:
        p.stop()


def test_schedule_with_wake_outside_and_manual_off_inside():
    cfg = {"mode": "interval", "interval": {"start": "07:00", "end": "22:00"}, **{"wake": WAKE_CFG["wake"]}}
    m, p = make_manager(cfg)
    try:
        assert m.desired_state(cfg, 0, NOON, present=False) is True
        m.turn_off()                                                      # off by hand during the day
        assert m.desired_state(cfg, 10, NOON, present=False) is False   # stays off until woken
        night = time(23, 0)
        assert m.desired_state(cfg, 20, night, present=False) is False   # schedule ended
        assert m.desired_state(cfg, 30, night, present=True) is True     # woken at night
        assert m.desired_state(cfg, 30 + 301, night, present=False) is False
        assert m.desired_state(cfg, 1000, time(7, 30), present=False) is True  # next morning: on again
    finally:
        p.stop()


def test_power_form_saves_wake_settings():
    from fastapi.testclient import TestClient

    from mirrordash_core.admin import require_api_key
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with patch("mirrordash_core.system_settings.update_system_settings", new_callable=AsyncMock) as update:
            TestClient(app).post("/admin/panels/power/save", data={
                "display_control[mode]": "wake",
                "display_control[wake][timeout_minutes]": "3",
                "display_control[wake][extend]": "false",
                "display_control[wake][presence]": ["false", "true"],  # hidden fallback + checked checkbox
            })
        assert update.call_args.kwargs["settings"]["display_control"]["wake"] == {
            "timeout_minutes": 3, "extend": False, "presence": True}
    finally:
        app.dependency_overrides.clear()
