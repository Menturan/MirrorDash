# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# Licensed under the PolyForm Noncommercial License 1.0.0.

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
from mirrordash_core.system import scan_wifi_networks, connect_wifi, reboot_system, remount_rw, remount_ro

from mirrordash_core.display_power import display_power_manager

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
    except Exception as e:
        logger.error(f"Error during module startup: {e}", exc_info=True)
    yield
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

# Register admin API router
app.include_router(admin_router)
app.include_router(backup_router)

# Serve static files
app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

# HTML pages routes
@app.get("/")
async def get_index(request: Request):
    host = request.headers.get("host", "")
    if "10.42.0.1" in host or request.query_params.get("captive") == "true":
        return RedirectResponse(url="/wifi-setup")
    return FileResponse(str(PACKAGE_DIR / "static" / "index.html"))

@app.get("/wifi-setup")
async def get_wifi_setup(request: Request):
    return templates.TemplateResponse(request=request, name="wifi_setup.html")

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

    success, message = await connect_wifi(ssid, password)
    if success:
        if timezone:
            from mirrordash_core.config import load_config, save_config
            from mirrordash_core.system import apply_system_timezone

            # Save timezone to config
            config = load_config()
            config.setdefault("globals", {})["timezone"] = timezone

            await remount_rw()
            try:
                save_config(config)
            finally:
                await remount_ro()

            # Apply system timezone
            await apply_system_timezone(timezone)

        await reboot_system(delay_sec=3.0)
        return {"status": "success", "message": "Successfully connected! System is rebooting..."}
    else:
        return {"status": "error", "message": message}

@app.get("/admin")
async def get_admin(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")

@app.get("/design")
async def get_design() -> FileResponse:
    return FileResponse(str(PACKAGE_DIR / "static" / "design.html"))

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "modules": list(module_loader.tasks.keys())}

@app.get("/api/active-modules")
async def get_active_modules() -> dict:
    modules_list = []
    for name, instance in module_loader.instances.items():
        config = getattr(instance, "config", {})
        position = config.get("position", "middle_center")

        default_title = name.replace("mirrordash_", "").replace("mymm_", "").replace("_", " ").title()
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
    return {"modules": modules_list, "boot_status": boot_status}

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
