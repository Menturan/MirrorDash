# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import inspect
from typing import Callable, Any

logger = logging.getLogger("mirrordash.core.event_bus")

class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable[[Any], Any]) -> None:
        """Subscribe to a specific type of event."""
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

    def publish(self, event_type: str, data: Any = None) -> None:
        """Publish an event to all subscribers.
        If a subscriber callback is an async coroutine function, it is scheduled on
        the running event loop. If it's a sync function, it is called directly.
        """
        subscribers = self._subscribers.get(event_type, [])
        if not subscribers:
            return

        logger.debug(f"Publishing event '{event_type}' with data: {data}")
        # Take a copy of the list to prevent modification issues during iteration
        for callback in list(subscribers):
            if inspect.iscoroutinefunction(callback):
                try:
                    # Run async coroutine safely by scheduling it as a task in the running loop
                    asyncio.create_task(self._safe_await(callback, data, event_type))
                except RuntimeError:
                    # If there's no running event loop (e.g. during certain test configurations)
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
