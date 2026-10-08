# Licensed under the PolyForm Noncommercial License 1.0.0.

"""The API for other systems, such as Home Assistant: /api/v1, with the token from the API Access
card (`Authorization: Bearer <token>`). It can read the status and run the screen, nothing else."""

from fastapi import APIRouter, Body, Depends, HTTPException

from mirrordash_core.api.admin_shared import require_api_token
from mirrordash_core.config import get_core_version, load_config

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_token)])


@router.get("/status")
async def get_status() -> dict:
    from mirrordash_core.display_power import display_power_manager
    from mirrordash_core.hardware import PRESENCE_TYPES, find_device, gpio_inputs, read_fan_state, read_sensor
    from mirrordash_core.module_loader import module_loader
    from mirrordash_core.system import display
    from mirrordash_core.system.telemetry import get_cpu_temperature, get_uptime_seconds

    system_cfg = load_config().get("system", {})
    sensors = {}
    for device_type in ("dht11", "light"):
        if find_device(system_cfg, device_type):
            sensors.update(await read_sensor(device_type) or {})  # cached for 30 s, no extra reads
    if any(find_device(system_cfg, t) for t in PRESENCE_TYPES):
        sensors["motion"] = gpio_inputs.motion_active
    fan = read_fan_state()
    if fan:
        sensors["fan_level"] = fan["level"]
    return {
        "version": get_core_version(),
        "uptime_seconds": round(get_uptime_seconds()),
        "cpu_temperature_c": get_cpu_temperature(),
        "screen_on": display_power_manager.is_on,
        "brightness": system_cfg.get("brightness", 100),
        "brightness_supported": display.brightness_supported is not False,
        "modules": sorted(module_loader.tasks),
        "sensors": sensors,
    }


@router.post("/screen")
async def set_screen(body: dict = Body(...)) -> dict:
    """{"state": "on"} wakes the screen (for "timeout_minutes" if given), {"state": "off"} turns it off."""
    from mirrordash_core.api.admin_system import update_screen_state
    return await update_screen_state(body)


@router.post("/brightness")
async def set_brightness(body: dict = Body(...)) -> dict:
    """{"value": 10-100}: saved like the admin page's slider, and applied right away."""
    from mirrordash_core.api.admin_system import update_system_settings
    value = body.get("value")
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=400, detail="value must be a whole number from 10 to 100")
    await update_system_settings(settings={"brightness": value})
    return {"status": "success", "brightness": value}
