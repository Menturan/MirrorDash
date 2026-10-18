# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Logs saved when an update fails.

The system log lives in RAM on a mirror (nothing is written to the SD card all day, so it doesn't wear
out), which means it is gone after a restart. When an update fails, the part that explains why is
saved to /storage/mirrordash/logs/ by save-log (scripts/configure_image.sh), at most 5 files. Here the
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
}


def saved_logs() -> list[dict]:
    """The saved logs, newest first."""
    try:
        files = [p for p in SAVED_LOG_DIR.glob("*.log") if p.is_file()]
    except OSError:
        return []
    logs = []
    for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
        kind = next((label for prefix, label in KINDS.items() if path.name.startswith(prefix + "-")), "Saved log")
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
