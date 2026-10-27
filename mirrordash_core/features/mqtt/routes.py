# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The MQTT card (Settings tab, ARCHITECTURE #42): the owner's broker, and what Home Assistant may see and do.
Everything starts as read-only: control of the screen, of which modules run and of what a module exposes is
turned on here, one by one."""

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from mirrordash_core.admin import events_header, require_api_key, templates
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.mqtt.bridge import (
    declarations,
    mqtt_bridge,
    new_client_id,
    new_id,
)
from mirrordash_core.forms import read_form

router = APIRouter(prefix="/admin")

HOST = re.compile(r"[A-Za-z0-9.:\[\]-]{1,253}")


def _printable(value, limit: int) -> str:
    text = str(value or "")
    if len(text) > limit or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise HTTPException(status_code=400, detail=f"At most {limit} characters, on one line")
    return text


def _shareable(config: dict) -> list[dict]:
    """The module instances that expose something, with what (for the card's choices)."""
    from mirrordash_core.features.modules.loader import SAFE_NAME, find_entry_point
    from mirrordash_core.features.settings.schema import get_module_schema
    found = []
    for instance, cfg in config.get("modules", {}).items():
        if isinstance(cfg, dict) and SAFE_NAME.match(instance):
            ep = find_entry_point(str(cfg.get("module", instance)))
            if entities := declarations(get_module_schema(ep) if ep else None):
                found.append({"id": instance, "entities": entities})
    return found


def _mqtt_card(request: Request, message: str = "", kind: str = "success"):
    config = load_config()
    cfg = config.get("mqtt", {})
    return templates.TemplateResponse(
        request=request, name="admin_mqtt.html",
        context={"cfg": cfg, "has_password": bool(cfg.get("password")), "status": mqtt_bridge.status,
                 "modules": _shareable(config), "choices": cfg.get("modules") or {}},
        headers=events_header(**{"md-notify": {"message": message, "kind": kind}}) if message else None,
    )


@router.get("/panels/system/mqtt", dependencies=[Depends(require_api_key)])
async def get_mqtt_card(request: Request):
    return _mqtt_card(request)


@router.post("/panels/system/mqtt/save", dependencies=[Depends(require_api_key)])
async def save_mqtt(request: Request):
    form = await read_form(request)
    config = load_config()
    old = config.get("mqtt", {})
    host = str(form.get("host") or "").strip()
    if not HOST.fullmatch(host):
        raise HTTPException(status_code=400, detail="The broker is a host name or an IP address")
    port = str(form.get("port") or "").strip()
    if port and not (port.isdigit() and 1 <= int(port) <= 65535):
        raise HTTPException(status_code=400, detail="The port is a number from 1 to 65535")
    password = _printable(form.get("password"), 256)
    shared, control = form.get("share") or {}, form.get("control") or {}
    choices = {}
    for module in _shareable(config):
        allowed = control.get(module["id"]) if isinstance(control, dict) else None
        allowed = [allowed] if isinstance(allowed, str) else allowed or []
        choices[module["id"]] = {
            "share": isinstance(shared, dict) and shared.get(module["id"]) in ("on", "true"),
            "control": sorted(n for n in allowed if n in module["entities"] and module["entities"][n]["control"])}
    config["mqtt"] = {
        "enabled": True, "id": old.get("id") or new_id(), "client_id": old.get("client_id") or new_client_id(), "host": host, "port": int(port) if port else None,
        "username": _printable(form.get("username"), 128), "password": password or old.get("password", ""),
        "tls": form.get("tls") in ("on", "true"),
        "control_screen": form.get("control_screen") in ("on", "true"),
        "control_modules": form.get("control_modules") in ("on", "true"),
        "modules": choices}
    save_config(config)
    await mqtt_bridge.restart()
    return _mqtt_card(request, "MQTT saved. The mirror connects to the broker.")


@router.post("/panels/system/mqtt/off", dependencies=[Depends(require_api_key)])
async def mqtt_off(request: Request):
    removed = await mqtt_bridge.forget()  # Home Assistant removes the devices while the connection is still up
    config = load_config()
    config.setdefault("mqtt", {})["enabled"] = False
    save_config(config)
    if removed:
        return _mqtt_card(request, "MQTT is off, and the mirror is gone from Home Assistant.")
    return _mqtt_card(request, "MQTT is off. The mirror wasn't connected, so delete it in Home Assistant "
                               "(Settings → Devices) if it is still there.", "warning")
