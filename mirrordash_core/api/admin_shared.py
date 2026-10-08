# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import binascii
import hashlib
import json
import secrets
import uuid
from pathlib import Path
from typing import Annotated, Awaitable, Callable

from fastapi import Header, HTTPException
from fastapi.responses import HTMLResponse
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
        raise HTTPException(
            status_code=403,
            detail="Admin auth config is corrupt or incomplete. Please reset the password."
        )

    provided_hash = hash_password(x_api_key, salt)
    if not secrets.compare_digest(provided_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid password")


def token_hash(token: str) -> str:
    # A plain sha256 is enough: the token is 256 random bits, nothing to guess from a list.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_api_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency for /api/v1: `Authorization: Bearer <token>` with the token from the
    API Access card. The admin password is not accepted here."""
    stored = load_config().get("api_token", {}).get("hash")
    scheme, _, token = (authorization or "").partition(" ")
    if not stored or scheme.lower() != "bearer" or not secrets.compare_digest(token_hash(token.strip()), stored):
        raise HTTPException(status_code=401, detail="Missing or wrong API token. Create one under Hardware > API Access.",
                            headers={"WWW-Authenticate": "Bearer"})


# Changes on every process start, so clients can tell for certain that a restart finished.
BOOT_ID = uuid.uuid4().hex

# Package operations (venv copy + uv install) take minutes on a Pi 3, longer than nginx's
# proxy timeout, so they run in the background and the admin UI polls /admin/jobs/current.
# ponytail: one job at a time and state lives in memory; that matches the A/B venv swap,
# which can't run concurrently anyway, and a restart ends every job.
_job: dict = {"id": None, "state": "idle", "error": ""}
_job_task: asyncio.Task | None = None


def start_job(work: Callable[[], Awaitable[object]]) -> str | None:
    """Run work() in the background. Returns the job id, or None if a job is already running."""
    global _job_task
    if _job["state"] == "running":
        return None
    job_id = uuid.uuid4().hex[:8]
    _job.update(id=job_id, state="running", error="")

    async def runner():
        try:
            await work()
            _job["state"] = "restarting"  # every package operation ends by restarting the app
        except Exception as e:
            _job.update(state="failed", error=str(getattr(e, "detail", "") or e))

    _job_task = asyncio.create_task(runner())
    return job_id


def job_status() -> dict:
    return {"boot_id": BOOT_ID, **_job}


def events_header(**events) -> dict:
    """Response header that fires page events after the HTMX swap (admin.html listens for them).

    Used instead of returning <script> tags: a script that rewrote the swap target while htmx
    was still inserting it crashed htmx mid-swap.
    """
    return {"HX-Trigger-After-Swap": json.dumps(events)}


def ui_events(**events) -> HTMLResponse:
    """Empty HTMX response that only fires page events."""
    return HTMLResponse(content="", headers=events_header(**events))


def notify(message: str, kind: str = "success", **more_events) -> HTMLResponse:
    """Show a status message in the admin page (more_events: extra page events to fire)."""
    return ui_events(**{"md-notify": {"message": message, "kind": kind}}, **more_events)


def job_response(job_id: str | None, title: str, message: str, success_msg: str, target_panel: str = "") -> HTMLResponse:
    """Show the progress overlay and follow the job until the app has restarted."""
    if job_id is None:
        return notify("Another install or update is still running. Please wait for it to finish.", "error")
    return ui_events(**{"md-follow": {"jobId": job_id, "bootId": BOOT_ID, "title": title, "message": message,
                                      "successMsg": success_msg, "targetPanel": target_panel}})
