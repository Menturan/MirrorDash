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
    auth = config.get("admin_auth")
    # setup_required is True when auth is absent OR when the stored entry is
    # corrupt (missing or empty hash / salt keys).
    setup_required = (
        not auth
        or not auth.get("hash")
        or not auth.get("salt")
    )
    hotspot_active = await is_wifi_hotspot_active()
    return {
        "setup_required": setup_required,
        # auth_existed tells the frontend whether an auth entry was present but
        # found to be corrupt, so it can show the right prompt message.
        "auth_existed": auth is not None,
        "wifi_hotspot_active": hotspot_active
    }


@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    password = body.get("password")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    config = load_config()
    auth = config.get("admin_auth")
    # Allow setup (or re-setup) when auth is absent OR corrupt.
    auth_is_valid = auth and auth.get("hash") and auth.get("salt")
    if auth_is_valid:
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
