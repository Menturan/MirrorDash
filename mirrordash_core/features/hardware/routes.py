# SPDX-License-Identifier: AGPL-3.0-only
"""The Hardware tab: the screen and sound settings, and the sensors and inputs on the GPIO header."""

import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import events_header, notify, require_api_key, templates
from mirrordash_core.config import get_core_version, load_config, save_config
from mirrordash_core.features.hardware.devices import (
    BUTTON_ACTIONS,
    BUTTON_TYPES,
    DEFAULT_LONG_PRESS,
    DEVICE_TYPES,
    FAN_TYPES,
    GPIO_HEADER_PINS,
    I2C_PINS,
    IIO_SENSORS,
    LONG_PRESS_CHOICES,
    PRESENCE_TYPES,
    PRESS_TYPES,
    find_device,
    get_devices,
    gpio_inputs,
    kernel_boot_id,
    read_fan_state,
    read_sensor,
    sync_gpio_overlays,
)
from mirrordash_core.system_settings import get_system_settings

logger = logging.getLogger("mirrordash.core.hardware")
router = APIRouter(prefix="/admin")


@router.get("/panels/system", dependencies=[Depends(require_api_key)])
async def get_panel_system(request: Request):
    settings_data = await get_system_settings()
    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context={
            "settings": settings_data.get("settings", {}),
            "resolutions": settings_data.get("resolutions", []),
            "current_version": get_core_version(),
        }
    )


def _format_temperature(celsius: float) -> str:
    if load_config().get("globals", {}).get("temperature_unit", "C") == "F":
        return f"{celsius * 9 / 5 + 32:.1f} °F"
    return f"{celsius:.1f} °C"


async def _sensor_texts(device_type: str) -> list[tuple[str, str]]:
    """(label, value) pairs for an IIO sensor's latest reading, or [] without a reading."""
    reading = await read_sensor(device_type)
    if not reading:
        return []
    if device_type == "dht11":
        return [("Temperature", _format_temperature(reading["temperature_c"])), ("Humidity", f"{reading['humidity']} %")]
    return [("Light", f"{reading['lux']:.0f} lux")]


def _fan_text() -> str | None:
    fan = read_fan_state()
    if not fan:
        return None
    if fan["level"] == 0:
        state = "Off"
    elif fan["max_level"] > 1:
        state = f"Running, speed {fan['level']} of {fan['max_level']}"
    else:
        state = "Running"
    return f"{state} (CPU {_format_temperature(fan['cpu_temperature_c'])})"


def _devices_card(request: Request, system_cfg: dict, message: str = "", kind: str = "success",
                  restart_needed: bool = False):
    """The Sensors & Inputs card, plus the page events that report what happened."""
    devices = get_devices(system_cfg)
    used_types = {device["type"] for device in devices}
    next_button = next((button for button in BUTTON_TYPES if button not in used_types), None)  # offer one button at a time
    used_pins = {device["pin"] for device in devices if "pin" in device}
    if any("address" in device for device in devices):
        used_pins |= set(I2C_PINS)
    events = {}
    if message:
        events["md-notify"] = {"message": message, "kind": kind}
    if restart_needed:
        events["gpio-changed"] = True
    return templates.TemplateResponse(
        request=request,
        name="admin_devices.html",
        context={
            "devices": devices,
            "device_types": DEVICE_TYPES,
            "available_types": [device_type for device_type in DEVICE_TYPES if device_type not in used_types
                                and (device_type not in BUTTON_TYPES or device_type == next_button)
                                and not (device_type in FAN_TYPES and used_types & set(FAN_TYPES))],
            "free_pins": [(bcm, header) for bcm, header in sorted(GPIO_HEADER_PINS.items()) if bcm not in used_pins],
            "header_pins": GPIO_HEADER_PINS,
            "button_types": BUTTON_TYPES,
            "button_actions": BUTTON_ACTIONS,
            "long_press_choices": LONG_PRESS_CHOICES,
            "default_long_press": DEFAULT_LONG_PRESS,
            "press_labels": [("single", "Single press"), ("double", "Double press"),
                             ("triple", "Triple press"), ("long", "Long press")],
        },
        headers=events_header(**events) if events else None,
    )


async def _save_devices(request: Request, config: dict, devices: list[dict], done_message: str):
    """Validate, write the overlays and save; answer with the updated card."""
    system_cfg = config.setdefault("system", {})
    previous = system_cfg.get("devices", [])
    system_cfg["devices"] = devices
    restart_needed, error = await sync_gpio_overlays(system_cfg)
    if error:
        system_cfg["devices"] = previous
        return _devices_card(request, system_cfg, error, "error")
    save_config(config)
    if restart_needed:
        done_message += " Restart the mirror to start using it."
    return _devices_card(request, system_cfg, done_message, restart_needed=restart_needed)


@router.get("/panels/system/devices", dependencies=[Depends(require_api_key)])
async def get_devices_card(request: Request):
    return _devices_card(request, load_config().get("system", {}))


@router.post("/panels/system/devices/add", dependencies=[Depends(require_api_key)])
async def add_device(request: Request):
    form = await request.form()
    device_type = form.get("type", "")
    config = load_config()
    devices = list(config.get("system", {}).get("devices", []))
    spec = DEVICE_TYPES.get(device_type)
    if not spec:
        return _devices_card(request, config.get("system", {}), "Choose what to connect.", "error")

    device = {"type": device_type}
    if spec.get("pin"):
        pin = form.get("pin", "")
        device["pin"] = int(pin) if pin.isdigit() else -1  # -1: rejected by the validation
    else:
        device["address"] = form.get("address", "")
    if spec.get("temperature"):
        temperature = form.get("temperature", "")
        device["temperature"] = int(temperature) if temperature.isdigit() else -1
    if device_type in BUTTON_TYPES:
        device["actions"] = {press: "none" for press in PRESS_TYPES}
        device["long_press"] = DEFAULT_LONG_PRESS
    return await _save_devices(request, config, devices + [device], f"{spec['label']} added.")


@router.post("/panels/system/devices/remove", dependencies=[Depends(require_api_key)])
async def remove_device(request: Request):

    device_type = (await request.form()).get("type", "")
    config = load_config()
    devices = [device for device in config.get("system", {}).get("devices", []) if device.get("type") != device_type]
    label = DEVICE_TYPES.get(device_type, {}).get("label", "Device")
    return await _save_devices(request, config, devices, f"{label} removed.")


@router.post("/panels/system/devices/button-actions", dependencies=[Depends(require_api_key)])
async def save_button_actions(request: Request):
    form = await request.form()
    actions = {press: form.get(f"action_{press}", "none") for press in PRESS_TYPES}
    if any(action not in BUTTON_ACTIONS for action in actions.values()):
        return notify("Unknown button action.", "error")
    try:
        long_press = float(form.get("long_press", DEFAULT_LONG_PRESS))
    except ValueError:
        long_press = -1.0
    if long_press not in LONG_PRESS_CHOICES:
        return notify("Choose how long a long press is.", "error")
    button_type = form.get("type", "button")
    config = load_config()
    button = find_device(config.get("system", {}), button_type) if button_type in BUTTON_TYPES else None
    if not button:
        return notify("That push button isn't connected.", "error")
    button["actions"] = actions  # takes effect on the next press, no restart needed
    button["long_press"] = long_press
    save_config(config)
    return notify("Saved.")


@router.get("/panels/system/gpio-status", dependencies=[Depends(require_api_key)])
async def get_gpio_status():
    system_cfg = load_config().get("system", {})
    if system_cfg.get("gpio_pending_boot_id") == kernel_boot_id():
        return HTMLResponse(content=templates.get_template("hardware_status.html").render(restart_needed=True))

    lines = []
    for device in get_devices(system_cfg):
        device_type = device["type"]
        label = DEVICE_TYPES[device_type]["label"]
        if device_type in BUTTON_TYPES:
            status = "Ready" if device_type in gpio_inputs.detected else "Not detected. Restart the mirror to load the driver."
        elif device_type in PRESENCE_TYPES:
            if device_type not in gpio_inputs.detected:
                status = "Not detected. Restart the mirror to load the driver."
            elif gpio_inputs.presence.get(device_type):
                status = "Someone is there right now"
            elif gpio_inputs.last_motion_at:
                minutes = int((time.monotonic() - gpio_inputs.last_motion_at) // 60)
                status = "Last motion just now" if minutes < 1 else f"Last motion {minutes} min ago"
            else:
                status = "No motion seen since MirrorDash started"
        elif device_type in FAN_TYPES:
            status = _fan_text() or "Not detected. Restart the mirror to load the driver."
        else:
            texts = await _sensor_texts(device_type)
            status = " · ".join(value for _, value in texts) if texts else "No reading. Check the wiring."
        lines.append((label, status))
    return HTMLResponse(content=templates.get_template("hardware_status.html").render(lines=lines))


@router.get("/panels/dashboard/sensor", dependencies=[Depends(require_api_key)])
async def get_dashboard_sensor():
    system_cfg = load_config().get("system", {})
    tiles = []
    for device_type in IIO_SENSORS:
        if find_device(system_cfg, device_type):
            tiles += await _sensor_texts(device_type)
    if any(find_device(system_cfg, fan_type) for fan_type in FAN_TYPES) and (fan := _fan_text()):
        tiles.append(("Fan", fan))
    return HTMLResponse(content=templates.get_template("sensor_tiles.html").render(tiles=tiles))
