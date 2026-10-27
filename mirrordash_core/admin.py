# SPDX-License-Identifier: AGPL-3.0-only
"""What every admin feature shares: the page shell, login, templates, background jobs and page events."""

import asyncio
import binascii
import hashlib
import ipaddress
import json
import logging
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.templating import Jinja2Templates

from mirrordash_core.config import (
    get_base_dir,
    get_config_path,
    get_core_version,
    load_config,
    write_atomic,
)

PACKAGE_DIR = Path(__file__).parent.resolve()
# Each feature keeps its templates next to its code; the page shells live in templates/
templates = Jinja2Templates(directory=[str(PACKAGE_DIR / "templates"),
                                       *sorted(str(p) for p in PACKAGE_DIR.glob("features/*/templates"))])

logger = logging.getLogger("mirrordash.core.admin")
router = APIRouter(prefix="/admin")


def hash_password(password: str, salt: str) -> str:
    """Hash a password using pbkdf2_hmac and sha256."""
    hash_bytes = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return binascii.hexlify(hash_bytes).decode('ascii')


# --- Login: a session cookie, so the password is sent once, not with every request ---
# The admin page logs in with the password (features/auth: /admin/auth/login) and gets a random
# session token as an HttpOnly cookie; only its sha256 is kept, next to config.json, so a restart
# (every update ends with one) doesn't log anyone out. X-API-Key with the password still works for
# scripts and tests. A wrong password from one address (an IPv6 /64) 5 times in a minute is refused for a minute.
SESSION_COOKIE = "mirrordash_session"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES, FAILURE_WINDOW = 5, 60
_sessions: dict[str, float] | None = None  # token hash -> when it expires
_failures: dict[str, list[float]] = {}  # client address -> times of its recent wrong passwords


def _sessions_file() -> Path:
    return get_config_path(for_saving=True).parent / "sessions.json"


def _live_sessions() -> dict[str, float]:
    global _sessions
    if _sessions is None:
        try:
            _sessions = json.loads(_sessions_file().read_text())
        except (OSError, ValueError):
            _sessions = {}
    now = time.time()
    for key in [k for k, expires in _sessions.items() if expires < now]:
        del _sessions[key]
    return _sessions


def _save_sessions() -> None:
    try:
        _sessions_file().parent.mkdir(parents=True, exist_ok=True)
        write_atomic(_sessions_file(), json.dumps(_live_sessions()))
    except OSError as e:  # the session still works until the next restart
        logger.warning(f"Could not save the login sessions: {e}")


def new_session() -> str:
    """Log a browser in: a new session token for its cookie."""
    token = secrets.token_urlsafe(32)
    _live_sessions()[token_hash(token)] = time.time() + SESSION_SECONDS
    _save_sessions()
    return token


def end_session(token: str | None) -> None:
    if token and _live_sessions().pop(token_hash(token), None):
        _save_sessions()


def end_all_sessions() -> None:
    """A new password logs every browser out, so a stolen session dies with the old password."""
    _live_sessions().clear()
    _save_sessions()


def session_valid(token: str | None) -> bool:
    return bool(token) and token_hash(token) in _live_sessions()


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", path="/")


def _throttled_as(client: str) -> str:
    """Who the wrong tries count against: an IPv4 address, or an IPv6 address's /64 network, since a
    device picks itself new IPv6 addresses inside its /64 at will (privacy addresses)."""
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return client
    if isinstance(address, ipaddress.IPv4Address):
        return str(address)
    if address.ipv4_mapped:  # ::ffff:192.0.2.7 is the IPv4 address
        return str(address.ipv4_mapped)
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


def check_throttle(client: str) -> None:
    """429 when this address got the password (or the recovery code) wrong too often lately."""
    client = _throttled_as(client)
    now = time.time()
    recent = _failures[client] = [failed_at for failed_at in _failures.get(client, []) if now - failed_at < FAILURE_WINDOW]
    if len(recent) >= MAX_FAILURES:
        wait = int(FAILURE_WINDOW - (now - recent[0])) + 1
        raise HTTPException(status_code=429, detail=f"Too many wrong tries. Try again in {wait} seconds.")


def note_failure(client: str) -> None:
    # ponytail: in memory, so a restart starts the count over; enough against guessing on a home network
    _failures.setdefault(_throttled_as(client), []).append(time.time())


async def check_password(password: str, client: str) -> bool:
    """The admin password, with the throttle: raises 429 while this address is locked out.
    The hashing (PBKDF2) runs in a thread, off the event loop."""
    check_throttle(client)
    auth = load_config().get("admin_auth") or {}
    ok = bool(auth.get("hash") and auth.get("salt")) and secrets.compare_digest(
        await asyncio.to_thread(hash_password, password, auth["salt"]), auth["hash"])
    if not ok:
        note_failure(client)
    return ok


def _same_site(request: Request) -> bool:
    """A browser sends Origin on a cross-site POST; SameSite=Strict already keeps the cookie off it,
    this is the second lock."""
    origin = request.headers.get("origin")
    return not origin or urllib.parse.urlsplit(origin).netloc == request.headers.get("host")


async def require_api_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency for every admin route: a login session (the cookie), or the password in X-API-Key."""
    config = load_config()
    auth = config.get("admin_auth")

    if not auth:
        raise HTTPException(status_code=403, detail="Admin password not set. Please complete setup.")
    if not auth.get("hash") or not auth.get("salt"):
        raise HTTPException(
            status_code=403,
            detail="Admin auth config is corrupt or incomplete. Please reset the password."
        )

    if session_valid(request.cookies.get(SESSION_COOKIE)):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_site(request):
            raise HTTPException(status_code=403, detail="Refused: the request came from another site")
        return
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Not logged in")
    if not await check_password(x_api_key, request.client.host if request.client else ""):
        raise HTTPException(status_code=401, detail="Invalid password")


def token_hash(token: str) -> str:
    # A plain sha256 is enough: the token is 256 random bits, nothing to guess from a list.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def require_api_token(bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))]) -> None:
    """FastAPI dependency for /api/v1: `Authorization: Bearer <token>` with the token from the
    API card, or the Matter node's own (features/matter), so a new API token doesn't cut Matter off. The admin
    password is not accepted here. HTTPBearer puts the Authorize button in /api/v1/docs."""
    config = load_config()
    stored = [h for h in (config.get("api_token", {}).get("hash"), config.get("matter", {}).get("token_hash")) if h]
    if not bearer or not any(secrets.compare_digest(token_hash(bearer.credentials.strip()), h) for h in stored):
        raise HTTPException(status_code=401, detail="Missing or wrong API token. Create one under Settings > API.",
                            headers={"WWW-Authenticate": "Bearer"})


# Changes on every process start, so clients can tell for certain that a restart finished.
BOOT_ID = uuid.uuid4().hex

# The OS's own calls to the core (the nightly update, the Wi-Fi and cable watches) send this, read as
# root from the core's data dir. A page on the screen (a module's card runs JS there, on this server's
# own address) or a module can't read it (ARCHITECTURE #41). New at every start.
LOCAL_TOKEN_FILE = get_base_dir() / "local-token"  # /storage/mirrordash/data/local-token on a mirror
_local_token = ""


def write_local_token() -> None:
    global _local_token
    token = secrets.token_hex(32)
    try:
        LOCAL_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(LOCAL_TOKEN_FILE, token + "\n", mode=0o600)
        _local_token = token
    except OSError as e:
        logger.error(f"Could not write {LOCAL_TOKEN_FILE}: the nightly update and the Wi-Fi watch can't reach the core: {e}")


def from_the_mirrors_os(request: Request) -> bool:
    """Whether a request is the mirror's OS calling: from loopback and with the local token."""
    token = request.headers.get("x-mirrordash-local", "")
    return bool(_local_token) and bool(request.client) and request.client.host in ("127.0.0.1", "::1") \
        and secrets.compare_digest(token.encode(), _local_token.encode())


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


def job_response(job_id: str | None, title: str, message: str, success_msg: str, target_panel: str = "",
                 max_seconds: int = 900) -> HTMLResponse:
    """Show the progress overlay and follow the job until the app has restarted (the page gives up
    after max_seconds)."""
    if job_id is None:
        return notify("Another install or update is still running. Please wait for it to finish.", "error")
    return ui_events(**{"md-follow": {"jobId": job_id, "bootId": BOOT_ID, "title": title, "message": message,
                                      "successMsg": success_msg, "targetPanel": target_panel,
                                      "maxSeconds": max_seconds}})


@router.get("/jobs/current", dependencies=[Depends(require_api_key)])
async def get_current_job() -> dict:
    return job_status()


@router.get("")
async def get_admin(request: Request):
    # The page shell is public; every panel (dashboard included) is loaded after login via
    # the authenticated /admin/panels/* routes, so nothing system-specific is rendered here.
    boot_status = os.environ.get("MIRRORDASH_BOOT_STATUS", "normal")
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        # The version in the CSS/JS links: after an update the phone loads the new files, not its cached ones
        context={"boot_status": boot_status, "version": get_core_version()}
    )
