# Licensed under the PolyForm Noncommercial License 1.0.0.

import secrets
from fastapi import APIRouter, Body, HTTPException

from mirrordash_core.config import load_config, save_config
from mirrordash_core.system import is_wifi_hotspot_active, remount_ro, remount_rw
from mirrordash_core.api.admin_shared import hash_password

router = APIRouter()


@router.get("/auth/status")
async def get_auth_status() -> dict:
    config = load_config()
    setup_required = "admin_auth" not in config
    hotspot_active = await is_wifi_hotspot_active()
    return {
        "setup_required": setup_required,
        "wifi_hotspot_active": hotspot_active
    }


@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    password = body.get("password")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    config = load_config()
    if "admin_auth" in config:
        raise HTTPException(status_code=400, detail="Password is already set")

    salt = secrets.token_hex(16)
    hashed_pw = hash_password(password, salt)

    config["admin_auth"] = {
        "hash": hashed_pw,
        "salt": salt
    }

    await remount_rw()
    try:
        save_config(config)
        return {"status": "success", "message": "Admin password set successfully"}
    finally:
        await remount_ro()
