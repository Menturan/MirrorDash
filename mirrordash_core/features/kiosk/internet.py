# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Is the mirror on the internet? Shown on the mirror as the Offline badge (index.html, js/kiosk/core.js).

The browser can't tell: navigator.onLine in Cog (WebKit) said offline after a start while the mirror
was online, and online while the Wi-Fi was joined but nothing got through. So the app checks itself
once a minute: a TCP connection to 1.1.1.1:443, or 8.8.8.8:443 when that fails (only the connection,
no data). Offline after 2 failed checks in a row, so a router restarting or a short dropout doesn't
make the badge blink; online again at the first check that works. Every change goes to the screens
over the websocket, and a screen that connects hears the state in its "hello" (ws.py).
"""

import asyncio
import logging

logger = logging.getLogger("mirrordash.core.internet")

CHECK_EVERY = 60  # seconds
TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443))
FAILS_FOR_OFFLINE = 2


async def reachable() -> bool:
    """Can the mirror open a connection to the internet right now?"""
    for host, port in TARGETS:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5)
        except (TimeoutError, OSError):
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return True
    return False


class InternetMonitor:
    def __init__(self) -> None:
        self.online = True  # until shown otherwise: no badge while the mirror is starting
        self._fails = 0
        self._task: asyncio.Task | None = None

    async def check(self) -> None:
        """One check; tells the screens when the state changes."""
        if await reachable():
            self._fails = 0
            online = True
        else:
            self._fails += 1
            online = self.online if self._fails < FAILS_FOR_OFFLINE else False
        if online != self.online:
            self.online = online
            logger.info("The mirror is online again." if online else "The mirror can't reach the internet.")
            from mirrordash_core.features.kiosk.ws import (
                manager,  # ws.py reads self.online for its hello
            )
            await manager.broadcast({"type": "internet", "online": online})

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
            except Exception as e:  # a check must never stop the monitor
                logger.warning(f"Internet check failed: {e}")
            await asyncio.sleep(CHECK_EVERY)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


internet_monitor = InternetMonitor()
