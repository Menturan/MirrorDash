# Licensed under the PolyForm Noncommercial License 1.0.0.
"""USB on or off (Power → USB): on a Pi 1, 2, 3 and Zero the USB controller wakes the CPU about 8000 times
a second even with nothing plugged in, and a Pi 3's hub and Ethernet chip draw power all the time.

The work is done by /usr/local/bin/mirrordash-usb (scripts/image/rootfs/usr/local/bin/mirrordash-usb):
`status` runs as the app, on and off through sudo; the setting is a file on /storage it applies at boot.
"""

import logging
import os

from fastapi import HTTPException

from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.power")

HELPER = "/usr/local/bin/mirrordash-usb"


async def _run(*command: str) -> str:
    """The command's output; an HTTP error with its reason when it fails."""
    exit_code, output, error_output = await run_command(*command, timeout=30)
    if exit_code != 0:
        message = error_output.strip() or f"exit code {exit_code}"
        logger.error(f"{' '.join(command)} failed: {message}")
        raise HTTPException(status_code=400, detail=message)
    return output.strip()


async def usb_status() -> str | None:
    """"on" or "off", or None where it can't be switched (a Pi 4 or 5, a development machine)."""
    if not os.access(HELPER, os.X_OK):
        return None
    try:
        status = await _run(HELPER, "status")
    except HTTPException:
        return None
    return status if status in ("on", "off") else None


async def set_usb(on: bool) -> None:
    """Turn the USB ports on or off, now and at every boot. HTTPException with the reason when it can't
    (off refuses while the mirror is connected by Ethernet, which goes through USB on a Pi 3)."""
    await _run("sudo", "-n", HELPER, "on" if on else "off")
    logger.info(f"USB turned {'on' if on else 'off'}")
