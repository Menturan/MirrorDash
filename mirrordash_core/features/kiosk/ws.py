# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import time
from fastapi import WebSocket
from mirrordash_core.config import get_core_version

logger = logging.getLogger("mirrordash.core.kiosk")


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.latest_messages: dict[str, dict] = {}
        # When the mirror's own screen last showed it's alive (mirrordash-app-watch restarts the kiosk
        # when it stops). A start counts, so the screen has time to load.
        self.screen_seen = time.monotonic()

    def saw(self, client) -> None:
        """A request from a loopback client: the mirror's own screen, whose page sends "alive" every
        minute (js/kiosk/core.js) or polls /admin/auth/status (the setup prompts)."""
        if client and client.host in ("127.0.0.1", "::1"):
            self.screen_seen = time.monotonic()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.saw(websocket.client)
        self.active_connections.append(websocket)
        logger.info(f"New client connected. Total clients: {len(self.active_connections)}")

        # The mirror page's code comes with MirrorDash: on another version than the page was loaded
        # with (an update or a rollback), the page reloads itself (js/kiosk/core.js). "online" sets
        # its Offline badge (internet.py; changes come later as {"type": "internet"}).
        from mirrordash_core.features.kiosk.internet import internet_monitor
        try:
            await websocket.send_json({"type": "hello", "version": get_core_version(), "online": internet_monitor.online})
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

        dead: list[WebSocket] = []
        # Iterate over a snapshot to avoid mutation during iteration
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error broadcasting to client: {e}. Marking connection as dead.")
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)

    def clear_cache(self) -> None:
        """Clear cached module messages."""
        self.latest_messages.clear()
        logger.info("Cleared cached module messages.")


# Singleton instance
manager = ConnectionManager()
