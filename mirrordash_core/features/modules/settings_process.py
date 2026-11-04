# SPDX-License-Identifier: AGPL-3.0-only
"""A module's settings process (ARCHITECTURE #48): a short-lived process of a module none of whose instances
runs, for the settings drawer's preview and the choices of its list and search settings. Like an instance's
own process it runs in the sandbox as a user of its own (one per module, "settings-<module>", kept in the
host's uids.json like an instance's), but it is never started with settings: it only answers the drawer,
and stops SETTINGS_IDLE seconds after it was last asked."""

import asyncio

from mirrordash_core.host import run_in_background

SETTINGS_IDLE = 120  # seconds an unused settings process stays: the owner is still filling in the form


def settings_instance_name(module: str) -> str:
    """The instance name a module's settings process runs as: its user, data and cache folders.
    ponytail: an instance an owner names "settings-<module>" would share them."""
    return f"settings-{module}"[:64]


class SettingsProcesses:
    """The running settings processes, one per module. open_connection(name) starts a process (the
    module host's _open_instance), read(conn) serves its messages until it ends."""

    def __init__(self, open_connection, read):
        self._open, self._read = open_connection, read
        self._conns: dict = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}

    async def get(self, module: str):
        """The module's settings process, started if none runs; it stays SETTINGS_IDLE seconds from now."""
        conn = self._conns.get(module)
        if conn is None:
            conn = self._conns[module] = await self._open(settings_instance_name(module))
            conn.module = module
            run_in_background(self._serve(module, conn), name=f"settings-{module}")
        if timer := self._timers.pop(module, None):
            timer.cancel()
        self._timers[module] = asyncio.get_running_loop().call_later(SETTINGS_IDLE, conn.close)
        return conn

    async def _serve(self, module: str, conn) -> None:
        await self._read(conn)
        conn.close()
        if self._conns.get(module) is conn:
            del self._conns[module]
            if timer := self._timers.pop(module, None):
                timer.cancel()

    def close_all(self) -> None:
        """Stop them all: the module host went, or the modules were reinstalled (new code)."""
        for conn in list(self._conns.values()):
            conn.close()
