# Licensed under the PolyForm Noncommercial License 1.0.0.

import binascii
import hashlib
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Header, HTTPException
from fastapi.templating import Jinja2Templates

from mirrordash_core.config import load_config

PACKAGE_DIR = Path(__file__).parent.parent.resolve()
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def hash_password(password: str, salt: str) -> str:
    """Hash a password using pbkdf2_hmac and sha256."""
    hash_bytes = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return binascii.hexlify(hash_bytes).decode('ascii')


async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency that validates the X-API-Key header against stored password."""
    config = load_config()
    auth = config.get("admin_auth")

    if not auth:
        raise HTTPException(status_code=403, detail="Admin password not set. Please complete setup.")

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing password in X-API-Key header")

    expected_hash = auth.get("hash")
    salt = auth.get("salt")

    if not expected_hash or not salt:
        raise HTTPException(status_code=500, detail="Invalid admin auth config")

    provided_hash = hash_password(x_api_key, salt)
    if not secrets.compare_digest(provided_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid password")
