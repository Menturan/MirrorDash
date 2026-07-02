import pytest
import json
import io
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from mirrordash_homeassistant.plugin import HomeassistantModule, fetch_entity_state, resolve_smart_state_and_icon

def test_resolve_smart_state_and_icon():
    # Helper dummy translate function
    def dummy_translate(key, default):
        translations = {
            "state_motion_detected": "Rörelse",
            "state_motion_clear": "Ingen rörelse",
            "state_running": "Körs",
            "state_idle": "Klar",
            "state_open": "Öppen",
            "state_closed": "Stängd",
            "state_locked": "Låst",
            "state_unlocked": "Upplåst",
            "state_on": "På",
            "state_off": "Av"
        }
        return translations.get(key, default)

    # 1. Test Motion Sensor
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.living_room_motion", "on", {"device_class": "motion"}, dummy_translate
    )
    assert state_str == "Rörelse"
    assert icon == "activity"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.living_room_motion", "off", {"device_class": "motion"}, dummy_translate
    )
    assert state_str == "Ingen rörelse"
    assert icon == "eye-off"
    assert active is False

    # 2. Test Washing Machine
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.washing_machine_running", "on", {}, dummy_translate
    )
    assert state_str == "Körs"
    assert icon == "washing-machine"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "sensor.washing_machine", "idle", {}, dummy_translate
    )
    # Since idle is listed as binary state in resolver, it maps to state_idle
    assert state_str == "Klar"
    assert icon == "washing-machine"
    assert active is False

    # 3. Test Door
    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.front_door", "on", {"device_class": "door"}, dummy_translate
    )
    assert state_str == "Öppen"
    assert icon == "door-open"
    assert active is True

    state_str, icon, active = resolve_smart_state_and_icon(
        "binary_sensor.front_door", "off", {"device_class": "door"}, dummy_translate
    )
    assert state_str == "Stängd"
    assert icon == "door-closed"
    assert active is False

    # 4. Test numeric temp and humidity
    state_str, icon, active = resolve_smart_state_and_icon(
        "sensor.room_temp", "21.5", {"unit_of_measurement": "°C"}, dummy_translate
    )
    assert state_str == "21.5 °C"
    assert icon == "thermometer"
    assert active is False



@patch("urllib.request.urlopen")
def test_fetch_entity_state_success(mock_urlopen):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "entity_id": "sensor.test_temp",
        "state": "22.4",
        "attributes": {
            "friendly_name": "Test Temperature",
            "unit_of_measurement": "°C"
        }
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    res = fetch_entity_state("http://localhost:8123", "fake_token", "sensor.test_temp")
    assert res is not None
    assert res["state"] == "22.4"
    assert res["attributes"]["friendly_name"] == "Test Temperature"

@patch("urllib.request.urlopen")
def test_fetch_entity_state_failure(mock_urlopen):
    # Setup mock response with non-200 code
    mock_response = MagicMock()
    mock_response.status = 404
    mock_urlopen.return_value.__enter__.return_value = mock_response

    res = fetch_entity_state("http://localhost:8123", "fake_token", "sensor.non_existent")
    assert res is None

@patch("urllib.request.urlopen")
def test_fetch_entity_state_exception(mock_urlopen):
    # Setup mock to raise exception
    mock_urlopen.side_effect = Exception("Connection refused")

    res = fetch_entity_state("http://localhost:8123", "fake_token", "sensor.test_temp")
    assert res is None

@pytest.mark.asyncio
@patch("urllib.request.urlopen")
async def test_fetch_all_states_mapping(mock_urlopen):
    # Setup mock response
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "entity_id": "sensor.living_room_temp",
        "state": "21.5",
        "attributes": {
            "friendly_name": "Living Room Temp",
            "unit_of_measurement": "°C"
        }
    }).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": [
            {"entity_id": "sensor.living_room_temp", "custom_name": "Living Room"}
        ]
    }
    
    module = HomeassistantModule(config)
    states = await module.fetch_all_states("http://localhost:8123", "fake_token", config["entities"])
    
    assert len(states) == 1
    assert states[0]["entity_id"] == "sensor.living_room_temp"
    assert states[0]["name"] == "Living Room"
    assert states[0]["state"] == "21.5 °C"
    assert states[0]["icon"] == "thermometer"
    assert not states[0]["error"]

@pytest.mark.asyncio
async def test_run_loop_missing_token():
    config = {
        "url": "http://localhost:8123",
        "entities": [{"entity_id": "sensor.temp"}]
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Missing Token</div>")
    
    broadcast_func = AsyncMock()
    
    # Run the loop logic once by patching asyncio.sleep to break the loop
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    broadcast_func.assert_called_once()
    args = broadcast_func.call_args[0]
    assert args[0] == "mirrordash_homeassistant"
    assert "Missing Token" in args[1]
    module.render_template.assert_called_with(
        "widget.html",
        error="API Token is missing",
        entities=[],
        heading="",
        show_header=True
    )

@pytest.mark.asyncio
async def test_run_loop_missing_entities():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>No Entities</div>")
    
    broadcast_func = AsyncMock()
    
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    broadcast_func.assert_called_once()
    args = broadcast_func.call_args[0]
    assert "No Entities" in args[1]
    module.render_template.assert_called_with(
        "widget.html",
        error="No entities configured",
        entities=[],
        heading="",
        show_header=True
    )

@pytest.mark.asyncio
async def test_run_loop_custom_heading():
    config = {
        "url": "http://localhost:8123",
        "token": "fake_token",
        "heading": "My Custom Devices",
        "entities": []
    }
    module = HomeassistantModule(config)
    module.render_template = MagicMock(return_value="<div>Custom Heading</div>")
    
    broadcast_func = AsyncMock()
    
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        try:
            await module.run_loop(broadcast_func)
        except asyncio.CancelledError:
            pass
            
    module.render_template.assert_called_with(
        "widget.html",
        error="No entities configured",
        entities=[],
        heading="My Custom Devices",
        show_header=True
    )
