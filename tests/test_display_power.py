import pytest
import asyncio
from datetime import time
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from mirrordash_core.display_power import DisplayPowerManager, display_power_manager
from mirrordash_core.app import app

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

@patch("mirrordash_core.api.admin_system.load_config")
@patch("mirrordash_core.api.admin_system.save_config")
@patch("mirrordash_core.api.admin_system.apply_system_settings", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.api.admin_system.remount_ro", new_callable=AsyncMock)
def test_system_settings_display_control_validation(mock_ro, mock_rw, mock_apply, mock_save, mock_load, client):
    mock_load.return_value = {
        "admin_auth": {"hash": "dummy", "salt": "dummy"}
    }
    
    # Mock require_api_key dependency to bypass authentication in testing
    from mirrordash_core.api.admin import require_api_key
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
    
    # Invalid GPIO pin for PIR mode
    payload["display_control"]["mode"] = "pir"
    payload["display_control"]["pir"]["pin"] = 99 # out of range (1-40)
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid PIR GPIO pin" in response.json()["detail"]
    
    # Invalid timeout for PIR mode
    payload["display_control"]["pir"]["pin"] = 18
    payload["display_control"]["pir"]["timeout_minutes"] = 0 # too small
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid PIR timeout" in response.json()["detail"]

    # Invalid GPIO pin for Button mode
    payload["display_control"]["mode"] = "button"
    payload["display_control"]["button"]["pin"] = -5 # negative pin
    response = client.post("/admin/system", json=payload, headers=headers)
    assert response.status_code == 400
    assert "Invalid Button GPIO pin" in response.json()["detail"]
    
    # Clear overrides
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_display_power_timezone_handling():
    manager = DisplayPowerManager()
    
    # Mock load_config to return a custom timezone
    mock_config = {
        "system": {
            "display_control": {
                "mode": "interval",
                "interval": {"start": "07:00", "end": "22:00"}
            }
        },
        "globals": {
            "timezone": "America/New_York"
        }
    }
    
    with patch("mirrordash_core.display_power.load_config", return_value=mock_config), \
         patch("mirrordash_core.display_power.datetime") as mock_datetime, \
         patch("mirrordash_core.display_power.asyncio.sleep", side_effect=asyncio.CancelledError):
         
        # Mock datetime.now() to return a mock datetime with a time
        mock_now = MagicMock()
        mock_now.time.return_value = time(12, 0)
        mock_datetime.now.return_value = mock_now
        
        # Run loop (which will immediately raise CancelledError due to sleep mock)
        with pytest.raises(asyncio.CancelledError):
            await manager._run_loop()
            
        # Verify datetime.now was called with ZoneInfo("America/New_York")
        mock_datetime.now.assert_called_once()
        called_args, called_kwargs = mock_datetime.now.call_args
        assert len(called_args) > 0 or "tz" in called_kwargs
        tz_arg = called_args[0] if len(called_args) > 0 else called_kwargs["tz"]
        assert tz_arg.key == "America/New_York"
