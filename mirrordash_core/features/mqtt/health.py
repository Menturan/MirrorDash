# SPDX-License-Identifier: AGPL-3.0-only
"""Tells the owner (the bell) when MQTT has been off for a long time: a broker or Home Assistant that is
down, a wrong password. One short disconnect is normal and says nothing; it is the time since the last
good connection that counts, and it is told once per outage."""

import time
from collections.abc import Callable

from mirrordash_core import notifications

TELL_AFTER = 600  # seconds without a connection


class ConnectionHealth:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._down_since: float | None = None
        self._told = False

    def reset(self) -> None:
        """Connected, or MQTT was turned off or restarted: start counting from the next failure."""
        self._down_since = None
        self._told = False

    def failed(self, status: str) -> None:
        """A connection attempt or a connection ended; status is what the admin card shows."""
        now = self._clock()
        if self._down_since is None:
            self._down_since = now
        if not self._told and now - self._down_since >= TELL_AFTER:
            self._told = True
            notifications.add("warning", f"MQTT hasn't been connected for {TELL_AFTER // 60} minutes: {status}", once=True)
