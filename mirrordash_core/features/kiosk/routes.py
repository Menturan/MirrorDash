# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The mirror's own screen: the page, its WebSocket and what it asks the server for."""

import logging
import os

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse

from mirrordash_core.admin import PACKAGE_DIR, templates
from mirrordash_core.config import load_config
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules.loader import module_loader
from mirrordash_core.features.wifi.network import HOTSPOT_SSID, get_hotspot_password, is_wifi_hotspot_active
from mirrordash_core.features.wifi.routes import SETUP_HOSTS

logger = logging.getLogger("mirrordash.core.kiosk")
router = APIRouter()


# HTML pages routes
@router.get("/")
async def get_index(request: Request):
    host = request.headers.get("host", "")
    if host.split(":")[0] in SETUP_HOSTS or request.query_params.get("captive") == "true":
        return RedirectResponse(url="/wifi-setup")
    
    if await is_wifi_hotspot_active():
        # The password goes to the mirror's own screen only: a phone on the hotspot can send any
        # Host header (the captive-portal redirect trusts "localhost"), but not a loopback address.
        on_mirror = request.client.host in ("127.0.0.1", "::1")
        return templates.TemplateResponse(request=request, name="wifi_prompt.html", context={
            "ssid": HOTSPOT_SSID,
            "password": await get_hotspot_password() if on_mirror else "",
        })

    config = load_config()
    auth = config.get("admin_auth")
    auth_is_valid = bool(auth and auth.get("hash") and auth.get("salt"))
    auth_corrupt = auth is not None and not auth_is_valid
    setup_required = auth is None

    if setup_required or auth_corrupt:
        host = request.headers.get("host", "")
        # Check loopback connection or Host header to verify if it's the kiosk screen
        is_kiosk = (
            request.client.host in ("127.0.0.1", "::1")
            or "localhost" in host
            or "127.0.0.1" in host
        )

        recovery_pin_str = ""
        if auth_corrupt:
            from mirrordash_core.features.auth.routes import get_recovery_pin
            raw_pin = get_recovery_pin()
            if len(raw_pin) == 6:
                recovery_pin_str = f"{raw_pin[:3]} {raw_pin[3:]}"
            else:
                recovery_pin_str = raw_pin

        return templates.TemplateResponse(
            request=request,
            name="admin_prompt.html",
            context={
                "auth_corrupt": auth_corrupt,
                "is_kiosk": is_kiosk,
                "recovery_pin": recovery_pin_str
            }
        )

    return FileResponse(str(PACKAGE_DIR / "static" / "index.html"))


@router.get("/design")
async def get_design() -> FileResponse:
    return FileResponse(str(PACKAGE_DIR / "static" / "design.html"))


@router.get("/health")
async def health() -> dict:
    from mirrordash_core.admin import BOOT_ID
    return {"status": "ok", "modules": list(module_loader.tasks.keys()), "boot_status": os.environ.get("MIRRORDASH_BOOT_STATUS", "normal"), "boot_id": BOOT_ID}


@router.get("/api/active-modules")
async def get_active_modules() -> dict:
    modules_list = []
    for name, instance in module_loader.instances.items():
        config = getattr(instance, "config", {})
        position = config.get("position", "middle_center")

        module_type = config.get("module", name)
        default_title = module_type.replace("mirrordash_", "").replace("_", " ").title()
        if hasattr(instance, "translate"):
            title = instance.translate("title", default_title)
        elif hasattr(instance, "translations"):
            title = instance.translations.get("title", default_title)
        else:
            title = default_title

        modules_list.append({
            "name": name,
            "position": position,
            "title": title,
            "carousel_group": config.get("carousel_group"),
            "carousel_interval": config.get("carousel_interval", 15)
        })
    # Read boot status from environment
    boot_status = os.environ.get("MIRRORDASH_BOOT_STATUS", "normal")
    
    from mirrordash_core.config import load_config
    cfg = load_config()
    globals_cfg = cfg.get("globals", {})
    
    safe_margin_cfg = globals_cfg.get("safe_margin", {})
    if not isinstance(safe_margin_cfg, dict):
        safe_margin_cfg = {}
        
    safe_margin_top = f"{safe_margin_cfg.get('top', 60)}px"
    safe_margin_bottom = f"{safe_margin_cfg.get('bottom', 60)}px"
    safe_margin_left = f"{safe_margin_cfg.get('left', 60)}px"
    safe_margin_right = f"{safe_margin_cfg.get('right', 60)}px"

    return {
        "modules": modules_list,
        "boot_status": boot_status,
        "safe_margin_top": safe_margin_top,
        "safe_margin_bottom": safe_margin_bottom,
        "safe_margin_left": safe_margin_left,
        "safe_margin_right": safe_margin_right,
    }


# WebSocket communication endpoint
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        manager.disconnect(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
