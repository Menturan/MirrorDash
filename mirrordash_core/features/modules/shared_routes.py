# SPDX-License-Identifier: AGPL-3.0-only
"""The shared settings are saved with the instance (routes.py: shared[...] in the Save POST, ARCHITECTURE #62) and
drawn in the sheet (shared_settings.shared_block). What is left here is the old section's address, which
admin.js still asks for until the sheet UI stops: delete this file and its import in app.py then."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import require_api_key

router = APIRouter(prefix="/admin")


@router.get("/panels/modules/shared/{module_name}", dependencies=[Depends(require_api_key)])
async def get_shared_settings(module_name: str):
    return HTMLResponse("")
