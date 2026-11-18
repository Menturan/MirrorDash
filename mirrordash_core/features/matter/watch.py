# SPDX-License-Identifier: AGPL-3.0-only
"""Tells the owner (the bell) when the Matter node stays down (ARCHITECTURE #35).

The node is a systemd unit that systemd restarts by itself after a crash (5 s), so one crash is nothing to
announce. Once a minute, while Matter is on, the unit is looked at; after DOWN_CHECKS looks in a row that
found it down, the bell says so once. Down means: not "active", or restarted since the last look (a crash
loop can look active at the very moment of a look). `systemctl show` only reads, so it needs no sudo."""

import asyncio
import logging
import time
from collections.abc import Callable

from mirrordash_core import notifications
from mirrordash_core.config import load_config
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.matter")

# ponytail: a look once a minute, not a D-Bus subscription; three looks is ~3 minutes, far beyond the restart
CHECK_EVERY = 60  # seconds
DOWN_CHECKS = 3
QUIET_AFTER_START = 60  # seconds: start_matter restarts the unit, which looks down for a moment


async def read_unit() -> tuple[str, int] | None:
    """(ActiveState, NRestarts) of the node's unit, or None when systemctl couldn't say."""
    code, output, _ = await run_command("systemctl", "show", "mirrordash-matter", "-p", "ActiveState", "-p", "NRestarts")
    if code != 0:
        return None
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    try:
        return values["ActiveState"], int(values["NRestarts"])
    except (KeyError, ValueError):
        return None


def matter_enabled() -> bool:
    return bool(load_config().get("matter", {}).get("enabled"))


class MatterWatch:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._restarts: int | None = None  # NRestarts at the last look
        self._down = 0  # looks in a row that found it down
        self._told = False
        self._quiet_until = 0.0

    def hold_off(self) -> None:
        """The node is being restarted on purpose: don't count the next minute."""
        self._quiet_until = self._clock() + QUIET_AFTER_START
        self._reset()

    def _reset(self) -> None:
        self._restarts, self._down, self._told = None, 0, False

    async def check(self) -> None:
        """One look."""
        if not matter_enabled() or self._clock() < self._quiet_until:
            self._reset()
            return
        unit = await read_unit()
        if unit is None:
            return  # no answer says nothing about the node
        state, restarts = unit
        crashed = self._restarts is not None and restarts > self._restarts
        self._restarts = restarts
        if state == "active" and not crashed:
            self._down, self._told = 0, False
            return
        self._down += 1
        if self._down >= DOWN_CHECKS and not self._told:
            self._told = True
            logger.error(f"The Matter node keeps failing (state: {state}, restarts: {restarts})")
            notifications.add("error", "Matter has stopped running and didn't come back; Apple Home and Google Home "
                                       "can't reach the mirror. Turn Matter off and on again, or see Logs.", once=True)

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception as e:  # a look must never stop the watch
                logger.warning(f"Looking at the Matter node failed: {e}")
            await asyncio.sleep(CHECK_EVERY)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="matter-watch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


matter_watch = MatterWatch()
