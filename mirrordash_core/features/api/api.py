# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The API for other systems (scripts, Home Assistant, the Matter bridge): /api/v1, with the token
from the API card (`Authorization: Bearer <token>`). It can read the status and run the screen,
nothing else. Its own app, so only this API has a description: /api/v1/docs and /api/v1/openapi.json."""

from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from mirrordash_core.admin import require_api_token
from mirrordash_core.config import get_core_version, load_config

import logging

logger = logging.getLogger("mirrordash.core.api")
api = FastAPI(title="MirrorDash API", version=get_core_version(), redoc_url=None,
              description="Read the mirror's status and run its screen. Every call needs `Authorization: Bearer <token>`, "
                          "with the token from the admin page's Settings > API card.")


class Sensors(BaseModel):
    """Only the sensors that are connected."""
    temperature_c: float | None = None
    humidity: int | None = Field(None, description="Relative humidity, %")
    lux: float | None = None
    motion: bool | None = Field(None, description="Someone in front of the mirror (PIR or mmWave)")
    fan_level: int | None = None


class Status(BaseModel):
    version: str
    uptime_seconds: int
    cpu_temperature_c: float | None
    screen_on: bool
    brightness: int = Field(description="10-100")
    brightness_supported: bool
    modules: list[str] = Field(description="The modules running")
    sensors: Sensors


class Screen(BaseModel):
    state: Literal["on", "off"]
    timeout_minutes: float | None = Field(None, gt=0, le=1440, description="With on: how long it stays on. "
                                          "Without it: the screen timeout from the Power tab.")


class Brightness(BaseModel):
    value: int = Field(ge=10, le=100, strict=True)


class Done(BaseModel):
    status: str = "success"
    message: str | None = None
    brightness: int | None = None


@api.get("/status", response_model=Status, response_model_exclude_none=True,
         dependencies=[Depends(require_api_token)])
async def get_status() -> dict:
    """The core version, uptime, CPU temperature, screen, brightness, running modules and sensor readings."""
    from mirrordash_core.features.power.display_power import display_power_manager
    from mirrordash_core.features.hardware.devices import PRESENCE_TYPES, find_device, gpio_inputs, read_fan_state, read_sensor
    from mirrordash_core.features.modules.loader import module_loader
    from mirrordash_core.features.hardware import display
    from mirrordash_core.features.dashboard.telemetry import get_cpu_temperature, get_uptime_seconds

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


@api.post("/screen", response_model=Done, response_model_exclude_none=True, dependencies=[Depends(require_api_token)])
async def set_screen(body: Screen) -> dict:
    """Wake the screen, or turn it off."""
    from mirrordash_core.features.power.routes import update_screen_state
    return await update_screen_state(body.model_dump(exclude_none=True))


@api.post("/brightness", response_model=Done, response_model_exclude_none=True, dependencies=[Depends(require_api_token)])
async def set_brightness(body: Brightness) -> dict:
    """Saved like the admin page's slider, and applied right away."""
    from mirrordash_core.system_settings import update_system_settings
    await update_system_settings(settings={"brightness": body.value})
    return {"status": "success", "brightness": body.value}
