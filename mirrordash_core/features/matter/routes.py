# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Matter (ARCHITECTURE #35): the screen as an on/off light in Apple Home, Google Home and the rest.
The node is a Node.js sidecar (matter/index.mjs, mirrordash-matter.service) that runs the screen
through /api/v1. The core starts it: /etc is a RAM overlay, so `systemctl enable` wouldn't last."""

import asyncio
import json
import logging
import secrets
import shutil

from fastapi import APIRouter, Depends, Request

from mirrordash_core.admin import events_header, require_api_key, templates, token_hash
from mirrordash_core.config import get_base_dir, load_config, save_config, write_atomic

logger = logging.getLogger("mirrordash.core.matter")
router = APIRouter(prefix="/admin")

# Next to data/, not in it: a backup carries neither the node's keys nor its token
MATTER_DIR = get_base_dir() / "matter"  # the node's own storage (fabrics, keys) and pairing.json
ENV_FILE = get_base_dir() / "matter.env"
# The spec rules these out (Matter Core 5.1.7.1)
FORBIDDEN_PASSCODES = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777,
                       88888888, 99999999, 12345678, 87654321}


async def _systemctl(action: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec("sudo", "-n", "systemctl", action, "mirrordash-matter",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, err = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        logger.error(f"systemctl {action} mirrordash-matter failed: {e}")
        return False
    if proc.returncode:
        logger.error(f"systemctl {action} mirrordash-matter failed: {err.decode(errors='replace').strip()}")
    return proc.returncode == 0


async def start_matter() -> bool:
    """Start the node, with a new token every time: only its hash is kept, so nothing has to survive
    a restart or a restore. The pairing codes are made once per mirror."""
    token = secrets.token_urlsafe(32)
    config = load_config()
    matter = config.setdefault("matter", {})
    if "passcode" not in matter:
        while (passcode := secrets.randbelow(99999998) + 1) in FORBIDDEN_PASSCODES:
            pass
        matter.update(passcode=passcode, discriminator=secrets.randbelow(4096))
    matter.update(enabled=True, token_hash=token_hash(token))
    save_config(config)
    MATTER_DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(ENV_FILE, f"MIRRORDASH_TOKEN={token}\nMATTER_PASSCODE={matter['passcode']}\n"
                           f"MATTER_DISCRIMINATOR={matter['discriminator']}\nMATTER_STORAGE_PATH={MATTER_DIR}\n", 0o600)
    return await _systemctl("restart")  # restart: a running node picks up the new token


async def stop_matter() -> None:
    await _systemctl("stop")
    config = load_config()
    matter = config.setdefault("matter", {})
    matter["enabled"] = False
    matter.pop("token_hash", None)
    save_config(config)


def _matter_card(request: Request, message: str = "", kind: str = "success"):
    enabled = load_config().get("matter", {}).get("enabled", False)
    try:
        pairing = json.loads((MATTER_DIR / "pairing.json").read_text(encoding="utf-8")) if enabled else {}
    except (OSError, ValueError):
        pairing = {}  # the node hasn't written it yet: the card asks again
    return templates.TemplateResponse(
        request=request, name="admin_matter.html", context={"enabled": enabled, "pairing": pairing},
        headers=events_header(**{"md-notify": {"message": message, "kind": kind}}) if message else None,
    )


@router.get("/panels/system/matter", dependencies=[Depends(require_api_key)])
async def get_matter_card(request: Request):
    return _matter_card(request)


@router.post("/panels/system/matter/on", dependencies=[Depends(require_api_key)])
async def matter_on(request: Request):
    (MATTER_DIR / "pairing.json").unlink(missing_ok=True)  # the node writes it anew once it is up
    if await start_matter():
        return _matter_card(request, "Matter is on.")
    return _matter_card(request, "Matter didn't start. The Logs tab has the details.", "error")


@router.post("/panels/system/matter/off", dependencies=[Depends(require_api_key)])
async def matter_off(request: Request):
    await stop_matter()
    return _matter_card(request, "Matter is off.")


@router.post("/panels/system/matter/reset", dependencies=[Depends(require_api_key)])
async def matter_reset(request: Request):
    """Forget every home the mirror is paired with, and offer it for pairing again."""
    await _systemctl("stop")
    shutil.rmtree(MATTER_DIR, ignore_errors=True)
    if await start_matter():
        return _matter_card(request, "Pairing reset. Add the mirror again in your home app.")
    return _matter_card(request, "Matter didn't start. The Logs tab has the details.", "error")
