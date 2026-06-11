# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import asyncio
from fastapi import WebSocket

logger = logging.getLogger("mirrordash.core.ws_manager")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.latest_messages: dict[str, dict] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New client connected. Total clients: {len(self.active_connections)}")

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
        # Cache module HTML update messages
        if "module" in message and "html" in message:
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
