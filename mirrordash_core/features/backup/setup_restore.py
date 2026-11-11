# SPDX-License-Identifier: AGPL-3.0-only
"""A new mirror set up from a backup, on the Wi-Fi setup page (features/wifi): the backup brings the network."""

import asyncio
import logging
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from mirrordash_core.admin import start_job
from mirrordash_core.features.backup.archive import backup_wifi, read_manifest
from mirrordash_core.features.backup.service import restore_backup, temp_upload
from mirrordash_core.features.wifi.network import connect_wifi, restore_captive_ap
from mirrordash_core.features.wifi.routes import check_wifi_auth
from mirrordash_core.host import reboot_system, run_in_background

logger = logging.getLogger("mirrordash.core.backup")
router = APIRouter()

# Big enough for a backup with local modules and their data; the phone sends it over the hotspot
RESTORE_MAX_BYTES = 500 * 1024 * 1024


@router.post("/api/wifi/restore", dependencies=[Depends(check_wifi_auth)])
async def post_wifi_restore(file: UploadFile = File(...), password: str = Form(default="")) -> dict:
    """Set a new mirror up from an encrypted backup: join a Wi-Fi network it carries, then restore it
    (modules come from GitHub, so the network comes first). Open like the rest of the setup page only
    while the mirror has no admin password: before that anyone near it could set it up anyway."""
    size = 0
    with open(temp_upload(), "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > RESTORE_MAX_BYTES:
                out.close()
                os.remove(temp_upload())  # never leave half a file of that size on the card
                raise HTTPException(status_code=413, detail="That file is too big to be a backup.")
            out.write(chunk)
    try:
        manifest = await read_manifest(temp_upload(), password or None)
    except Exception:
        raise HTTPException(status_code=400, detail="That file isn't a MirrorDash backup, or it's from an older version.")
    if manifest is None:
        raise HTTPException(status_code=400, detail="Wrong password for this backup." if password
                            else "This backup is protected: enter its password.")
    networks = await backup_wifi(temp_upload(), password or None)
    if not networks:
        raise HTTPException(status_code=400, detail="This backup has no Wi-Fi networks (only a backup with a password "
                            "has them). Connect the mirror to Wi-Fi first, then restore it on the admin page.")

    async def join_then_restore():
        await asyncio.sleep(1)  # let the answer reach the phone before the hotspot goes
        for network in networks:
            if (await connect_wifi(network["ssid"], network.get("psk") or None))[0]:
                start_job(lambda: restore_backup(password=password or None))
                return
        logger.warning("Could not join any Wi-Fi network from the backup; bringing the setup hotspot back.")
        if not await restore_captive_ap():
            await reboot_system(delay_sec=3.0)
    run_in_background(join_then_restore())
    return {"status": "success", "networks": [network["ssid"] for network in networks]}
