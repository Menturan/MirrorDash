# SPDX-License-Identifier: AGPL-3.0-only
"""The last update check's answer, kept in memory and shared by every admin page and browser: the
Dashboard's banner and Settings → Updates show it without asking PyPI and GitHub again. It is used for 30
minutes; Check for Updates (force) and a change of the Test versions setting make a new check. An
install needs nothing: the app restarts and memory starts empty."""

import asyncio
import time

from mirrordash_core.features.modules.service import module_updates
from mirrordash_core.features.updates.service import check_core_update, check_os_update

KNOWN_MAX_AGE = 1800.0
_known: dict | None = None
_made_at = 0.0  # time.monotonic() when _known was made
_lock = asyncio.Lock()


async def known_updates(force: bool = False) -> dict:
    """{"core", "os", "modules", "checked_at"}: each part is its check's answer or the exception it raised."""
    global _known, _made_at
    if _fresh() and not force:
        return _known
    async with _lock:
        if _fresh() and not force:  # another request made it while this one waited
            return _known
        # ponytail: a forced check still takes the modules' answers up to a minute old, so tapping again and
        # again can't use up GitHub's 60 requests an hour (one per module installed from GitHub)
        core, os_update, modules = await asyncio.gather(
            check_core_update(), check_os_update(), module_updates(max_age=60) if force else module_updates(),
            return_exceptions=True)
        answer = {"core": core, "os": os_update, "modules": modules, "checked_at": time.time()}
        # A failed part (offline at boot, GitHub down) is not kept: the next read asks again
        failed = any(isinstance(part, BaseException) for part in (core, os_update, modules))
        _known, _made_at = (None, 0.0) if failed else (answer, time.monotonic())
        return answer


def _fresh() -> bool:
    return _known is not None and time.monotonic() - _made_at < KNOWN_MAX_AGE


def forget(_event=None) -> None:  # also an event subscriber
    global _known
    _known = None
