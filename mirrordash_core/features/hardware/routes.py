# Licensed under the PolyForm Noncommercial License 1.0.0.

import html
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import events_header, notify, require_api_key, templates
from mirrordash_core.config import load_config, get_core_version, save_config
from mirrordash_core.system_settings import get_system_settings

logger = logging.getLogger("mirrordash.core.hardware")
router = APIRouter(prefix="/admin")


@router.get("/panels/system", dependencies=[Depends(require_api_key)])
async def get_panel_system(request: Request):
    config = load_config()
    globals_cfg = config.get("globals", {})
    time_format = globals_cfg.get("time_format", "24h")

    settings_data = await get_system_settings()
    settings = settings_data.get("settings", {})
    resolutions = settings_data.get("resolutions", [])

    # Parse current active times
    display_control = settings.get("display_control", {})
    interval = display_control.get("interval", {"start": "07:00", "end": "22:00"})
    start_time_str = interval.get("start", "07:00")
    end_time_str = interval.get("end", "22:00")

    # Helper to parse 24h string to (hour, minute, ampm)
    def parse_time_to_format(time_str: str, fmt: str):
        try:
            h_str, m_str = time_str.split(":")
            h = int(h_str)
            m = int(m_str)
        except Exception:
            h, m = 7, 0

        if fmt == "12h":
            ampm = "PM" if h >= 12 else "AM"
            h_12 = h % 12
            if h_12 == 0:
                h_12 = 12
            return h_12, m, ampm
        else:
            return h, m, None

    start_h, start_m, start_ampm = parse_time_to_format(start_time_str, time_format)
    end_h, end_m, end_ampm = parse_time_to_format(end_time_str, time_format)

    # Hours list
    if time_format == "12h":
        hours_list = list(range(1, 13))
    else:
        hours_list = list(range(0, 24))

    minutes_list = list(range(0, 60))

    current_version = get_core_version()

    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context={
            "settings": settings,
            "resolutions": resolutions,
            "current_version": current_version,
            "time_format": time_format,
            "start_h": start_h,
            "start_m": start_m,
            "start_ampm": start_ampm,
            "end_h": end_h,
            "end_m": end_m,
            "end_ampm": end_ampm,
            "hours_list": hours_list,
            "minutes_list": minutes_list
        }
    )


def _format_temperature(celsius: float) -> str:
    if load_config().get("globals", {}).get("temperature_unit", "C") == "F":
        return f"{celsius * 9 / 5 + 32:.1f} °F"
    return f"{celsius:.1f} °C"


async def _sensor_texts(device_type: str) -> list[tuple[str, str]]:
    """(label, value) pairs for an IIO sensor's latest reading, or [] without a reading."""
    from mirrordash_core.features.hardware.devices import read_sensor

    reading = await read_sensor(device_type)
    if not reading:
        return []
    if device_type == "dht11":
        return [("Temperature", _format_temperature(reading["temperature_c"])), ("Humidity", f"{reading['humidity']} %")]
    return [("Light", f"{reading['lux']:.0f} lux")]


def _fan_text() -> str | None:
    from mirrordash_core.features.hardware.devices import read_fan_state

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
    from mirrordash_core.features.hardware.devices import BUTTON_ACTIONS, BUTTON_TYPES, DEVICE_TYPES, FAN_TYPES, GPIO_HEADER_PINS, I2C_PINS, DEFAULT_LONG_PRESS, LONG_PRESS_CHOICES, get_devices

    devices = get_devices(system_cfg)
    used_types = {d["type"] for d in devices}
    next_button = next((t for t in BUTTON_TYPES if t not in used_types), None)  # offer one button at a time
    used_pins = {d["pin"] for d in devices if "pin" in d}
    if any("address" in d for d in devices):
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
            "available_types": [t for t in DEVICE_TYPES if t not in used_types
                                and (t not in BUTTON_TYPES or t == next_button)
                                and not (t in FAN_TYPES and any(d["type"] in FAN_TYPES for d in devices))],
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
    from mirrordash_core.features.hardware.devices import sync_gpio_overlays

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
    from mirrordash_core.features.hardware.devices import BUTTON_TYPES, DEFAULT_LONG_PRESS, DEVICE_TYPES, PRESS_TYPES

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
        device["actions"] = {p: "none" for p in PRESS_TYPES}
        device["long_press"] = DEFAULT_LONG_PRESS
    return await _save_devices(request, config, devices + [device], f"{spec['label']} added.")


@router.post("/panels/system/devices/remove", dependencies=[Depends(require_api_key)])
async def remove_device(request: Request):
    from mirrordash_core.features.hardware.devices import DEVICE_TYPES

    device_type = (await request.form()).get("type", "")
    config = load_config()
    devices = [d for d in config.get("system", {}).get("devices", []) if d.get("type") != device_type]
    label = DEVICE_TYPES.get(device_type, {}).get("label", "Device")
    return await _save_devices(request, config, devices, f"{label} removed.")


@router.post("/panels/system/devices/button-actions", dependencies=[Depends(require_api_key)])
async def save_button_actions(request: Request):
    from mirrordash_core.features.hardware.devices import BUTTON_ACTIONS, BUTTON_TYPES, DEFAULT_LONG_PRESS, LONG_PRESS_CHOICES, PRESS_TYPES, find_device

    form = await request.form()
    actions = {p: form.get(f"action_{p}", "none") for p in PRESS_TYPES}
    if any(a not in BUTTON_ACTIONS for a in actions.values()):
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
    import time
    from mirrordash_core.features.hardware.devices import BUTTON_TYPES, DEVICE_TYPES, PRESENCE_TYPES, get_devices, gpio_inputs, kernel_boot_id

    system_cfg = load_config().get("system", {})
    if system_cfg.get("gpio_pending_boot_id") == kernel_boot_id():
        return HTMLResponse(content="""
            <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <span><strong>Restart needed:</strong> the changes are used after the mirror restarts.</span>
                <button type="button" class="btn secondary btn-sm" hx-post="/admin/panels/power/reboot" hx-target="#global-status"
                        hx-disabled-elt="this" hx-confirm="Restart the mirror now? The screen will be off for about a minute.">
                    <i class="fas fa-redo" aria-hidden="true"></i> Restart Mirror
                </button>
            </div>
        """)

    lines = []
    for d in get_devices(system_cfg):
        label = DEVICE_TYPES[d["type"]]["label"]
        if d["type"] in BUTTON_TYPES:
            status = "Ready" if d["type"] in gpio_inputs.detected else "Not detected. Restart the mirror to load the driver."
        elif d["type"] in PRESENCE_TYPES:
            if d["type"] not in gpio_inputs.detected:
                status = "Not detected. Restart the mirror to load the driver."
            elif gpio_inputs.presence.get(d["type"]):
                status = "Someone is there right now"
            elif gpio_inputs.last_motion_at:
                minutes = int((time.monotonic() - gpio_inputs.last_motion_at) // 60)
                status = "Last motion just now" if minutes < 1 else f"Last motion {minutes} min ago"
            else:
                status = "No motion seen since MirrorDash started"
        elif d["type"] in ("fan", "pwm_fan"):
            status = _fan_text() or "Not detected. Restart the mirror to load the driver."
        else:
            texts = await _sensor_texts(d["type"])
            status = " · ".join(value for _, value in texts) if texts else "No reading. Check the wiring."
        lines.append((label, status))
    if not lines:
        return HTMLResponse(content="<div>Nothing connected yet.</div>")
    return HTMLResponse(content="".join(
        f"<div><strong>{html.escape(label)}:</strong> {html.escape(status)}</div>" for label, status in lines))


@router.get("/panels/dashboard/sensor", dependencies=[Depends(require_api_key)])
async def get_dashboard_sensor():
    from mirrordash_core.features.hardware.devices import FAN_TYPES, IIO_SENSORS, find_device

    system_cfg = load_config().get("system", {})
    tiles = []
    for device_type in IIO_SENSORS:
        if find_device(system_cfg, device_type):
            tiles += await _sensor_texts(device_type)
    if any(find_device(system_cfg, t) for t in FAN_TYPES) and (fan := _fan_text()):
        tiles.append(("Fan", fan))
    if not tiles:
        return HTMLResponse(content='<p style="color: var(--text-muted); margin: 0;">Waiting for the first reading...</p>')
    return HTMLResponse(content='<div style="display: flex; gap: 32px; flex-wrap: wrap;">' + "".join(
        f'<div><div style="font-size: 1.8rem; font-weight: 600; color: white;">{html.escape(value)}</div>'
        f'<div style="font-size: 0.75rem; color: var(--text-muted);">{label}</div></div>'
        for label, value in tiles) + "</div>")
