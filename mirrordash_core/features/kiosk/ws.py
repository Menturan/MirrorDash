# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import time

from fastapi import WebSocket

from mirrordash_core.config import get_core_version

logger = logging.getLogger("mirrordash.core.kiosk")

SEND_TIMEOUT = 5  # seconds a client gets to take a message


def on_screen(client) -> bool:
    """Whether a client is the mirror's own screen (a loopback address)."""
    return bool(client) and client.host in ("127.0.0.1", "::1")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.latest_messages: dict[str, dict] = {}
        # When the mirror's own screen last showed it's alive (mirrordash-app-watch restarts the kiosk
        # when it stops). A start counts, so the screen has time to load.
        self.screen_seen = time.monotonic()
        # While a module plays a video (its video_is_playing()), the other modules' updates wait for the
        # next whole minute, so the page redraws once a minute instead of under the video (ARCHITECTURE #33)
        self.video_players: set[str] = set()
        self.pending: set[str] = set()
        self._minute_task: asyncio.Task | None = None

    def saw(self, client) -> None:
        """A request from a loopback client: the mirror's own screen, whose page sends "alive" every
        minute (js/kiosk/core.js) or polls /admin/auth/status (the setup prompts)."""
        if on_screen(client):
            self.screen_seen = time.monotonic()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.saw(websocket.client)
        self.active_connections.append(websocket)
        logger.info(f"New client connected. Total clients: {len(self.active_connections)}")

        # The mirror page's code comes with MirrorDash: on another version than the page was loaded
        # with (an update or a rollback), the page reloads itself (js/kiosk/core.js). "online" sets
        # its Offline badge (internet.py; changes come later as {"type": "internet"}), and
        # "video_is_playing" whether the modules update once a minute (later as {"type": "video_is_playing"}).
        from mirrordash_core.admin import BOOT_ID
        from mirrordash_core.features.kiosk.internet import internet_monitor
        try:
            await websocket.send_json({"type": "hello", "version": get_core_version(), "boot_id": BOOT_ID, "online": internet_monitor.online,
                                      "video_is_playing": bool(self.video_players)})
        except Exception as e:
            logger.error(f"Error greeting new client: {e}")

        # Send the latest state of all active modules immediately to prevent blank/delayed load
        for msg in list(self.latest_messages.values()):
            try:
                await websocket.send_json(msg)
            except Exception as e:
                logger.error(f"Error sending cached message to new client: {e}")

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Total clients: {len(self.active_connections)}")

    async def broadcast(self, message: dict) -> None:
        """Broadcast a message to all connected clients.
        Dead connections are removed after iteration to prevent accumulation.
        """
        # Cache module HTML update messages; the same one again is not sent, so the screen doesn't redraw
        # (restart its icons, scripts and scrolling) when nothing changed. A reload clears the cache.
        if "module" in message and "html" in message:
            if self.latest_messages.get(message["module"]) == message:
                return
            self.latest_messages[message["module"]] = message
            if self.video_players and message["module"] not in self.video_players:
                self.pending.add(message["module"])
                return
        await self._send(message)

    async def _send(self, message: dict) -> None:
        # Iterate over a snapshot to avoid mutation during iteration; one stalled client must not delay the rest
        connections = list(self.active_connections)

        async def send(connection: WebSocket) -> None:
            try:
                await asyncio.wait_for(connection.send_json(message), SEND_TIMEOUT)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e!r}. Dropping connection.")
                self.disconnect(connection)
                try:
                    await asyncio.wait_for(connection.close(), 1)
                except Exception:
                    pass

        await asyncio.gather(*(send(c) for c in connections))

    async def set_video_playing(self, instance_id: str, playing: bool) -> None:
        """A module starts or stops playing a video. The page hears when the first starts and the last
        stops; the updates held meanwhile go out on each whole minute, and at once when the last stops."""
        was_playing = bool(self.video_players)
        (self.video_players.add if playing else self.video_players.discard)(instance_id)
        if bool(self.video_players) == was_playing:
            return
        await self._send({"type": "video_is_playing", "playing": not was_playing})
        if was_playing:
            self._minute_task.cancel()
            self._minute_task = None
            await self.send_pending()
        else:
            self._minute_task = asyncio.create_task(self._each_minute())

    async def _each_minute(self) -> None:
        while True:
            await asyncio.sleep(60 - time.time() % 60)  # on the minute, when the page steps its carousels
            await self.send_pending()

    async def send_pending(self) -> None:
        """Send the latest update of each module held while a video plays."""
        pending, self.pending = self.pending, set()
        for module in pending:
            if module in self.latest_messages:
                await self._send(self.latest_messages[module])

    def clear_cache(self) -> None:
        """Clear cached module messages."""
        self.latest_messages.clear()
        self.pending.clear()
        logger.info("Cleared cached module messages.")


# Singleton instance
manager = ConnectionManager()
