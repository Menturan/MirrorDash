# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Developer logging (Settings → Developer): the whole system log kept on /storage for 7 days.

The work is done by /usr/local/bin/mirrordash-dev-logging (scripts/image/rootfs/usr/local/bin/mirrordash-dev-logging),
run through sudo. Whether it's on is a file on /storage holding when it turns off, so the script can
read it at boot before the app runs; this module only reads that file and calls the script.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.settings")

STORAGE = Path("/storage/mirrordash")
FLAG = STORAGE / "dev-logging-until"
JOURNAL_DIR = STORAGE / "journal"
HELPER = "/usr/local/bin/mirrordash-dev-logging"


def dev_logging_available() -> bool:
    """Only on a MirrorDash image (the script and /storage are there), not on a development machine."""
    return os.access(HELPER, os.X_OK)


def dev_logging_until() -> datetime | None:
    """When developer logging turns itself off, or None when it's off."""
    try:
        until = datetime.fromtimestamp(int(FLAG.read_text().strip()))
    except (OSError, ValueError):
        return None
    return until if until > datetime.now() else None


def saved_journal_mb() -> int:
    """How much system log is saved on /storage, in whole MB (rounded up, so a little shows as 1)."""
    total = 0
    for root, _, files in os.walk(JOURNAL_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return -(-total // 1048576)


async def _run_helper(action: str) -> None:
    """`sudo mirrordash-dev-logging <action>`; an HTTP error with the helper's reason when it fails."""
    exit_code, _, error_output = await run_command("sudo", "-n", HELPER, action, timeout=60)
    if exit_code != 0:
        message = error_output.strip() or f"exit code {exit_code}"
        logger.error(f"mirrordash-dev-logging {action} failed: {message}")
        raise HTTPException(status_code=500, detail=f"Developer logging: {message}")


async def set_dev_logging(on: bool) -> None:
    """Turn it on (for 7 days from now, also when it was on already) or off."""
    if not dev_logging_available():
        raise HTTPException(status_code=400, detail="Developer logging is only available on a MirrorDash image.")
    await _run_helper("on" if on else "off")
    logger.info(f"Developer logging turned {'on' if on else 'off'}")


async def clear_saved_journal() -> None:
    """Delete the saved system log (the script refuses while developer logging is on)."""
    await _run_helper("clear")
