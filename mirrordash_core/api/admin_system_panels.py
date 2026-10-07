# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import html
import importlib.metadata
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import BOOT_ID, job_response, notify, require_api_key, start_job, templates, ui_events
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
    from mirrordash_core.hardware import BUTTON_ACTIONS, GPIO_HEADER_PINS

    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context={
            "gpio_pins": sorted(GPIO_HEADER_PINS.items()),
            "button_actions": BUTTON_ACTIONS,
            "press_labels": [("single", "Single press"), ("double", "Double press"),
                             ("triple", "Triple press"), ("long", "Long press (1 s)")],
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

    return templates.TemplateResponse(
        request=request,
        name="admin_power.html",
        context={
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
            pir["pin"] = int(pir.get("pin", 18))
            pir["timeout_minutes"] = int(pir.get("timeout_minutes", 5))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="PIR pin and timeout must be integers")

    res = await update_system_settings(settings=parsed)

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


def _format_reading(reading: dict) -> tuple[str, str]:
    """(temperature, humidity) strings in the user's temperature unit."""
    unit = load_config().get("globals", {}).get("temperature_unit", "C")
    temp = reading["temperature_c"]
    if unit == "F":
        return f"{temp * 9 / 5 + 32:.1f} °F", f"{reading['humidity']} %"
    return f"{temp:.1f} °C", f"{reading['humidity']} %"


@router.post("/panels/system/gpio", dependencies=[Depends(require_api_key)])
async def save_gpio_settings(request: Request):
    from mirrordash_core.hardware import BUTTON_ACTIONS, GPIO_HEADER_PINS, PRESS_TYPES, kernel_boot_id, write_gpio_overlays

    form = await request.form()

    def parse_pin(name: str) -> int | None:
        value = (form.get(name) or "").strip()
        if not value:
            return None
        if not value.isdigit() or int(value) not in GPIO_HEADER_PINS:
            raise ValueError(f"GPIO {value} can't be used.")
        return int(value)

    try:
        button_pin, dht11_pin = parse_pin("button_pin"), parse_pin("dht11_pin")
    except ValueError as e:
        return notify(str(e), "error")
    actions = {p: form.get(f"action_{p}", "none") for p in PRESS_TYPES}
    if any(a not in BUTTON_ACTIONS for a in actions.values()):
        return notify("Unknown button action.", "error")
    if button_pin is not None and button_pin == dht11_pin:
        return notify("The button and the DHT11 sensor can't use the same GPIO.", "error")

    config = load_config()
    system_cfg = config.setdefault("system", {})
    dc = system_cfg.get("display_control", {})
    pir_pin = dc.get("pir", {}).get("pin") if dc.get("mode") == "pir" else None
    if pir_pin is not None and pir_pin in (button_pin, dht11_pin):
        return notify(f"GPIO {pir_pin} is already used by the motion sensor (Power tab).", "error")

    pins_changed = (system_cfg.get("button", {}).get("pin"), system_cfg.get("dht11", {}).get("pin")) != (button_pin, dht11_pin)
    if pins_changed:
        error = await write_gpio_overlays(button_pin, dht11_pin)
        if error:
            return notify(error, "error")
        # The new pins are active after the next OS boot; remember which boot they were set in
        system_cfg["gpio_pending_boot_id"] = kernel_boot_id()

    system_cfg["button"] = {"pin": button_pin, "actions": actions}
    system_cfg["dht11"] = {"pin": dht11_pin}
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()

    if not pins_changed:
        return notify("Saved.")
    return notify("Saved. Restart the mirror to use the new GPIO pin.", **{"gpio-changed": True})


@router.get("/panels/system/gpio-status", dependencies=[Depends(require_api_key)])
async def get_gpio_status():
    from mirrordash_core.hardware import button_manager, read_dht11

    from mirrordash_core.hardware import kernel_boot_id

    system_cfg = load_config().get("system", {})
    button_pin = system_cfg.get("button", {}).get("pin")
    dht11_pin = system_cfg.get("dht11", {}).get("pin")

    if system_cfg.get("gpio_pending_boot_id") == kernel_boot_id():
        return HTMLResponse(content="""
            <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                <span><strong>Restart needed:</strong> the new GPIO pins are used after the mirror restarts.</span>
                <button type="button" class="btn secondary btn-sm" hx-post="/admin/panels/power/reboot" hx-target="#global-status"
                        hx-disabled-elt="this" hx-confirm="Restart the mirror now? The screen will be off for about a minute.">
                    <i class="fas fa-redo" aria-hidden="true"></i> Restart Mirror
                </button>
            </div>
        """)

    if button_pin is None:
        button = "Not connected"
    elif button_manager.connected:
        button = f"Ready on GPIO {button_pin}"
    else:
        button = f"Not detected on GPIO {button_pin}. Restart the mirror to load the button driver."

    if dht11_pin is None:
        sensor = "Not connected"
    else:
        reading = await read_dht11()
        sensor = " · ".join(_format_reading(reading)) if reading else \
            f"No reading from GPIO {dht11_pin}. Check the wiring."

    return HTMLResponse(content=f"""
        <div><strong>Button:</strong> {html.escape(button)}</div>
        <div><strong>DHT11:</strong> {html.escape(sensor)}</div>
    """)


@router.get("/panels/dashboard/sensor", dependencies=[Depends(require_api_key)])
async def get_dashboard_sensor():
    from mirrordash_core.hardware import read_dht11

    reading = await read_dht11()
    if not reading:
        return HTMLResponse(content='<p style="color: var(--text-muted); margin: 0;">Waiting for the first reading...</p>')
    temperature, humidity = _format_reading(reading)
    return HTMLResponse(content=f"""
        <div style="display: flex; gap: 32px; flex-wrap: wrap;">
            <div><div style="font-size: 1.8rem; font-weight: 600; color: white;">{temperature}</div>
                 <div style="font-size: 0.75rem; color: var(--text-muted);">Temperature</div></div>
            <div><div style="font-size: 1.8rem; font-weight: 600; color: white;">{humidity}</div>
                 <div style="font-size: 0.75rem; color: var(--text-muted);">Humidity</div></div>
        </div>
    """)


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
