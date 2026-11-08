# SPDX-License-Identifier: AGPL-3.0-only
"""The weekly automatic backup: the way back when the storage partition has to be rebuilt
(mirrordash-hydrate.sh rescues the newest backups first, ARCHITECTURE #58).

Made from the nightly run (updates/routes.py, before the update), at most once a week and only when the
content changed since the last one. It holds the passwords and the Wi-Fi unencrypted, as an encrypted
backup does: the mirror knows no password to encrypt it with, and they lie unencrypted beside it on the
card anyway. So it never leaves the mirror (routes.py refuses to download it). The newest KEEP are kept;
hand-made backups are never touched.
"""
import logging
import os
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


async def automatic_backup() -> str:
    """Make this week's automatic backup when it's due and something changed. Returns what it did."""
    backups = automatic_backups()
    checked = os.path.join(service.BACKUPS_DIR, CHECKED)
    last = max([os.path.getmtime(path) for path in [*backups[:1], checked] if os.path.exists(path)], default=0)
    if backups and time.time() - last < INTERVAL:
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
    for old in automatic_backups()[KEEP:]:
        os.remove(old)
    logger.info(f"Automatic backup: {result['filename']}")
    return result["filename"]
