# SPDX-License-Identifier: AGPL-3.0-only
"""The nightly update for a mirror that is off at night (a smart plug, a switch): the night's timer never
fires there, so with "Catch up after a night off" on, the same round runs a while after the app starts,
when the last one is long enough ago. Kept in the app, not as systemd's Persistent=: the timer's own
memory of its last run lies in the root's RAM overlay, which every start begins without."""

import asyncio
import logging
import time
from pathlib import Path

from mirrordash_core.config import get_config_path, load_config

logger = logging.getLogger("mirrordash.core.updates")

# Long enough for the network, the clock and the modules to be there, and for someone who just turned the
# mirror on to see it work before it may restart
AFTER_START_SECONDS = 10 * 60
# Under a day: a mirror turned on at about the same time every morning must not skip every other day
MISSED_AFTER_SECONDS = 20 * 3600


def _stamp() -> Path:
    return get_config_path().parent / "nightly-update-at"


def note_round() -> None:
    """A round ran now (routes.nightly_round)."""
    try:
        _stamp().write_text(str(int(time.time())))
    except OSError as e:
        logger.warning(f"Could not note the nightly update's time: {e}")


def missed(now: float) -> bool:
    """Whether the last round is long enough ago. A clock that shows a time before it (not set yet, no
    network) says no: better a day late than a restart on a guess."""
    try:
        return now - int(_stamp().read_text()) > MISSED_AFTER_SECONDS
    except (OSError, ValueError):
        return True  # never ran here


async def catch_up() -> None:
    """The app's start calls this. Never on the trial start of a new system: that one is busy proving itself."""
    from mirrordash_core.features.updates.routes import nightly_round
    from mirrordash_core.features.updates.service import TRYBOOT_FLAG, TRYBOOT_ON
    await asyncio.sleep(AFTER_START_SECONDS)
    system = load_config().get("system", {})  # read now: the owner may have changed it since the start
    if not (system.get("auto_update", True) and system.get("auto_update_catch_up", False)) or not missed(time.time()):
        return
    try:
        if TRYBOOT_FLAG.read_bytes() == TRYBOOT_ON:
            return
    except OSError:
        pass
    logger.info("The mirror missed the nightly update: catching up")
    await nightly_round()
