# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import html
import importlib.metadata
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import BOOT_ID, events_header, job_response, notify, require_api_key, start_job, templates, ui_events
from mirrordash_core.config import load_config, get_core_version, save_config
from mirrordash_core.system import poweroff_system, reboot_system, remount_ro, remount_rw, sudo_allowed
from mirrordash_core.api.admin_system import (
    get_system_settings,
    update_system_settings,
    check_core_update,
    update_core,
    rebuild_venv,
)

logger = logging.getLogger("mirrordash.core.api.admin_system_panels")

router = APIRouter()


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


@router.get("/panels/power", dependencies=[Depends(require_api_key)])
async def get_panel_power(request: Request):
    config = load_config()
    globals_cfg = config.get("globals", {})
    time_format = globals_cfg.get("time_format", "24h")

    settings_data = await get_system_settings()
    settings = settings_data.get("settings", {})

    # Parse current active times
    display_control = settings.get("display_control", {})
    interval = display_control.get("interval", {"start": "07:00", "end": "22:00"})
    start_time_str = interval.get("start", "07:00")
    end_time_str = interval.get("end", "22:00")

    def parse_time_to_format(time_str: str, fmt: str):
        try:
            h_str, m_str = time_str.split(":")
            h, m = int(h_str), int(m_str)
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

    hours_list = list(range(1, 13)) if time_format == "12h" else list(range(0, 24))
    minutes_list = list(range(0, 60))

    from mirrordash_core.hardware import DEVICE_TYPES, PRESENCE_TYPES, get_devices
    return templates.TemplateResponse(
        request=request,
        name="admin_power.html",
        context={
            "presence_sensors": [f"{DEVICE_TYPES[d['type']]['label']} on GPIO {d['pin']}"
                                 for d in get_devices(config.get("system", {})) if d["type"] in PRESENCE_TYPES],
            "settings": settings,
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

@router.post("/panels/power/save", dependencies=[Depends(require_api_key)])
@router.post("/panels/system/save", dependencies=[Depends(require_api_key)])
async def save_system_settings_route(request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v

    from mirrordash_core.api.form_generator import parse_flat_form_data
    parsed = parse_flat_form_data(flat_data)

    # Format times back to HH:MM strings expected by update_system_settings
    display_control = parsed.get("display_control", {})
    interval = display_control.get("interval", {})
    if "start_h" in interval and "start_m" in interval:
        h = int(interval["start_h"])
        m = interval["start_m"]
        ampm = interval.get("start_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        display_control["interval"] = {
            "start": f"{h:02d}:{m}"
        }
    if "end_h" in interval and "end_m" in interval:
        h = int(interval["end_h"])
        m = interval["end_m"]
        ampm = interval.get("end_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        if "interval" not in display_control:
            display_control["interval"] = {}
        display_control["interval"]["end"] = f"{h:02d}:{m}"

    if "brightness" in parsed:
        try:
            parsed["brightness"] = int(parsed["brightness"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Brightness must be an integer")
    if "volume" in parsed:
        try:
            parsed["volume"] = int(parsed["volume"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Volume must be an integer")

    if "pir" in display_control:
        pir = display_control["pir"]
        try:
            pir["timeout_minutes"] = int(pir.get("timeout_minutes", 5))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="The timeout must be a whole number of minutes")

    await update_system_settings(settings=parsed)
    return notify("Saved.")


@router.post("/panels/power/screen", dependencies=[Depends(require_api_key)])
@router.post("/panels/system/screen", dependencies=[Depends(require_api_key)])
async def post_panel_screen(request: Request):
    form_data = await request.form()
    state = form_data.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state")

    from mirrordash_core.display_power import display_power_manager
    asyncio.create_task(display_power_manager.set_state(state == "on"))

    return notify(f"Screen turned {state}.")


@router.get("/panels/system/update-check", dependencies=[Depends(require_api_key)])
async def get_system_update_check():
    try:
        data = await check_core_update()
    except Exception as e:
        return HTMLResponse(content=f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for updates: {str(e)}</div>')

    current = data.get("current_version", "—")
    latest = data.get("latest_version", "—")
    avail = data.get("update_available", False)

    if avail:
        return HTMLResponse(content=f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #10b981;"><strong>Update available!</strong> New version v{latest} is available (currently installed: v{current}).</p>
                <button type="button" 
                        class="btn primary btn-sm" 
                        style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Are you sure you want to upgrade MirrorDash Core to v{latest}? MirrorDash will restart afterwards."
                        hx-disabled-elt="this">
                    Upgrade to v{latest} Now
                </button>
            </div>
        """)
    else:
        return HTMLResponse(content=f'<div style="margin-top: 10px; color: var(--text-muted);">Your system is up-to-date (v{current}).</div>')


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update():
    job_id = start_job(update_core)
    return job_response(job_id, "Updating MirrorDash", "Installing the new version. This can take a few minutes...",
                        "MirrorDash was updated successfully.")


@router.post("/panels/system/rebuild-venv", dependencies=[Depends(require_api_key)])
async def trigger_rebuild_venv():
    job_id = start_job(rebuild_venv)
    return job_response(job_id, "Rebuilding Environment", "Reinstalling MirrorDash and its modules. This can take several minutes...",
                        "Environment rebuilt successfully.")


def _format_temperature(celsius: float) -> str:
    if load_config().get("globals", {}).get("temperature_unit", "C") == "F":
        return f"{celsius * 9 / 5 + 32:.1f} °F"
    return f"{celsius:.1f} °C"


async def _sensor_texts(device_type: str) -> list[tuple[str, str]]:
    """(label, value) pairs for an IIO sensor's latest reading, or [] without a reading."""
    from mirrordash_core.hardware import read_sensor

    reading = await read_sensor(device_type)
    if not reading:
        return []
    if device_type == "dht11":
        return [("Temperature", _format_temperature(reading["temperature_c"])), ("Humidity", f"{reading['humidity']} %")]
    return [("Light", f"{reading['lux']:.0f} lux")]


def _devices_card(request: Request, system_cfg: dict, message: str = "", kind: str = "success",
                  restart_needed: bool = False):
    """The Sensors & Inputs card, plus the page events that report what happened."""
    from mirrordash_core.hardware import (
        BUTTON_ACTIONS, DEVICE_TYPES, GPIO_HEADER_PINS, I2C_PINS, get_devices,
    )

    devices = get_devices(system_cfg)
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
            "available_types": [t for t in DEVICE_TYPES if t not in {d["type"] for d in devices}],
            "free_pins": [(bcm, header) for bcm, header in sorted(GPIO_HEADER_PINS.items()) if bcm not in used_pins],
            "header_pins": GPIO_HEADER_PINS,
            "button_actions": BUTTON_ACTIONS,
            "press_labels": [("single", "Single press"), ("double", "Double press"),
                             ("triple", "Triple press"), ("long", "Long press (1 s)")],
        },
        headers=events_header(**events) if events else None,
    )


async def _save_devices(request: Request, config: dict, devices: list[dict], done_message: str):
    """Validate, write the overlays and save; answer with the updated card."""
    from mirrordash_core.hardware import sync_gpio_overlays

    system_cfg = config.setdefault("system", {})
    previous = system_cfg.get("devices", [])
    system_cfg["devices"] = devices
    restart_needed, error = await sync_gpio_overlays(system_cfg)
    if error:
        system_cfg["devices"] = previous
        return _devices_card(request, system_cfg, error, "error")
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
    if restart_needed:
        done_message += " Restart the mirror to start using it."
    return _devices_card(request, system_cfg, done_message, restart_needed=restart_needed)


@router.get("/panels/system/devices", dependencies=[Depends(require_api_key)])
async def get_devices_card(request: Request):
    return _devices_card(request, load_config().get("system", {}))


@router.post("/panels/system/devices/add", dependencies=[Depends(require_api_key)])
async def add_device(request: Request):
    from mirrordash_core.hardware import DEVICE_TYPES, PRESS_TYPES

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
    if device_type == "button":
        device["actions"] = {p: "none" for p in PRESS_TYPES}
    return await _save_devices(request, config, devices + [device], f"{spec['label']} added.")


@router.post("/panels/system/devices/remove", dependencies=[Depends(require_api_key)])
async def remove_device(request: Request):
    from mirrordash_core.hardware import DEVICE_TYPES

    device_type = (await request.form()).get("type", "")
    config = load_config()
    devices = [d for d in config.get("system", {}).get("devices", []) if d.get("type") != device_type]
    label = DEVICE_TYPES.get(device_type, {}).get("label", "Device")
    return await _save_devices(request, config, devices, f"{label} removed.")


@router.post("/panels/system/devices/button-actions", dependencies=[Depends(require_api_key)])
async def save_button_actions(request: Request):
    from mirrordash_core.hardware import BUTTON_ACTIONS, PRESS_TYPES, find_device

    form = await request.form()
    actions = {p: form.get(f"action_{p}", "none") for p in PRESS_TYPES}
    if any(a not in BUTTON_ACTIONS for a in actions.values()):
        return notify("Unknown button action.", "error")
    config = load_config()
    button = find_device(config.get("system", {}), "button")
    if not button:
        return notify("No push button is connected.", "error")
    button["actions"] = actions  # takes effect on the next press, no restart needed
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
    return notify("Saved.")


@router.get("/panels/system/gpio-status", dependencies=[Depends(require_api_key)])
async def get_gpio_status():
    import time
    from mirrordash_core.hardware import DEVICE_TYPES, PRESENCE_TYPES, get_devices, gpio_inputs, kernel_boot_id

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
        if d["type"] == "button":
            status = "Ready" if "button" in gpio_inputs.detected else "Not detected. Restart the mirror to load the driver."
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
    from mirrordash_core.hardware import IIO_SENSORS, find_device

    system_cfg = load_config().get("system", {})
    tiles = []
    for device_type in IIO_SENSORS:
        if find_device(system_cfg, device_type):
            tiles += await _sensor_texts(device_type)
    if not tiles:
        return HTMLResponse(content='<p style="color: var(--text-muted); margin: 0;">Waiting for the first reading...</p>')
    return HTMLResponse(content='<div style="display: flex; gap: 32px; flex-wrap: wrap;">' + "".join(
        f'<div><div style="font-size: 1.8rem; font-weight: 600; color: white;">{html.escape(value)}</div>'
        f'<div style="font-size: 0.75rem; color: var(--text-muted);">{label}</div></div>'
        for label, value in tiles) + "</div>")


@router.post("/panels/power/shutdown", dependencies=[Depends(require_api_key)])
async def shutdown_mirror():
    if not await sudo_allowed("/usr/sbin/poweroff"):
        return notify("This mirror's OS image is too old to shut down from here. Flash the latest MirrorDash OS image.", "error")
    await poweroff_system(delay_sec=2.0)
    return ui_events(**{"md-overlay": {"title": "Shutting Down", "message":
        "The mirror is shutting down. Wait until the screen has been dark for 10 seconds before unplugging the power."}})


@router.post("/panels/power/reboot", dependencies=[Depends(require_api_key)])
async def reboot_mirror():
    if not await sudo_allowed("/usr/sbin/reboot"):
        return notify("Restarting the mirror isn't allowed on this OS image.", "error")
    await reboot_system(delay_sec=2.0)
    return ui_events(**{"md-follow": {"bootId": BOOT_ID, "title": "Restarting the Mirror",
                                      "message": "Restarting... This takes about a minute.",
                                      "successMsg": "The mirror has restarted."}})
