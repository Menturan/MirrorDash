# Licensed under the PolyForm Noncommercial License 1.0.0.

import secrets
from fastapi import APIRouter, Body, Depends, HTTPException

from mirrordash_core.config import load_config, save_config
from mirrordash_core.system import is_wifi_hotspot_active, remount_ro, remount_rw
from mirrordash_core.api.admin_shared import hash_password, require_api_key

router = APIRouter()


@router.get("/auth/status")
async def get_auth_status() -> dict:
    config = load_config()
    auth = config.get("admin_auth")
    auth_is_valid = bool(auth and auth.get("hash") and auth.get("salt"))

    # setup_required = True only when there is NO auth entry at all (first-boot).
    setup_required = auth is None
    # auth_corrupt = True when an entry exists but is unusable.
    auth_corrupt = auth is not None and not auth_is_valid

    hotspot_active = await is_wifi_hotspot_active()
    return {
        "setup_required": setup_required,
        "auth_corrupt": auth_corrupt,
        "wifi_hotspot_active": hotspot_active,
    }


@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    """First-boot only. Sets the admin password when NO auth entry exists at all.
    Rejected if any admin_auth key is present (valid or corrupt) — use
    /auth/change-password to update an existing password.
    """
    password = body.get("password")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    config = load_config()
    if "admin_auth" in config:
        # Block unconditionally — even a corrupt entry must be recovered via
        # change-password (which requires the existing key or manual config fix),
        # not via this unauthenticated endpoint.
        raise HTTPException(status_code=400, detail="Password is already set")

    salt = secrets.token_hex(16)
    hashed_pw = hash_password(password, salt)

    config["admin_auth"] = {
        "hash": hashed_pw,
        "salt": salt,
    }

    await remount_rw()
    try:
        save_config(config)
        return {"status": "success", "message": "Admin password set successfully"}
    finally:
        await remount_ro()


@router.post("/auth/change-password", dependencies=[Depends(require_api_key)])
async def change_password(body: dict = Body(...)) -> dict:
    """Change the admin password. Requires the current password via X-API-Key header.
    This is the only legitimate way to update the password once it has been set.
    """
    new_password = body.get("new_password")
    if not new_password or len(new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters")

    config = load_config()
    salt = secrets.token_hex(16)
    hashed_pw = hash_password(new_password, salt)

    config["admin_auth"] = {
        "hash": hashed_pw,
        "salt": salt,
    }

    await remount_rw()
    try:
        save_config(config)
        return {"status": "success", "message": "Admin password changed successfully"}
    finally:
        await remount_ro()
