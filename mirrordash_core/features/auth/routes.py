# SPDX-License-Identifier: AGPL-3.0-only
"""The admin password, and the recovery code that resets it.

The recovery code is shown once, when the password is set up (and again after each use, as a new
one). Lost both? Remove `admin_auth` from config.json over SSH or on the SD card (USER_GUIDE, "Lost the recovery code too?")."""

import asyncio
import logging
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from mirrordash_core.admin import (
    SESSION_COOKIE,
    check_password,
    check_throttle,
    end_all_sessions,
    end_session,
    hash_password,
    new_session,
    note_failure,
    require_api_key,
    session_valid,
    set_session_cookie,
)
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.wifi.network import is_wifi_hotspot_active

logger = logging.getLogger("mirrordash.core.auth")
router = APIRouter(prefix="/admin")

MIN_PASSWORD = 8  # also the backup password and SSH's; the admin page checks the same (admin.js)
# No 0/O, 1/I/L: the code is read off a screen and typed back
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


async def _hash_with_new_salt(secret: str) -> dict:
    """{"hash", "salt"} of a password or recovery code, as admin_auth keeps them."""
    salt = secrets.token_hex(16)
    return {"hash": await asyncio.to_thread(hash_password, secret, salt), "salt": salt}


def _normalize_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())  # "abcd-efgh" -> "ABCDEFGH"


async def _new_recovery_code(auth: dict) -> str:
    """Put a new recovery code in `auth` (only its hash) and return it formatted, XXXX-XXXX-XXXX.
    12 characters of 31 is ~59 bits behind PBKDF2: guessing it over the network isn't feasible."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))
    auth["recovery"] = await _hash_with_new_salt(code)
    return "-".join(code[start:start + 4] for start in range(0, 12, 4))


def _check_new_password(password) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise HTTPException(status_code=400, detail=f"Password must be at least {MIN_PASSWORD} characters")
    return password


def auth_state(config: dict) -> tuple[bool, bool]:
    """(setup_required, auth_corrupt): no admin_auth at all, or one without a usable password."""
    auth = config.get("admin_auth")
    return auth is None, auth is not None and not (auth.get("hash") and auth.get("salt"))


@router.get("/auth/status")
async def get_auth_status(request: Request) -> dict:
    manager.saw(request.client)  # the setup prompts on the mirror's screen poll this
    setup_required, auth_corrupt = auth_state(load_config())
    return {
        "setup_required": setup_required,
        "auth_corrupt": auth_corrupt,
        "logged_in": session_valid(request.cookies.get(SESSION_COOKIE)),  # the cookie is HttpOnly: JS can't see it
        "wifi_hotspot_active": await is_wifi_hotspot_active(),
    }


def _logged_in(body: dict) -> JSONResponse:
    """The answer, with a new login session for this browser (setup, recover, login, a new password)."""
    response = JSONResponse(body)
    set_session_cookie(response, new_session())
    return response


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/auth/login")
async def login(request: Request, body: dict = Body(...)) -> JSONResponse:
    """The password, once: the answer sets the session cookie the admin page then uses."""
    if not isinstance(body.get("password"), str) or not await check_password(body["password"], _client(request)):
        raise HTTPException(status_code=401, detail="Invalid password. Please try again.")
    return _logged_in({"status": "success"})


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    end_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"status": "success"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    """First start only: set the password when there is no admin_auth at all. Answers with the
    recovery code, the only time it is shown."""
    password = _check_new_password(body.get("password"))
    config = load_config()
    if "admin_auth" in config:
        # Even a corrupt entry: this endpoint needs no login, so it must never overwrite one
        raise HTTPException(status_code=400, detail="Password is already set")
    auth = await _hash_with_new_salt(password)
    code = await _new_recovery_code(auth)
    config["admin_auth"] = auth
    save_config(config)
    return _logged_in({"status": "success", "recovery_code": code})


@router.post("/auth/change-password", dependencies=[Depends(require_api_key)])
async def change_password(request: Request, body: dict = Body(...)) -> JSONResponse:
    """Change the password. The current one is asked again, so a logged-in page left open can't change
    it. Every other browser is logged out; this one gets a new session. The recovery code stays."""
    if not isinstance(body.get("current_password"), str) or not await check_password(body["current_password"], _client(request)):
        raise HTTPException(status_code=401, detail="The current password is wrong.")
    new_password = _check_new_password(body.get("new_password"))
    config = load_config()
    config["admin_auth"] = {**config["admin_auth"], **await _hash_with_new_salt(new_password)}
    save_config(config)
    end_all_sessions()
    return _logged_in({"status": "success"})


@router.post("/auth/recover")
async def recover_auth(request: Request, body: dict = Body(...)) -> JSONResponse:
    """Set a new password with the recovery code. The code is used up: the answer has a new one.
    Every browser is logged out but this one."""
    check_throttle(_client(request))
    config = load_config()
    stored = (config.get("admin_auth") or {}).get("recovery") or {}
    code = _normalize_code(str(body.get("code") or ""))
    if not (stored.get("hash") and stored.get("salt") and code
            and secrets.compare_digest(await asyncio.to_thread(hash_password, code, stored["salt"]), stored["hash"])):
        note_failure(_client(request))
        raise HTTPException(status_code=401, detail="Wrong recovery code")
    auth = await _hash_with_new_salt(_check_new_password(body.get("new_password")))
    new_code = await _new_recovery_code(auth)
    config["admin_auth"] = auth
    save_config(config)
    logger.warning("The admin password was reset with the recovery code")
    end_all_sessions()
    return _logged_in({"status": "success", "recovery_code": new_code})
