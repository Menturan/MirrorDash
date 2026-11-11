# SPDX-License-Identifier: AGPL-3.0-only
"""One connection from the core to the module host (client.py): lines of JSON, and replies matched to requests."""

import asyncio
import collections
import itertools
import json
import socket
import time

LINE_LIMIT = 16 * 1024 * 1024  # as instance.py
EVENT_RATE_PER_SECOND = 20  # public events an instance may send a second; more are dropped
LOG_BURST = 100  # log lines an instance may send in from_instances.LOG_WINDOW seconds; more are counted, not logged


class Connection:
    """One connection to the module host: the control process's, or an instance's own process's."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 descriptor_socket: socket.socket | None = None, instance: str | None = None):
        self.reader, self.writer, self.instance = reader, writer, instance
        # Where file descriptors go to the host (SOCK_SEQPACKET): a GPIO request's lines, or an instance's sockets
        self.descriptor_socket = descriptor_socket
        self.pending: dict[int, asyncio.Future] = {}
        self.windows: dict[int, asyncio.Event] = {}  # its video windows the core holds
        self.key: str | None = None  # an instance's settings_key
        self.html: str | None = None  # its last card, sent again when the screen reloads
        self.running = False
        self.started = time.monotonic()
        self.module = ""  # an instance's module (its package name): the start of its public events' names
        self.published: collections.deque[float] = collections.deque(maxlen=EVENT_RATE_PER_SECOND)  # when, the last ones
        self.logged: collections.deque[float] = collections.deque(maxlen=LOG_BURST)  # when, the last log lines
        self.dropped = 0  # log lines left out since the last one logged
        self.shown = 0.0  # when its card was last sent to the screen
        self.showing: asyncio.Task | None = None  # its next card's send, waiting for HTML_INTERVAL
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    async def send(self, message: dict) -> None:
        line = json.dumps(message).encode() + b"\n"
        async with self._lock:
            self.writer.write(line)
            await self.writer.drain()

    async def request(self, message: dict, timeout: float) -> dict:
        request = next(self._ids)
        future = self.pending[request] = asyncio.get_running_loop().create_future()
        try:
            await self.send({**message, "id": request})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request, None)

    def close(self) -> None:
        self.writer.close()
        if self.descriptor_socket:
            self.descriptor_socket.close()
        for event in self.windows.values():
            event.set()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("the module host went away"))
