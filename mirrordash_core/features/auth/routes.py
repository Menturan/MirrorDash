# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The admin password, and the recovery code that resets it.

The recovery code is shown once, when the password is set up (and again after each use, as a new
one). Lost both? Remove `admin_auth` from config.json over SSH or on the SD card (USER_GUIDE §1)."""

import logging
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException

from mirrordash_core.admin import hash_password, require_api_key
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.wifi.network import is_wifi_hotspot_active

logger = logging.getLogger("mirrordash.core.auth")
router = APIRouter(prefix="/admin")

MIN_PASSWORD = 4
# No 0/O, 1/I/L: the code is read off a screen and typed back
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _hashed(secret: str) -> dict:
    salt = secrets.token_hex(16)
    return {"hash": hash_password(secret, salt), "salt": salt}


def _normalize_code(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def _new_recovery_code(auth: dict) -> str:
    """Put a new recovery code in `auth` (only its hash) and return it formatted, XXXX-XXXX-XXXX.
    12 characters of 31 is ~59 bits behind PBKDF2: guessing it over the network isn't feasible."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    auth["recovery"] = _hashed(raw)
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def _check_new_password(password) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD} characters")
    return password


def auth_state(config: dict) -> tuple[bool, bool]:
    """(setup_required, auth_corrupt): no admin_auth at all, or one without a usable password."""
    auth = config.get("admin_auth")
    return auth is None, auth is not None and not (auth.get("hash") and auth.get("salt"))


@router.get("/auth/status")
async def get_auth_status() -> dict:
    setup_required, auth_corrupt = auth_state(load_config())
    return {
        "setup_required": setup_required,
        "auth_corrupt": auth_corrupt,
        "wifi_hotspot_active": await is_wifi_hotspot_active(),
    }


@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    """First start only: set the password when there is no admin_auth at all. Answers with the
    recovery code, the only time it is shown."""
    password = _check_new_password(body.get("password"))
    config = load_config()
    if "admin_auth" in config:
        # Even a corrupt entry: this endpoint needs no login, so it must never overwrite one
        raise HTTPException(status_code=400, detail="Password is already set")
    auth = _hashed(password)
    code = _new_recovery_code(auth)
    config["admin_auth"] = auth
    save_config(config)
    return {"status": "success", "recovery_code": code}


@router.post("/auth/change-password", dependencies=[Depends(require_api_key)])
async def change_password(body: dict = Body(...)) -> dict:
    """Change the password; the current one comes in X-API-Key. The recovery code stays."""
    new_password = _check_new_password(body.get("new_password"))
    config = load_config()
    config["admin_auth"] = {**config["admin_auth"], **_hashed(new_password)}
    save_config(config)
    return {"status": "success"}


@router.post("/auth/recover")
async def recover_auth(body: dict = Body(...)) -> dict:
    """Set a new password with the recovery code. The code is used up: the answer has a new one."""
    config = load_config()
    stored = (config.get("admin_auth") or {}).get("recovery") or {}
    code = _normalize_code(str(body.get("code") or ""))
    if not (stored.get("hash") and stored.get("salt") and code
            and secrets.compare_digest(hash_password(code, stored["salt"]), stored["hash"])):
        raise HTTPException(status_code=401, detail="Wrong recovery code")
    auth = _hashed(_check_new_password(body.get("new_password")))
    new_code = _new_recovery_code(auth)
    config["admin_auth"] = auth
    save_config(config)
    logger.warning("The admin password was reset with the recovery code")
    return {"status": "success", "recovery_code": new_code}
