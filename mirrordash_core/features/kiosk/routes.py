# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The mirror's own screen: the page, its WebSocket and what it asks the server for."""

import json
import logging
import os
import re
import secrets
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from mirrordash_core.admin import PACKAGE_DIR, templates
from mirrordash_core.config import load_config
from mirrordash_core.features.auth.routes import auth_state
from mirrordash_core.features.kiosk.ws import manager, on_screen
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.loader import (
    find_entry_point,
    load_translations,
    package_dir,
)
from mirrordash_core.features.wifi.network import (
    HOTSPOT_SSID,
    get_hotspot_password,
    is_wifi_hotspot_active,
)
from mirrordash_core.features.wifi.routes import SETUP_HOSTS

logger = logging.getLogger("mirrordash.core.kiosk")
router = APIRouter()


def screen_csp(host: str, nonce: str) -> str:
    """The screen's Content-Security-Policy (ARCHITECTURE #41). Only scripts with the page's nonce run:
    the page's own and those core.js gives the cards whose module may run scripts (scripts_allowed); another
    card's `<script>`, `onerror=` or a frame of this site's (whose scripts would reach the page) never do.
    Cards show pictures and video from anywhere, but their JS talks only to this server (fetch, its
    WebSocket), and nothing frames the page, submits a form or embeds a plugin."""
    ws = f" ws://{host}" if re.fullmatch(r"[A-Za-z0-9.:\[\]-]{1,255}", host) else ""
    return (f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
            f"img-src * data: blob:; media-src * data: blob:; font-src 'self' data:; connect-src 'self'{ws}; "
            "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


# HTML pages routes
@router.get("/")
async def get_index(request: Request):
    host = request.headers.get("host", "")
    if host.split(":")[0] in SETUP_HOSTS or request.query_params.get("captive") == "true":
        return RedirectResponse(url="/wifi-setup")
    if not on_screen(request.client):
        # The screen is the mirror's own only (ARCHITECTURE #41): a module's card never runs in a browser
        # logged in to the admin page. A phone can send any Host header, but not a loopback address.
        return RedirectResponse(url="/admin")

    if await is_wifi_hotspot_active():
        page = templates.TemplateResponse(request=request, name="wifi_prompt.html", context={
            "ssid": HOTSPOT_SSID,
            "password": await get_hotspot_password(),
        })
    elif any(setup := auth_state(load_config())):  # no admin password yet, or a broken one
        page = templates.TemplateResponse(request=request, name="admin_prompt.html",
                                          context={"auth_corrupt": setup[1]})
    else:
        page = HTMLResponse((PACKAGE_DIR / "static" / "index.html").read_text())
    nonce = secrets.token_urlsafe(16)
    page = HTMLResponse(page.body.decode().replace("<script", f'<script nonce="{nonce}"'))
    page.headers["Content-Security-Policy"] = screen_csp(host, nonce)
    return page


@router.get("/design")
async def get_design() -> FileResponse:
    return FileResponse(str(PACKAGE_DIR / "static" / "design.html"))


@router.get("/health")
async def health(response: Response) -> dict:
    from mirrordash_core.admin import BOOT_ID
    from mirrordash_core.features.power.display_power import display_power_manager
    # None while the screen is off: there is nothing to watch then
    screen_seen = round(time.monotonic() - manager.screen_seen) if display_power_manager.is_on else None
    # The kiosk's start page (file://, origin "null") reads this; without it WebKit blocks the answer and
    # the screen stays on "MirrorDash didn't start". Nothing here is secret and no credentials ride along.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return {"status": "ok", "modules": list(module_host.running), "boot_status": os.environ.get("MIRRORDASH_BOOT_STATUS", "normal"),
            "boot_id": BOOT_ID, "screen_seen_seconds_ago": screen_seen}


@router.get("/api/active-modules")
async def get_active_modules() -> dict:
    cfg = load_config()
    modules_config = cfg.get("modules", {})
    lang = cfg.get("globals", {}).get("language", "en")
    modules_list = []
    for name in module_host.running:
        config = modules_config.get(name, {})
        position = config.get("position", "middle_center")

        module_type = config.get("module", name)
        default_title = module_type.replace("mirrordash_", "").replace("_", " ").title()
        ep = find_entry_point(module_type)  # its translations are read as files: the core runs no module code
        title = load_translations(package_dir(ep), lang).get("title", default_title) if ep else default_title

        modules_list.append({
            "name": name,
            "position": position,
            "title": title,
            "carousel_group": config.get("carousel_group"),
            "carousel_interval": config.get("carousel_interval", 15),
            "order": module_host._placement.get(name, {}).get("order"),
        })
    # Read boot status from environment
    boot_status = os.environ.get("MIRRORDASH_BOOT_STATUS", "normal")
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
    if not on_screen(websocket.client):  # the cards go to the mirror's own screen only, like the page
        await websocket.close()
        return
    await manager.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            manager.saw(websocket.client)
            # Where a module's video window is, asked by features/video/window.py
            if text.startswith("{"):
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") == "video_window":
                    from mirrordash_core.features.video.window import window_reported
                    window_reported(message.get("module"), message.get("rect"))
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        manager.disconnect(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
