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
    # The WebSocket's address named as well, for browsers (older WebKit) that don't count ws:// as 'self';
    # only when the Host header is a plain host name, so it can't add anything else to the policy
    websocket_source = f" ws://{host}" if re.fullmatch(r"[A-Za-z0-9.:\[\]-]{1,255}", host) else ""
    return (f"default-src 'self'; script-src 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
            f"img-src * data: blob:; media-src * data: blob:; font-src 'self' data:; connect-src 'self'{websocket_source}; "
            "frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@router.get("/")
async def get_index(request: Request):
    """The mirror's screen: the setup prompts while there's no network or admin password, then the mirror.
    Every `<script>` gets this answer's nonce, so only the page's own scripts run (screen_csp)."""
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
    elif any(setup_required_or_corrupt := auth_state(load_config())):  # no admin password yet, or a broken one
        _, auth_corrupt = setup_required_or_corrupt
        page = templates.TemplateResponse(request=request, name="admin_prompt.html",
                                          context={"auth_corrupt": auth_corrupt})
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


DEFAULT_SAFE_MARGIN_PX = 60  # the empty border round the screen, where a frame or bezel covers it


@router.get("/api/active-modules")
async def get_active_modules() -> dict:
    """What the screen lays out: each running instance's place, title and carousel, and the safe margins."""
    config = load_config()
    modules_config = config.get("modules", {})
    globals_config = config.get("globals", {})
    language = globals_config.get("language", "en")
    modules_list = []
    for instance_id in module_host.running:
        instance_config = modules_config.get(instance_id, {})
        module_name = instance_config.get("module", instance_id)
        default_title = module_name.replace("mirrordash_", "").replace("_", " ").title()
        ep = find_entry_point(module_name)  # its translations are read as files: the core runs no module code
        title = load_translations(package_dir(ep), language).get("title", default_title) if ep else default_title
        modules_list.append({
            "name": instance_id,
            "position": instance_config.get("position", "middle_center"),
            "title": title,
            "carousel_group": instance_config.get("carousel_group"),
            "carousel_interval": instance_config.get("carousel_interval", 15),
            "order": module_host._placement.get(instance_id, {}).get("order"),
        })

    safe_margin = globals_config.get("safe_margin", {})
    if not isinstance(safe_margin, dict):
        safe_margin = {}
    return {
        "modules": modules_list,
        "boot_status": os.environ.get("MIRRORDASH_BOOT_STATUS", "normal"),
        **{f"safe_margin_{side}": f"{safe_margin.get(side, DEFAULT_SAFE_MARGIN_PX)}px"
           for side in ("top", "bottom", "left", "right")},
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
