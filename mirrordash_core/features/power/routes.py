# SPDX-License-Identifier: AGPL-3.0-only
"""The Power tab: the screen's schedule and timeout, turning it on and off, USB, shutting down and restarting."""

import html
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import (
    BOOT_ID,
    events_header,
    notify,
    require_api_key,
    templates,
    ui_events,
)
from mirrordash_core.config import load_config
from mirrordash_core.features.power.usb import set_usb, usb_status
from mirrordash_core.forms import read_form
from mirrordash_core.host import poweroff_system, reboot_system, sudo_allowed
from mirrordash_core.system_settings import get_system_settings

logger = logging.getLogger("mirrordash.core.power")
router = APIRouter(prefix="/admin")


@router.post("/screen")
async def update_screen_state(body: dict = Body(...)) -> dict:
    """Open endpoint (e.g. for Home Assistant). {"state": "on"} wakes the screen for the
    configured screen timeout, or for "timeout_minutes" if given; {"state": "off"} turns it off."""
    state = body.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state value. Must be 'on' or 'off'")
    timeout = body.get("timeout_minutes")
    if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 1440):
        raise HTTPException(status_code=400, detail="timeout_minutes must be a number from 1 to 1440")

    from mirrordash_core.features.power.display_power import display_power_manager
    if state == "on":
        display_power_manager.wake(timeout)
    else:
        display_power_manager.turn_off()

    return {"status": "success", "message": f"Screen turned {state}"}


def _clock_parts(time_text: str, time_format: str) -> tuple[int, int, str | None]:
    """"22:30" as (hour, minute, AM/PM): (10, 30, "PM") on a 12h clock, (22, 30, None) on a 24h one."""
    try:
        hour, minute = (int(part) for part in time_text.split(":"))
    except ValueError:
        hour, minute = 7, 0
    if time_format != "12h":
        return hour, minute, None
    return hour % 12 or 12, minute, "PM" if hour >= 12 else "AM"


@router.get("/panels/power", dependencies=[Depends(require_api_key)])
async def get_panel_power(request: Request):
    config = load_config()
    globals_cfg = config.get("globals", {})
    time_format = globals_cfg.get("time_format", "24h")

    settings_data = await get_system_settings()
    settings = settings_data.get("settings", {})

    # When the screen is on in the scheduled mode, shown as the owner's clock (24h or 12h) has it
    interval = settings.get("display_control", {}).get("interval", {"start": "07:00", "end": "22:00"})
    start_h, start_m, start_ampm = _clock_parts(interval.get("start", "07:00"), time_format)
    end_h, end_m, end_ampm = _clock_parts(interval.get("end", "22:00"), time_format)
    hours_list = list(range(1, 13)) if time_format == "12h" else list(range(0, 24))
    minutes_list = list(range(0, 60))

    from mirrordash_core.features.hardware.devices import (
        DEVICE_TYPES,
        PRESENCE_TYPES,
        get_devices,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin_power.html",
        context={
            "presence_sensors": [f"{DEVICE_TYPES[device['type']]['label']} on GPIO {device['pin']}"
                                 for device in get_devices(config.get("system", {})) if device["type"] in PRESENCE_TYPES],
            "settings": settings,
            "time_format": time_format,
            "start_h": start_h,
            "start_m": start_m,
            "start_ampm": start_ampm,
            "end_h": end_h,
            "end_m": end_m,
            "end_ampm": end_ampm,
            "hours_list": hours_list,
            "minutes_list": minutes_list,
            "usb": await usb_status(),
        }
    )


@router.post("/panels/power/usb", dependencies=[Depends(require_api_key)])
async def post_panel_usb(request: Request):
    turn_on = (await read_form(request)).get("on") in (True, "true")
    await set_usb(turn_on)
    message = f"USB is {'on' if turn_on else 'off'}."
    return templates.TemplateResponse(request=request, name="admin_power_usb.html", context={"usb": await usb_status()},
                                      headers=events_header(**{"md-notify": {"message": message, "kind": "success"}}))


@router.post("/panels/power/screen", dependencies=[Depends(require_api_key)])
@router.post("/panels/system/screen", dependencies=[Depends(require_api_key)])
async def post_panel_screen(request: Request):
    form_data = await request.form()
    state = form_data.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state")

    from mirrordash_core.features.power.display_power import display_power_manager
    display_power_manager.wake() if state == "on" else display_power_manager.turn_off()

    return notify(f"Screen turned {state}.")


@router.post("/panels/system/reload-screen", dependencies=[Depends(require_api_key)])
async def post_reload_screen():
    from mirrordash_core.features.kiosk.ws import manager
    await manager.broadcast({"action": "reload"})
    return notify("The mirror's screen is reloading.")


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


@router.get("/panels/power/screen-status", dependencies=[Depends(require_api_key)])
async def get_screen_status():
    import math

    from mirrordash_core.features.hardware.devices import gpio_inputs
    from mirrordash_core.features.power.display_power import display_power_manager

    status = display_power_manager.status()
    if not status["on"]:
        text = "The screen is off" + (" (turned off by hand)." if status["forced_off"] else ".")
    elif status["base_on"]:
        text = "The screen is on."
    elif gpio_inputs.motion_active and status["wake_seconds_left"]:
        text = "The screen is on while someone is in front of the mirror."
    elif status["wake_seconds_left"]:
        text = f"The screen is on and turns off in {math.ceil(status['wake_seconds_left'] / 60)} min."
    else:
        text = "The screen is on."
    dot_color = "#a0ffba" if status["on"] else "#71717a"
    return HTMLResponse(content=f"<i class='fas fa-circle' style='font-size: 0.5rem; color: {dot_color};'></i> {html.escape(text)}")
