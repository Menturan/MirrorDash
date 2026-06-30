# Licensed under the PolyForm Noncommercial License 1.0.0.

import secrets
import string
from fastapi import APIRouter, Body, Depends, HTTPException

from mirrordash_core.config import load_config, save_config
from mirrordash_core.system import is_wifi_hotspot_active, remount_ro, remount_rw
from mirrordash_core.api.admin_shared import hash_password, require_api_key

router = APIRouter()

RECOVERY_PIN: str | None = None


def get_recovery_pin() -> str:
    global RECOVERY_PIN
    if RECOVERY_PIN is None:
        RECOVERY_PIN = "".join(secrets.choice(string.digits) for _ in range(6))
    return RECOVERY_PIN


def clear_recovery_pin() -> None:
    global RECOVERY_PIN
    RECOVERY_PIN = None


@router.get("/auth/status")
async def get_auth_status() -> dict:
    config = load_config()
    auth = config.get("admin_auth")
    auth_is_valid = bool(auth and auth.get("hash") and auth.get("salt"))

    # setup_required = True only when there is NO auth entry at all (first-boot).
    setup_required = auth is None
    # auth_corrupt = True when an entry exists but is unusable.
    auth_corrupt = auth is not None and not auth_is_valid

    if auth_corrupt:
        # Side-effect: generate/retain the recovery PIN in memory
        get_recovery_pin()

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


@router.post("/auth/recover")
async def recover_auth(body: dict = Body(...)) -> dict:
    """Recover from a corrupt admin auth entry using the memory-stored Recovery PIN."""
    global RECOVERY_PIN

    config = load_config()
    auth = config.get("admin_auth")
    auth_is_valid = bool(auth and auth.get("hash") and auth.get("salt"))

    # We only allow recovery if the configuration is actually corrupt
    if auth is None or auth_is_valid:
        raise HTTPException(status_code=400, detail="Recovery not available. Password is valid or not set.")

    provided_pin = body.get("pin")
    new_password = body.get("new_password")

    if not provided_pin or not RECOVERY_PIN or provided_pin.replace(" ", "") != RECOVERY_PIN:
        raise HTTPException(status_code=401, detail="Invalid Recovery PIN")

    if not new_password or len(new_password) < 4:
        raise HTTPException(status_code=400, detail="New password must be at least 4 characters")

    salt = secrets.token_hex(16)
    hashed_pw = hash_password(new_password, salt)

    config["admin_auth"] = {
        "hash": hashed_pw,
        "salt": salt,
    }

    await remount_rw()
    try:
        save_config(config)
        clear_recovery_pin()
        return {"status": "success", "message": "Admin password restored successfully"}
    finally:
        await remount_ro()


@router.post("/auth/forgot-password")
async def forgot_password() -> dict:
    """Initiate password recovery by corrupting the current auth block,
    generating a Recovery PIN, and telling the kiosk screen to reload.
    """
    config = load_config()
    auth = config.get("admin_auth")
    if not auth:
        raise HTTPException(status_code=400, detail="Password has not been set yet.")

    # Corrupt the admin_auth configuration (delete hash key if exists)
    config["admin_auth"] = {
        "salt": "forgotten"
    }

    await remount_rw()
    try:
        save_config(config)
        # Ensure the recovery PIN is active in memory
        get_recovery_pin()
        
        # Broadcast reload so the physical kiosk immediately loads the PIN screen
        try:
            from mirrordash_core.app import manager
            await manager.broadcast({"action": "reload"})
        except Exception:
            pass
            
        return {"status": "success", "message": "Password recovery mode initialized."}
    finally:
        await remount_ro()

