# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Wi-Fi setup: the captive portal on the mirror's own hotspot and joining the home network."""

import asyncio
import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from mirrordash_core.admin import notify, require_api_key, templates
from mirrordash_core.config import load_config
from mirrordash_core.features.dashboard.telemetry import get_wifi_info
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules.loader import module_loader
from mirrordash_core.features.wifi.network import (connect_wifi, failed_switch, forget_hotspot_state,
                                                   is_wifi_hotspot_active, restore_captive_ap, scan_wifi_networks,
                                                   switch_wifi)
from mirrordash_core.host import reboot_system

logger = logging.getLogger("mirrordash.core.wifi")
router = APIRouter()


async def check_wifi_auth(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Validate X-API-Key if admin password is configured."""
    config = load_config()
    auth = config.get("admin_auth")
    if not auth:
        # No admin auth is set up yet (e.g. captive portal on fresh install), allow anyone to configure wifi
        return

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing password in X-API-Key header")

    expected_hash = auth.get("hash")
    salt = auth.get("salt")
    if not expected_hash or not salt:
        raise HTTPException(status_code=500, detail="Invalid admin auth config")

    from mirrordash_core.admin import hash_password
    provided_hash = hash_password(x_api_key, salt)
    if not secrets.compare_digest(provided_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid password")


# On the setup hotspot every DNS name answers with the mirror (dnsmasq-shared.d in the OS image),
# so this name works without typing an IP address.
SETUP_HOSTS = ("mirrordash.setup", "10.42.0.1")


SETUP_URL = "http://mirrordash.setup/wifi-setup"


# What the setup page itself needs; everything else is redirected to it
SETUP_PATHS = ("/wifi-setup", "/static", "/api/wifi", "/health", "/admin/auth/status")


async def captive_portal_redirect(request: Request, call_next):
    """While the hotspot is up, answer everything else with a redirect to the setup page. A phone's
    connectivity check (captive.apple.com/hotspot-detect.html, .../generate_204, ...) then gets
    the redirect instead of its expected answer and shows "Sign in to network"."""
    host = request.headers.get("host", "")
    is_local = "localhost" in host or "127.0.0.1" in host
    if not is_local and not request.url.path.startswith(SETUP_PATHS) and await is_wifi_hotspot_active():
        return RedirectResponse(url=SETUP_URL, status_code=302)
    return await call_next(request)


@router.get("/wifi-setup")
async def get_wifi_setup(request: Request):
    return templates.TemplateResponse(request=request, name="wifi_setup.html",
                                      context={"hotspot": await is_wifi_hotspot_active()})


@router.get("/api/wifi/scan", dependencies=[Depends(check_wifi_auth)])
async def get_wifi_scan() -> dict:
    networks = await scan_wifi_networks()
    return {"networks": networks}


@router.post("/api/wifi/setup", dependencies=[Depends(check_wifi_auth)])
async def post_wifi_setup(body: dict) -> dict:
    ssid = body.get("ssid")
    password = body.get("password")
    timezone = body.get("timezone")
    if not ssid:
        return {"status": "error", "message": "SSID is required"}

    # Connecting tears the setup hotspot down first, so a phone on it gets no answer either way
    from_hotspot = await is_wifi_hotspot_active()
    success, message = await connect_wifi(ssid, password)
    if not success and from_hotspot:
        # Otherwise the mirror is left with neither Wi-Fi nor hotspot until it's unplugged.
        # Bring MirrorDash-Setup back (same password) so the phone can try again; restart only if that fails.
        logger.warning(f"Could not connect to '{ssid}' from the setup hotspot; bringing it back.")
        if not await restore_captive_ap():
            await reboot_system(delay_sec=3.0)
    if success:
        if timezone:
            from mirrordash_core.config import load_config, save_config
            from mirrordash_core.host import apply_system_timezone

            # Save timezone to config
            config = load_config()
            config.setdefault("globals", {})["timezone"] = timezone

            save_config(config)

            # Apply system timezone
            await apply_system_timezone(timezone)

        # No restart needed: the mirror's screen notices the hotspot is gone and reloads, the clock and
        # mirrordash.local follow the new network by themselves. Modules start over to fetch data now.
        asyncio.create_task(module_loader.reload_modules())
        return {"status": "success", "message": "Connected."}
    else:
        return {"status": "error", "message": message}


# Settings → Wi-Fi: change the mirror's network from the admin page
@router.get("/admin/panels/wifi", dependencies=[Depends(require_api_key)])
async def get_wifi_card(request: Request):
    return templates.TemplateResponse(request=request, name="admin_wifi.html", context={
        "network": await get_wifi_info(), "networks": await scan_wifi_networks(), "failed": failed_switch})


_switch_task: asyncio.Task | None = None


@router.post("/admin/panels/wifi/connect", dependencies=[Depends(require_api_key)])
async def post_wifi_connect(request: Request):
    """Answers first, then switches: the phone loses the mirror the moment it changes network."""
    global _switch_task
    form = await request.form()
    ssid = str(form.get("ssid") or "").strip()
    if not ssid:
        raise HTTPException(status_code=400, detail="Choose a network")
    if _switch_task and not _switch_task.done():
        raise HTTPException(status_code=409, detail="The mirror is already changing network")

    password = str(form.get("password") or "") or None

    async def later():
        await asyncio.sleep(1)  # lets this answer reach the phone first
        await switch_wifi(ssid, password)
    _switch_task = asyncio.create_task(later())
    return notify(f"The mirror is switching to {ssid}. Put your phone on {ssid} too, then open "
                  "mirrordash.local. If it can't join, it goes back to the network it was on.")


@router.post("/api/wifi/changed")
async def post_wifi_changed(request: Request) -> dict:
    """The OS image's Wi-Fi watch calls this after it turned the hotspot on or off, so the mirror's
    screen reloads into the setup prompt or the mirror. Only from the mirror itself."""
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Only from the mirror itself")
    forget_hotspot_state()
    await manager.broadcast({"action": "reload"})
    return {"status": "success"}
