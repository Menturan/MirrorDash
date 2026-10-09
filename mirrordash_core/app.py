# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mirrordash_core.config import load_config
from mirrordash_core.ws_manager import manager
from mirrordash_core.module_loader import module_loader
from mirrordash_core.api.admin import router as admin_router
from mirrordash_core.api.backup import router as backup_router
from mirrordash_core.api.public import router as public_router
from mirrordash_core.system import scan_wifi_networks, connect_wifi, reboot_system, is_wifi_hotspot_active, get_hotspot_password, restore_captive_ap
from mirrordash_core.system.network import HOTSPOT_SSID

from mirrordash_core.display_power import display_power_manager
from mirrordash_core.hardware import gpio_inputs

import secrets
from typing import Annotated

logger = logging.getLogger("mirrordash.core.app")

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

    from mirrordash_core.api.admin import hash_password
    provided_hash = hash_password(x_api_key, salt)
    if not secrets.compare_digest(provided_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await module_loader.start_modules()
        await display_power_manager.start()
        await gpio_inputs.start()
    except Exception as e:
        logger.error(f"Error during module startup: {e}", exc_info=True)
    yield
    await gpio_inputs.stop()
    await display_power_manager.stop()
    await module_loader.stop_modules()

app = FastAPI(lifespan=lifespan)

PACKAGE_DIR = Path(__file__).parent.resolve()
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))

# CORS — allow same-origin and local network access for admin panel
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# On the setup hotspot every DNS name answers with the mirror (dnsmasq-shared.d in the OS image),
# so this name works without typing an IP address.
SETUP_HOSTS = ("mirrordash.setup", "10.42.0.1")
SETUP_URL = "http://mirrordash.setup/wifi-setup"
# What the setup page itself needs; everything else is redirected to it
SETUP_PATHS = ("/wifi-setup", "/static", "/api/wifi", "/health", "/admin/auth/status")


@app.middleware("http")
async def captive_portal_redirect(request: Request, call_next):
    """While the hotspot is up, answer everything else with a redirect to the setup page. A phone's
    connectivity check (captive.apple.com/hotspot-detect.html, .../generate_204, ...) then gets
    the redirect instead of its expected answer and shows "Sign in to network"."""
    host = request.headers.get("host", "")
    is_local = "localhost" in host or "127.0.0.1" in host
    if not is_local and not request.url.path.startswith(SETUP_PATHS) and await is_wifi_hotspot_active():
        return RedirectResponse(url=SETUP_URL, status_code=302)
    return await call_next(request)

# Register admin API router
app.include_router(admin_router)
app.include_router(backup_router)
app.include_router(public_router)

# Serve static files
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

# HTML pages routes
@app.get("/")
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
            from mirrordash_core.api.admin_auth import get_recovery_pin
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

@app.get("/wifi-setup")
async def get_wifi_setup(request: Request):
    return templates.TemplateResponse(request=request, name="wifi_setup.html",
                                      context={"hotspot": await is_wifi_hotspot_active()})

@app.get("/api/wifi/scan", dependencies=[Depends(check_wifi_auth)])
async def get_wifi_scan() -> dict:
    networks = await scan_wifi_networks()
    return {"networks": networks}

@app.post("/api/wifi/setup", dependencies=[Depends(check_wifi_auth)])
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
            from mirrordash_core.system import apply_system_timezone

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

@app.get("/admin")
async def get_admin(request: Request):
    # The page shell is public; every panel (dashboard included) is loaded after login via
    # the authenticated /admin/panels/* routes, so nothing system-specific is rendered here.
    boot_status = os.environ.get("MIRRORDASH_BOOT_STATUS", "normal")
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={"boot_status": boot_status}
    )


@app.get("/design")
async def get_design() -> FileResponse:
    return FileResponse(str(PACKAGE_DIR / "static" / "design.html"))

@app.get("/health")
async def health() -> dict:
    from mirrordash_core.api.admin_shared import BOOT_ID
    return {"status": "ok", "modules": list(module_loader.tasks.keys()), "boot_status": os.environ.get("MIRRORDASH_BOOT_STATUS", "normal"), "boot_id": BOOT_ID}

@app.get("/api/active-modules")
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
@app.websocket("/ws")
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
