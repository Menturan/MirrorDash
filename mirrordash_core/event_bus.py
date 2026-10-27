# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from mirrordash_core.host import run_in_background

logger = logging.getLogger("mirrordash.core.event_bus")

class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None  # the app's, for a publish from a thread
        # In the core: every event also goes to the module host (client.py), where the modules subscribe
        self.forward: Callable[[str, Any], None] | None = None
        # In a module instance's process: a public event goes to the core, which gives it to every instance
        self.share: Callable[[str, Any], None] | None = None

    def subscribe(self, event_type: str, callback: Callable[[Any], Any]) -> None:
        """Subscribe to a specific type of event."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed callback to event: {event_type}")

    def unsubscribe(self, event_type: str, callback: Callable[[Any], Any]) -> None:
        """Unsubscribe from an event."""
        if event_type in self._subscribers and callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            logger.debug(f"Unsubscribed callback from event: {event_type}")

    def clear(self) -> None:
        """Drop every subscription (all module instances are being replaced)."""
        self._subscribers.clear()

    def publish(self, event_type: str, data: Any = None, public: bool = False) -> None:
        """Publish an event to all subscribers.
        If a subscriber callback is an async coroutine function, it is scheduled on
        the app's event loop (also from a module's own thread). If it's a sync function, it is called
        directly, in the publisher's thread.
        public, in a module instance: every instance gets it, the sender's own too, through the core (client.py),
        which checks it first; otherwise it stays in the instance.
        """
        if public and self.share:
            self.share(event_type, data)
            return
        if self.forward:
            self.forward(event_type, data)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        subscribers = self._subscribers.get(event_type, [])
        if not subscribers:
            return

        logger.debug(f"Publishing event '{event_type}' with data: {data}")
        # Take a copy of the list to prevent modification issues during iteration
        for callback in list(subscribers):
            if inspect.iscoroutinefunction(callback):
                if running:
                    run_in_background(self._safe_await(callback, data, event_type))
                elif self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self._safe_await(callback, data, event_type), self._loop)
                else:
                    logger.warning(f"No running event loop to schedule async callback for event '{event_type}'")
            else:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in sync callback for event '{event_type}': {e}", exc_info=True)

    async def _safe_await(self, callback: Callable, data: Any, event_type: str) -> None:
        try:
            await callback(data)
        except Exception as e:
            logger.error(f"Error in async callback for event '{event_type}': {e}", exc_info=True)

# Global singleton instance
event_bus = EventBus()
