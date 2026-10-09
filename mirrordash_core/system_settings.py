# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The mirror's system settings (screen, sound, screen schedule, SSH, test versions): one place that
checks and applies them, shared by the Hardware, Power and Settings tabs and Home Assistant."""

import asyncio
import logging
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from mirrordash_core.admin import notify, require_api_key
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.hardware import display
from mirrordash_core.features.hardware.display import apply_brightness, apply_system_settings, get_available_resolutions
from mirrordash_core.features.power.display_power import DEFAULT_WAKE
from mirrordash_core.features.settings.ssh import apply_ssh, get_ssh_status
from mirrordash_core.forms import read_form

logger = logging.getLogger("mirrordash.core.system_settings")
router = APIRouter(prefix="/admin")


@router.get("/system", dependencies=[Depends(require_api_key)])
async def get_system_settings() -> dict:
    config = load_config()
    system_cfg = config.get("system", {})
    resolutions = await get_available_resolutions()
    ssh_active = await get_ssh_status()
    return {
        "settings": {
            "rotation": system_cfg.get("rotation", "normal"),
            "resolution": system_cfg.get("resolution", "auto"),
            "brightness": system_cfg.get("brightness", 100),
            "brightness_supported": display.brightness_supported is not False,
            "volume": system_cfg.get("volume", 80),
            "ssh": ssh_active,
            "prerelease": system_cfg.get("prerelease", False),
            "display_control": system_cfg.get("display_control", {
                "mode": "manual",
                "interval": {"start": "07:00", "end": "22:00"},
            }),
        },
        "resolutions": resolutions
    }


# What a setting is when the config doesn't have it yet
DEFAULTS = {"rotation": "normal", "resolution": "auto", "brightness": 100, "volume": 80, "ssh": True, "prerelease": False}
HHMM = re.compile(r"^\d{2}:\d{2}$")


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _check(new: dict) -> None:
    """Trust boundary for everything the admin page and Home Assistant can send."""
    dc = new["display_control"]
    wake = dc.get("wake", {})
    interval = dc.get("interval", {})
    checks = [
        (new["rotation"] in ("normal", "left", "right", "inverted"), "Invalid rotation value"),
        (_int_in(new["brightness"], 10, 100), "Brightness must be between 10 and 100"),
        (_int_in(new["volume"], 0, 100), "Volume must be between 0 and 100"),
        (dc.get("mode", "manual") in ("manual", "interval", "wake"), "Invalid display power mode"),
        (_int_in(wake.get("timeout_minutes", DEFAULT_WAKE["timeout_minutes"]), 1, 1440), "The screen timeout must be 1 to 1440 minutes"),
        (all(isinstance(wake.get(k, True), bool) for k in ("extend", "presence")), "Invalid wake settings"),
        (isinstance(new["prerelease"], bool), "Test versions must be on or off"),
        (dc.get("mode") != "interval" or all(HHMM.match(interval.get(k, "00:00")) for k in ("start", "end")),
         "Invalid interval time format (HH:MM)"),
    ]
    for ok, message in checks:
        if not ok:
            raise HTTPException(status_code=400, detail=message)


@router.post("/system", dependencies=[Depends(require_api_key)])
async def update_system_settings(settings: dict = Body(...)) -> dict:
    """Save the settings that were sent (the rest stay as they are) and apply them."""
    config = load_config()
    system_cfg = config.setdefault("system", {})
    new = {key: settings.get(key, system_cfg.get(key, default)) for key, default in DEFAULTS.items()}
    # display_control is merged per part: the Power tab sends only what it shows
    dc = dict(system_cfg.get("display_control", {"mode": "manual", "interval": {"start": "07:00", "end": "22:00"},
                                                 "wake": dict(DEFAULT_WAKE)}))
    incoming = settings.get("display_control") or {}
    if "mode" in incoming:
        dc["mode"] = incoming["mode"]
    for part in ("interval", "wake"):
        if part in incoming:
            dc[part] = {**dc.get(part, {}), **incoming[part]}
    new["display_control"] = dc
    _check(new)
    system_cfg.update(new)

    # Only touch SSH when this request is about it: the Power tab saves just the display
    # schedule, and re-checking SSH there failed whenever the service state differed.
    if "ssh" in settings:
        await apply_ssh(new["ssh"], settings.get("pi_password"))
    # Saved only now: a refused SSH password must not leave "ssh: on" in the config
    save_config(config)

    # Re-applying an unchanged rotation/resolution can make the screen flicker: only what was sent
    if any(k in settings for k in ("rotation", "resolution", "volume")):
        asyncio.create_task(apply_system_settings(new["rotation"], new["resolution"], new["brightness"], new["volume"]))
    elif "brightness" in settings:  # the slider, or Home Assistant: only the brightness
        asyncio.create_task(apply_brightness(new["brightness"]))
    return {"status": "success", "message": "System settings saved and applied successfully"}


def _hhmm(hour, minute, ampm: str | None) -> str:
    """The time pickers' hour, minute and AM/PM as HH:MM."""
    hour = int(hour)
    if ampm == "PM" and hour != 12:
        hour += 12
    elif ampm == "AM" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute}"


def _as_int(data: dict, key: str, message: str) -> None:
    if key in data:
        try:
            data[key] = int(data[key])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail=message)


@router.post("/panels/power/save", dependencies=[Depends(require_api_key)])
@router.post("/panels/system/save", dependencies=[Depends(require_api_key)])
async def save_system_settings_route(request: Request):
    """Every system-settings form in the admin page posts here (Hardware, Power, Updates, Developer)."""
    parsed = await read_form(request)
    dc = parsed.get("display_control", {})
    picked = dc.get("interval", {})
    times = {end: _hhmm(picked[f"{end}_h"], picked[f"{end}_m"], picked.get(f"{end}_ampm"))
             for end in ("start", "end") if f"{end}_h" in picked and f"{end}_m" in picked}
    if times:
        dc["interval"] = times
    _as_int(parsed, "brightness", "Brightness must be an integer")
    _as_int(parsed, "volume", "Volume must be an integer")
    if "wake" in dc:
        dc["wake"].setdefault("timeout_minutes", 5)
        _as_int(dc["wake"], "timeout_minutes", "The screen timeout must be a whole number of minutes")
        for key in ("extend", "presence"):
            if key in dc["wake"]:  # read_form already turns "true"/"false" (and checkbox pairs) into bools
                dc["wake"][key] = dc["wake"][key] in (True, "true")

    await update_system_settings(settings=parsed)
    if "ssh" in parsed:  # only the Developer card sends it
        on = parsed["ssh"] in (True, "true")
        return notify("SSH is on. Log in with: ssh pi@mirrordash.local" if on else "SSH is off.",
                      **{"md-ssh": {"on": on}})
    return notify("Saved.")
