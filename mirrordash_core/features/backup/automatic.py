# SPDX-License-Identifier: AGPL-3.0-only
"""The weekly automatic backup: the way back when the storage partition has to be rebuilt
(mirrordash-hydrate.sh rescues the newest backups first, ARCHITECTURE #58).

Made from the nightly run (updates/routes.py, before the update), at most once a week and only when the
content changed since the last one. It holds the passwords and the Wi-Fi unencrypted, as an encrypted
backup does: the mirror knows no password to encrypt it with, and they lie unencrypted beside it on the
card anyway. So it never leaves the mirror (routes.py refuses to download it). The newest KEEP are kept;
hand-made backups are never touched.
"""
import contextlib
import logging
import os
import shutil
import time

from mirrordash_core.features.backup import service
from mirrordash_core.features.backup.archive import read_manifest

logger = logging.getLogger("mirrordash.core.backup")

KEEP = 2
# A week, minus an hour: the nightly run starts at the same time each night, and last week's backup
# finished a little after its start
INTERVAL = 7 * 24 * 3600 - 3600
# Touched (in the backups folder) when a week passed without changes: checked again in a week, not every
# night (a check writes the content out to compare it). Not the backup's own time, which the Backup list shows.
CHECKED = ".automatic-checked"


def automatic_backups() -> list[str]:
    """The automatic backups' paths, newest first (the name holds the time)."""
    folder = service.BACKUPS_DIR
    names = os.listdir(folder) if os.path.isdir(folder) else []
    return [os.path.join(folder, name) for name in
            sorted((n for n in names if n.startswith(service.AUTOMATIC_PREFIX) and n.endswith(".mirror")), reverse=True)]


def remove_stale_temporaries() -> None:
    """What a backup or a restore left in the backups folder when the power went or the app was killed
    mid-way (its temporary folder, a decrypted zip, the copy waiting to be restored): copies of the
    settings and passwords that nothing would ever remove. Only those a day old: one may be in use now."""
    folder = service.BACKUPS_DIR
    for entry in os.scandir(folder) if os.path.isdir(folder) else []:
        if entry.name.startswith("tmp") and time.time() - entry.stat(follow_symlinks=False).st_mtime > 24 * 3600:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                with contextlib.suppress(OSError):  # gone in the meantime
                    os.remove(entry.path)


async def automatic_backup() -> str:
    """Make this week's automatic backup when it's due and something changed. Returns what it did."""
    from mirrordash_core.admin import job_status
    if job_status()["state"] == "running":  # an update or a restore changing what would be backed up
        return "busy"
    remove_stale_temporaries()
    backups = automatic_backups()
    checked = os.path.join(service.BACKUPS_DIR, CHECKED)
    last = max([os.path.getmtime(path) for path in [*backups[:1], checked] if os.path.exists(path)], default=0)
    # abs: a clock that went back more than a week (no clock battery, a wrong time server) mustn't stop them
    if backups and abs(time.time() - last) < INTERVAL:
        return "not due"
    last_hash = None
    if backups:
        try:
            last_hash = (await read_manifest(backups[0]) or {}).get("content_hash")
        except Exception as e:  # a broken last backup: make a new one
            logger.warning(f"Could not read {os.path.basename(backups[0])}: {e}")
    result = await service.create_backup(automatic=True, unless_hash=last_hash)
    if result["status"] == "unchanged":
        with open(checked, "w"):
            pass
        os.utime(checked)
        return "unchanged"
    # Never the one just written, even when a wrong clock gave it an older name than the others
    for old in [p for p in automatic_backups() if os.path.basename(p) != result["filename"]][KEEP - 1:]:
        os.remove(old)
    logger.info(f"Automatic backup: {result['filename']}")
    return result["filename"]
