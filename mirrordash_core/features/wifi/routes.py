# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Wi-Fi setup: the captive portal on the mirror's own hotspot and joining the home network."""

import asyncio
import logging
import os
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse

from mirrordash_core.admin import (
    check_password,
    from_the_mirrors_os,
    notify,
    require_api_key,
    start_job,
    templates,
)
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.dashboard.telemetry import get_wifi_info
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.wifi.network import (
    connect_wifi,
    failed_switch,
    forget_hotspot_state,
    is_wifi_hotspot_active,
    restore_captive_ap,
    scan_wifi_networks,
    switch_wifi,
)
from mirrordash_core.host import (
    apply_system_timezone,
    is_known_timezone,
    reboot_system,
    run_in_background,
)

logger = logging.getLogger("mirrordash.core.wifi")
router = APIRouter()


async def check_wifi_auth(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Validate X-API-Key if admin password is configured, with the login's lockout after wrong tries."""
    if not load_config().get("admin_auth"):
        # No admin auth is set up yet (e.g. captive portal on fresh install), allow anyone to configure wifi
        return

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing password in X-API-Key header")
    if not await check_password(x_api_key, request.client.host if request.client else ""):
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
    """Join the home network from the setup page, and take the phone's time zone along. Open to anyone on
    the hotspot until the mirror has an admin password, so everything in `body` is checked here."""
    ssid, password, timezone = body.get("ssid"), body.get("password"), body.get("timezone")
    if not ssid or not isinstance(ssid, str):
        return {"status": "error", "message": "SSID is required"}
    if password is not None and not isinstance(password, str):
        return {"status": "error", "message": "The password must be text"}
    if timezone and not is_known_timezone(timezone):
        timezone = None  # the phone's time zone is a convenience: an unknown one is left out, not an error

    # Connecting tears the setup hotspot down first, so a phone on it gets no answer either way
    from_hotspot = await is_wifi_hotspot_active()
    success, message = await connect_wifi(ssid, password)
    if not success:
        if from_hotspot:
            # Otherwise the mirror is left with neither Wi-Fi nor hotspot until it's unplugged.
            # Bring MirrorDash-Setup back (same password) so the phone can try again; restart only if that fails.
            logger.warning(f"Could not connect to '{ssid}' from the setup hotspot; bringing it back.")
            if not await restore_captive_ap():
                await reboot_system(delay_sec=3.0)
        return {"status": "error", "message": message}

    if timezone:
        config = load_config()
        config.setdefault("globals", {})["timezone"] = timezone
        save_config(config)
        await apply_system_timezone(timezone)
    # No restart needed: the mirror's screen notices the hotspot is gone and reloads, the clock and
    # mirrordash.local follow the new network by themselves. Modules start over to fetch data now.
    run_in_background(module_host.reload_modules())
    return {"status": "success", "message": "Connected."}


# Big enough for a backup with local modules and their data; the phone sends it over the hotspot
RESTORE_MAX_BYTES = 500 * 1024 * 1024


@router.post("/api/wifi/restore", dependencies=[Depends(check_wifi_auth)])
async def post_wifi_restore(file: UploadFile = File(...), password: str = Form(default="")) -> dict:
    """Set a new mirror up from an encrypted backup: join a Wi-Fi network it carries, then restore it
    (modules come from GitHub, so the network comes first). Open like the rest of the setup page only
    while the mirror has no admin password: before that anyone near it could set it up anyway."""
    from mirrordash_core.features.backup.service import (
        backup_wifi,
        read_manifest,
        restore_backup,
        temp_upload,
    )
    size = 0
    with open(temp_upload(), "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > RESTORE_MAX_BYTES:
                out.close()
                os.remove(temp_upload())  # never leave half a file of that size on the card
                raise HTTPException(status_code=413, detail="That file is too big to be a backup.")
            out.write(chunk)
    try:
        manifest = await read_manifest(temp_upload(), password or None)
    except Exception:
        raise HTTPException(status_code=400, detail="That file isn't a MirrorDash backup, or it's from an older version.")
    if manifest is None:
        raise HTTPException(status_code=400, detail="Wrong password for this backup." if password
                            else "This backup is protected: enter its password.")
    networks = await backup_wifi(temp_upload(), password or None)
    if not networks:
        raise HTTPException(status_code=400, detail="This backup has no Wi-Fi networks (only a backup with a password "
                            "has them). Connect the mirror to Wi-Fi first, then restore it on the admin page.")

    async def join_then_restore():
        await asyncio.sleep(1)  # let the answer reach the phone before the hotspot goes
        for network in networks:
            if (await connect_wifi(network["ssid"], network.get("psk") or None))[0]:
                start_job(lambda: restore_backup(password=password or None))
                return
        logger.warning("Could not join any Wi-Fi network from the backup; bringing the setup hotspot back.")
        if not await restore_captive_ap():
            await reboot_system(delay_sec=3.0)
    run_in_background(join_then_restore())
    return {"status": "success", "networks": [network["ssid"] for network in networks]}


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

    async def answer_then_switch():
        await asyncio.sleep(1)  # lets this answer reach the phone first
        await switch_wifi(ssid, password)
    _switch_task = asyncio.create_task(answer_then_switch())
    return notify(f"The mirror is switching to {ssid}. Put your phone on {ssid} too, then open "
                  "mirrordash.local. If it can't join, it goes back to the network it was on.")


@router.post("/api/wifi/changed")
async def post_wifi_changed(request: Request) -> dict:
    """The OS image's Wi-Fi watch calls this after it turned the hotspot on or off, so the mirror's
    screen reloads into the setup prompt or the mirror. Only from the mirror's OS (the local token)."""
    if not from_the_mirrors_os(request):
        raise HTTPException(status_code=403, detail="Only from the mirror itself")
    forget_hotspot_state()
    await manager.broadcast({"action": "reload"})
    return {"status": "success"}
