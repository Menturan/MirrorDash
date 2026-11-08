# SPDX-License-Identifier: AGPL-3.0-only
"""Every new saved log becomes a notification (features/logs/saved.py). The saved logs are what the
system's own scripts leave behind, also while the app is down (a rollback, safe mode, Wi-Fi recovery, a
power cut), so the bell hears of them without any script having to reach the app.

Looked at when the app starts and once a minute after that. The first look on a mirror only remembers
the logs already there, so an update to this version doesn't announce old news."""

import asyncio
import logging
from datetime import UTC, datetime

from mirrordash_core import notifications
from mirrordash_core.features.logs import saved
from mirrordash_core.features.notifications import versions

logger = logging.getLogger("mirrordash.core.notifications")

# ponytail: a look once a minute, not inotify; a notification can be a minute late
CHECK_EVERY = 60  # seconds
# Told even on a mirror's first look (mirrordash-hydrate.sh rebuilt the storage partition, notifications included)
TOLD_ON_FIRST_LOOK = {"storage-rebuilt"}


def sync_saved_logs() -> None:
    try:
        paths = {p.name: p for p in saved.SAVED_LOG_DIR.glob("*.log") if p.is_file()}
    except OSError:
        return
    seen = notifications.seen_logs()
    # A storage partition made anew has no notifications yet, so this is a first look; its news is the one
    # that mustn't be taken for old
    new = set(paths) - seen if seen is not None else {n for n in paths if saved.kind_of(n) in TOLD_ON_FIRST_LOOK}
    if new:
        for name in sorted(new):
            kind = saved.kind_of(name)
            try:
                time = datetime.fromtimestamp(paths[name].stat().st_mtime, UTC)
            except OSError:
                continue  # deleted in the meantime
            notifications.add("error" if kind in saved.ERRORS else "warning", saved.KINDS.get(kind, "A log was saved"),
                              log=name, time=time)
    if seen != set(paths):
        notifications.set_seen_logs(set(paths))  # the deleted ones too drop off: names hold the time, never reused


_task: asyncio.Task | None = None


async def _run() -> None:
    while True:
        try:
            sync_saved_logs()
        except Exception as e:  # a look must never stop the watch
            logger.warning(f"Looking for new saved logs failed: {e}")
        await asyncio.sleep(CHECK_EVERY)


async def start() -> None:
    global _task
    try:
        versions.note_new_versions()
    except Exception as e:
        logger.warning(f"Comparing the running versions with the last start's failed: {e}")
    _task = asyncio.create_task(_run())


async def stop() -> None:
    global _task
    if _task:
        _task.cancel()
        _task = None
