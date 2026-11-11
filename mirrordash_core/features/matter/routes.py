# SPDX-License-Identifier: AGPL-3.0-only
"""Matter (ARCHITECTURE #35): the screen as an on/off light in Apple Home, Google Home and the rest.
The node is a Node.js sidecar (matter/index.mjs, mirrordash-matter.service) that runs the screen
through /api/v1. The core starts it (node.py): /etc is a RAM overlay, so `systemctl enable` wouldn't last."""

from fastapi import APIRouter, Depends, Request

from mirrordash_core.admin import events_header, require_api_key, templates
from mirrordash_core.features.matter import node

router = APIRouter(prefix="/admin")


def _matter_card(request: Request, message: str = "", kind: str = "success"):
    enabled, pairing = node.pairing()
    return templates.TemplateResponse(
        request=request, name="admin_matter.html", context={"enabled": enabled, "pairing": pairing},
        headers=events_header(**{"md-notify": {"message": message, "kind": kind}}) if message else None,
    )


@router.get("/panels/system/matter", dependencies=[Depends(require_api_key)])
async def get_matter_card(request: Request):
    return _matter_card(request)


@router.post("/panels/system/matter/on", dependencies=[Depends(require_api_key)])
async def matter_on(request: Request):
    (node.MATTER_DIR / "pairing.json").unlink(missing_ok=True)  # the node writes it anew once it is up
    if await node.start_matter():
        return _matter_card(request, "Matter is on.")
    return _matter_card(request, "Matter didn't start. The Logs tab has the details.", "error")


@router.post("/panels/system/matter/off", dependencies=[Depends(require_api_key)])
async def matter_off(request: Request):
    await node.stop_matter()
    return _matter_card(request, "Matter is off.")


@router.post("/panels/system/matter/reset", dependencies=[Depends(require_api_key)])
async def matter_reset(request: Request):
    """Forget every home the mirror is paired with, and offer it for pairing again."""
    await node.forget_homes()
    if await node.start_matter():
        return _matter_card(request, "Pairing reset. Add the mirror again in your home app.")
    return _matter_card(request, "Matter didn't start. The Logs tab has the details.", "error")
