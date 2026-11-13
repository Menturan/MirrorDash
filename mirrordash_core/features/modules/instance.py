# SPDX-License-Identifier: AGPL-3.0-only
"""One module instance's process in the module host (ARCHITECTURE #41), as the instance's own user.

The forkserver makes this process when the core starts an instance (host.py CHILDREN["module"]). It holds
one connection to the core, the host protocol in host.py's docstring, and runs the module in it:

- Host reads the core's messages (start, awake, hidden, event, http, ...) and starts the module with
  ModuleLoader.
- HostLink is the module's way back to the core: its card's HTML, its log lines (LogForward), video, GPIO
  and what it exposes. loader.py's helpers (self.fetch, self.video_window, self.gpio, ...) call it.
- call_route runs a call to /api/modules/<instance>/ through the module's own routes.
"""

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import os
import socket
import struct
import threading

from fastapi import APIRouter, FastAPI

from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.hardware.gpio import GpioLines
from mirrordash_core.features.modules.choices import setting_choices
from mirrordash_core.features.modules.loader import ModuleLoader
from mirrordash_core.features.modules.preview import render_preview
from mirrordash_core.features.modules.video_playback import VideoPlayback

logger = logging.getLogger("mirrordash.core.modules.host")

LINE_LIMIT = 16 * 1024 * 1024  # one message; a bigger one ends the connection
RESPONSE_LIMIT = 4 * 1024 * 1024  # a module route's answer
REQUEST_TIMEOUT = 30
GPIO_TIMEOUT = 10
VIDEO_WINDOW_TIMEOUT = 30  # the core asks the page (5 s) and switches the refresh rate first


class HostLink:
    """The modules' way to the core: what loader.py's helpers and broadcast_func call."""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.awake = asyncio.Event()  # set while the screen is on: fetch_json waits on it
        self.awake.set()
        self.hidden = False  # the screen hides its card: its schedule, or privacy mode
        self.gen = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.videos: dict[int, VideoPlayback] = {}  # its video_window blocks, by request
        self.gpio_lines: dict[str, list[GpioLines]] = {}  # each instance's, released when its run_loop ends
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    async def send(self, message: dict) -> None:
        line = json.dumps(message, default=str).encode() + b"\n"
        async with self._lock:
            self.writer.write(line)
            await self.writer.drain()

    async def html(self, instance_id: str, html: str) -> None:
        await self.send({"t": "html", "gen": self.gen, "instance": instance_id, "html": str(html)})

    async def state(self, instance_id: str, name: str, value) -> None:
        """`await self.expose("now_playing", title)` in a module: a value of something its config_schema.json's
        "exposes" declares, published if the owner shares it (ARCHITECTURE #42)."""
        await self.send({"t": "state", "instance": instance_id, "name": str(name), "value": value})

    async def video_playing(self, instance_id: str, playing: bool) -> None:
        await self.send({"t": "video_playing", "instance": instance_id, "on": bool(playing)})

    @contextlib.asynccontextmanager
    async def video_window(self, instance_id: str, *, url: str, output: str = "jack", volume: float = 1.0,
                           fullscreen: bool = False, hz: int | None = None):
        """The core plays url in a window (features/video/playback.py) for as long as the block runs, and yields
        a VideoPlayback with its events. What the module asks for is checked by the core, which says why not."""
        request = next(self._ids)
        video = self.videos[request] = VideoPlayback()
        future = self.pending[request] = asyncio.get_running_loop().create_future()
        try:
            await self.send({"t": "video_window", "id": request, "instance": instance_id, "url": url, "output": output,
                             "volume": volume, "fullscreen": bool(fullscreen), "hz": hz})
            reply = await asyncio.wait_for(future, VIDEO_WINDOW_TIMEOUT)
            if reply.get("error"):
                raise RuntimeError(reply["error"])
            yield video
        finally:
            self.pending.pop(request, None)
            self.videos.pop(request, None)
            with contextlib.suppress(Exception):  # the core has gone: the host is about to exit anyway
                await self.send({"t": "video_window_end", "id": request})

    async def gpio(self, instance_id: str, pins: list[int], **options) -> GpioLines:
        """`await self.gpio([17], output=True)` in a module: these GPIO lines, requested by the core if they
        are free (features/hardware/gpio.py). options: output, initial, pull, edge, active_low, debounce_ms."""
        request = next(self._ids)
        future = self.pending[request] = asyncio.get_running_loop().create_future()
        try:
            await self.send({"t": "gpio", "id": request, "instance": instance_id, "pins": list(pins), **options})
            reply = await asyncio.wait_for(future, GPIO_TIMEOUT)
        finally:
            self.pending.pop(request, None)
        if "fd" not in reply:
            raise RuntimeError(f"No GPIO {', '.join(map(str, pins))}: {reply.get('error') or 'the mirror did not answer'}")
        lines = GpioLines(reply["fd"], pins)
        self.gpio_lines.setdefault(instance_id, []).append(lines)
        return lines

    def release_gpio(self, instance_id: str) -> None:
        """Its run_loop ended (loader.py): its lines go back, whether or not it closed them."""
        for lines in self.gpio_lines.pop(instance_id, []):
            lines.close()

    def fd_arrived(self, request: int, fd: int) -> None:
        """A file descriptor from the core (receive_fds): the answer to that GPIO request."""
        future = self.pending.get(request)
        if future and not future.done():
            future.set_result({"fd": fd})
        else:
            os.close(fd)  # its request timed out meanwhile


def receive_fds(conn: socket.socket, link: HostLink, loop: asyncio.AbstractEventLoop) -> None:
    """In a thread: the instance's descriptor socket, each message a request id with one file descriptor."""
    with conn:
        while True:
            try:
                data, fds, _, _ = socket.recv_fds(conn, 8, 1)
            except OSError:
                return
            if not data:
                return
            for fd in fds:
                if len(data) == 8:
                    loop.call_soon_threadsafe(link.fd_arrived, struct.unpack("<Q", data)[0], fd)
                else:
                    os.close(fd)


class LogForward(logging.Handler):
    """Every log record of the process goes to the core, which writes it to its log under mirrordash.modules
    (the admin page's Logs). From any thread: a module's fetch logs from one."""

    def __init__(self, link: HostLink, loop: asyncio.AbstractEventLoop):
        super().__init__(logging.INFO)
        self.link, self.loop = link, loop
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = {"t": "log", "name": record.name, "level": record.levelno, "text": self.format(record)}
            asyncio.run_coroutine_threadsafe(self.link.send(message), self.loop)
        except Exception:
            pass  # never log from here: it would come back


async def call_route(app, message: dict) -> tuple[int, list, bytes]:
    """Run one HTTP call through a module's routes, as uvicorn would. (status, headers, body)."""
    root = f"/api/modules/{message['instance']}"
    path = root + str(message.get("path") or "/")
    body = base64.b64decode(message.get("body") or "")
    client = message.get("client")
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
        "method": str(message.get("method", "GET")), "path": path, "raw_path": path.encode(),
        "root_path": root, "query_string": str(message.get("query", "")).encode("latin-1"),
        "headers": [(str(k).lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in message.get("headers", [])],
        "client": (client, 0) if isinstance(client, str) else None, "server": ("mirrordash", 80),
    }
    received = False

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.get_running_loop().create_future()  # the client never leaves: the core holds it

    status, headers, chunks, size = 500, [], [], 0

    async def send(event: dict) -> None:
        nonlocal status, headers, size
        if event["type"] == "http.response.start":
            status = event["status"]
            headers = [[k.decode("latin-1"), v.decode("latin-1")] for k, v in event.get("headers", [])]
        elif event["type"] == "http.response.body":
            chunks.append(event.get("body", b""))
            size += len(chunks[-1])
            if size > RESPONSE_LIMIT:
                raise ValueError(f"answer larger than {RESPONSE_LIMIT} bytes")

    await asyncio.wait_for(app(scope, receive, send), REQUEST_TIMEOUT)
    return status, headers, b"".join(chunks)


async def read_lines(reader: asyncio.StreamReader, handle) -> None:
    """handle(message) for each line from the core, until it closes."""
    try:
        while line := await reader.readline():
            try:
                handle(json.loads(line))
            except Exception as e:
                logger.error(f"Bad message from the core: {e}")
    except (ConnectionError, ValueError) as e:  # ValueError: a line over LINE_LIMIT
        logger.warning(f"The connection to the core ended: {e}")


class Host:
    """One instance's connection from the core, and the module it runs (in the instance's own process)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.link = HostLink(writer)
        self.loader = ModuleLoader(self.link)
        self.apps: dict[str, FastAPI] = {}  # a FastAPI app around each instance's router, made on its first call
        self._start_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        forward = LogForward(self.link, loop)
        logging.getLogger().addHandler(forward)

        def share(name: str, data) -> None:
            """A public event (event_bus.publish(..., public=True)), from any thread: to the core."""
            message = {"t": "publish", "name": str(name), "data": data}
            json.dumps(message)  # not JSON: the module's publish raises, rather than send it changed
            loop.call_soon_threadsafe(lambda: self._spawn(self.link.send(message)))
        event_bus.share = share
        try:
            await read_lines(self.reader, self._handle)
        finally:
            event_bus.share = None
            logging.getLogger().removeHandler(forward)
            for task in self._tasks:
                task.cancel()
            await self.loader.stop_modules()
            self.link.writer.close()

    def _spawn(self, coro) -> None:
        """Run a message's work beside the read loop, so a slow route doesn't hold up the rest."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "start":
            self._spawn(self._start(message))
        elif kind == "awake":
            self.link.awake.set() if message.get("on") else self.link.awake.clear()
            self._spawn(self.loader.shown(self.link.awake.is_set() and not self.link.hidden))
        elif kind == "hidden":
            self.link.hidden = message.get("on") is True
            self._spawn(self.loader.shown(self.link.awake.is_set() and not self.link.hidden))
        elif kind == "event":
            event_bus.publish(str(message.get("name")), message.get("data"))
        elif kind == "http":
            self._spawn(self._http(message))
        elif kind == "preview":
            self._spawn(self._preview(message))
        elif kind == "choices":
            self._spawn(self._choices(message))
        elif kind == "control":
            self._spawn(self.loader.control(str(message.get("name")), message.get("value")))
        elif kind == "diagnose":
            gpio = {i: [p for lines in held for p in lines.pins] for i, held in self.link.gpio_lines.items()}
            self._spawn(self.link.send({"t": "reply", "id": message.get("id"), "gpio": gpio}))
        elif kind == "video":
            if video := self.link.videos.get(message.get("id")):
                video.event(message)
        elif kind == "reply":
            future = self.link.pending.get(message.get("id"))
            if future and not future.done():
                future.set_result(message)

    async def _start(self, message: dict) -> None:
        async with self._start_lock:
            await self.loader.stop_modules()
            self.apps.clear()
            self.link.gen = message.get("gen", 0)
            self.link.awake.set() if message.get("awake", True) else self.link.awake.clear()
            self.link.hidden = message.get("hidden") is True
            await self.loader.start_modules(message.get("modules") or {}, message.get("globals") or {})
            await self.loader.shown(self.link.awake.is_set() and not self.link.hidden)
            await self.link.send({"t": "status", "gen": self.link.gen, "running": sorted(self.loader.tasks)})

    async def _preview(self, message: dict) -> None:
        """The admin's preview: the card the unsaved settings would show, from the module's sample answers
        (preview.render_preview); html None when the module has none, or rendering failed."""
        html = None
        module_cfg = message.get("config")
        if isinstance(module_cfg, dict) and isinstance(message.get("instance"), str):
            try:
                html = await render_preview(message["instance"], module_cfg, message.get("globals") or {}, self.link)
            except Exception as e:
                logger.warning(f"{message['instance']}: its preview failed: {e!r}")
        await self.link.send({"t": "reply", "id": message.get("id"), "html": html if isinstance(html, str) else None})

    async def _choices(self, message: dict) -> None:
        """The choices of a list or search setting in the admin's unsaved form (choices.py)."""
        reply = {"error": "Not a list or search setting."}
        if isinstance(message.get("config"), dict) and isinstance(message.get("instance"), str):
            reply = await setting_choices(message["instance"], message["config"], message.get("globals") or {},
                                          self.link, message)
        await self.link.send({"t": "reply", "id": message.get("id"), **reply})

    async def _http(self, message: dict) -> None:
        instance_id = message.get("instance")
        router = getattr(self.loader.instances.get(instance_id), "router", None)
        status, headers, body = 404, [["content-type", "application/json"]], b'{"detail":"Not Found"}'
        if isinstance(router, APIRouter):
            app = self.apps.get(instance_id)
            if app is None:
                app = self.apps[instance_id] = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
                app.include_router(router)
            try:
                status, headers, body = await call_route(app, message)
            except Exception as e:
                logger.error(f"{instance_id}: {message.get('method')} {message.get('path')} failed: {e!r}")
                status, headers, body = 500, [["content-type", "text/plain"]], b"Internal Server Error"
        await self.link.send({"t": "reply", "id": message.get("id"), "status": status, "headers": headers,
                              "body": base64.b64encode(body).decode()})


async def instance_main(stream: socket.socket, fds: socket.socket, instance: str) -> None:
    """One module instance, as its own user: the core's connection for it and the module (which adds its
    own folder only: ModuleLoader.start_modules)."""
    reader, writer = await asyncio.open_unix_connection(sock=stream, limit=LINE_LIMIT)
    host = Host(reader, writer)
    threading.Thread(target=receive_fds, args=(fds, host.link, asyncio.get_running_loop()), daemon=True).start()
    await host.run()
