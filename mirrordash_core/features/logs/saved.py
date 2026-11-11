# SPDX-License-Identifier: AGPL-3.0-only
"""Logs saved when an update fails.

The system log lives in RAM on a mirror (nothing is written to the SD card all day, so it doesn't wear
out), which means it is gone after a restart. When an update fails, the part that explains why is
saved to /storage/mirrordash/logs/ by save-log (scripts/image/rootfs/usr/local/lib/mirrordash/save-log), at most 5 files. Here the
admin page lists, downloads and deletes them.
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger("mirrordash.core.logs")

SAVED_LOG_DIR = Path("/storage/mirrordash/logs")
SAVE_LOG = Path("/usr/local/lib/mirrordash/save-log")
# File names start with what happened (save-log's KIND)
KINDS = {
    "os-trial": "OS update: the new version didn't start properly",
    "os-install": "OS update: installing failed",
    "app-rollback": "App update: the new version crashed and was rolled back",
    "app-safe-mode": "MirrorDash couldn't start and started in safe mode",
    "wifi-lost": "Wi-Fi: no traffic got through; joined again, or started the Wi-Fi chip again",
    "wifi-back": "Wi-Fi: the Wi-Fi chip had stopped answering and works again",
    "wifi-restart": "Wi-Fi: the Wi-Fi chip didn't work, so the mirror was restarted",
    "wifi-chip": "Wi-Fi: the Wi-Fi chip didn't start; started it again",
    "p1-update": "System update: renewing the Pi 3 firmware failed (the update itself stayed)",
    "storage-repair": "The storage partition had errors and was repaired at start",
    "storage-rebuilt": "The storage partition was damaged beyond repair and was made anew. Restore the newest "
                       "backup under Backup (if a password-protected one's password is forgotten, the automatic "
                       "one). If this happens again, replace the SD card.",
    "app-hung": "MirrorDash stopped answering and was restarted",
    "screen-lost": "The mirror's screen stopped and was restarted",
    "power-lost": "The mirror shut down without warning (power cut or freeze) and started again",
}
# How each kind is told on the bell (features/notifications/watch.py): an error needs the owner, a warning
# fixed itself
ERRORS = {"app-safe-mode", "os-install", "p1-update", "storage-rebuilt"}


def kind_of(filename: str) -> str | None:
    """The KINDS key a saved log's file name starts with."""
    return next((kind for kind in KINDS if filename.startswith(kind + "-")), None)


def saved_logs() -> list[dict]:
    """The saved logs, newest first."""
    try:
        files = [p for p in SAVED_LOG_DIR.glob("*.log") if p.is_file()]
    except OSError:
        return []
    logs = []
    for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        kind = KINDS.get(kind_of(path.name), "Saved log")
        stat = path.stat()
        logs.append({"filename": path.name, "kind": kind,
                     "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                     "size": f"{stat.st_size / 1024:.0f} KB"})
    return logs


def saved_log_file(filename: str) -> Path:
    """The saved log called filename. Only names that are in the folder are accepted, so a name like
    ../../something can't reach any other file."""
    if filename not in {log["filename"] for log in saved_logs()}:
        raise HTTPException(status_code=404, detail="No such saved log")
    return SAVED_LOG_DIR / filename


def delete_saved_logs(filename: str | None = None) -> None:
    """Delete one saved log, or all of them."""
    for name in [filename] if filename else [log["filename"] for log in saved_logs()]:
        saved_log_file(name).unlink(missing_ok=True)


async def save_log(kind: str, *journal_args: str) -> None:
    """Save part of the system log (journalctl options, e.g. "-u", "rauc") as a saved log of this kind.
    Does nothing where save-log isn't installed (a development machine)."""
    if not os.access(SAVE_LOG, os.X_OK):
        return
    try:
        proc = await asyncio.create_subprocess_exec(str(SAVE_LOG), kind, *journal_args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        logger.warning(f"Could not save the {kind} log: {e}")
