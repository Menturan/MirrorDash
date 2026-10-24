# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The module host (ARCHITECTURE #41): `python -m mirrordash_core.features.modules.host <socket>`.

Every module runs here, apart from the core: on a mirror as its own user in its own sandbox
(mirrordash-modules.service), with no way to the core's files, its routes or the home network. The core
connects to the unix socket (client.py) and both send one JSON object per line:

  core -> host  start {gen, modules, globals, awake}   (re)start the modules with these settings
                awake {on}                              the screen went on or off
                event {name, data}                      a hardware event for the modules' event bus
                http {id, instance, method, path, ...}  a call to /api/modules/<instance>/...
                check {id, python, names}               do these modules import in that venv?
                reply {id, window}                      the answer to video_window
  host -> core  html {gen, instance, html}              an instance's card (the core places it)
                status {gen, running}                   the instances whose run_loop runs
                video_playing {instance, on}
                video_window {id, instance, fullscreen, hz}, video_window_end {id}
                log {name, level, text}
                reply {id, ...}                         the answer to http or check

The host serves one connection. When it ends (the core restarted, or was updated), the host stops its
modules and exits; systemd starts it again, so a new venv's modules load fresh.
"""

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import sys
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI

from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.modules.loader import SAFE_NAME, ModuleLoader

logger = logging.getLogger("mirrordash.core.modules.host")

LINE_LIMIT = 16 * 1024 * 1024  # one message; a bigger one ends the connection
RESPONSE_LIMIT = 4 * 1024 * 1024  # a module route's answer
REQUEST_TIMEOUT = 30
CHECK_TIMEOUT = 180
VIDEO_WINDOW_TIMEOUT = 30  # the core asks the page (5 s) and switches the refresh rate first
# The upgrade check (service.py update_modules): the upgraded modules must import in the new venv
CHECK = ("import sys; from importlib.metadata import entry_points; "
         "[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in sys.argv[1:]]")


class HostLink:
    """The modules' way to the core: what loader.py's helpers and broadcast_func call."""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.awake = asyncio.Event()  # set while the screen is on: fetch_json waits on it
        self.awake.set()
        self.gen = 0
        self.pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()

    async def send(self, message: dict) -> None:
        line = json.dumps(message, default=str).encode() + b"\n"
        async with self._lock:
            self.writer.write(line)
            await self.writer.drain()

    async def html(self, instance_id: str, html: str) -> None:
        await self.send({"t": "html", "gen": self.gen, "instance": instance_id, "html": str(html)})

    async def video_playing(self, instance_id: str, playing: bool) -> None:
        await self.send({"t": "video_playing", "instance": instance_id, "on": bool(playing)})

    @contextlib.asynccontextmanager
    async def video_window(self, instance_id: str, *, fullscreen: bool = False, hz: int | None = None):
        """features/video/window.py's video_window, held by the core for as long as the block runs."""
        request = next(self._ids)
        future = self.pending[request] = asyncio.get_running_loop().create_future()
        try:
            await self.send({"t": "video_window", "id": request, "instance": instance_id,
                             "fullscreen": bool(fullscreen), "hz": hz})
            window = (await asyncio.wait_for(future, VIDEO_WINDOW_TIMEOUT)).get("window")
            if not isinstance(window, dict):
                raise RuntimeError("the mirror couldn't place the video window (see its log)")
            yield SimpleNamespace(**window)
        finally:
            self.pending.pop(request, None)
            with contextlib.suppress(Exception):  # the core has gone: the host is about to exit anyway
                await self.send({"t": "video_window_end", "id": request})


class LogForward(logging.Handler):
    """Every log record of the host goes to the core, which writes it to its log under mirrordash.modules
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


class Host:
    """One connection from the core, and the modules it runs."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.link = HostLink(writer)
        self.loader = ModuleLoader(self.link)
        self.apps: dict[str, FastAPI] = {}  # a FastAPI app around each instance's router, made on its first call
        self._start_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        forward = LogForward(self.link, asyncio.get_running_loop())
        logging.getLogger().addHandler(forward)
        try:
            while line := await self.reader.readline():
                try:
                    message = json.loads(line)
                    self._handle(message)
                except Exception as e:
                    logger.error(f"Bad message from the core: {e}")
        except (ConnectionError, ValueError) as e:  # ValueError: a line over LINE_LIMIT
            logger.warning(f"The connection to the core ended: {e}")
        finally:
            logging.getLogger().removeHandler(forward)
            for task in self._tasks:
                task.cancel()
            await self.loader.stop_modules()
            self.link.writer.close()

    def _spawn(self, coro) -> None:
        """Run a message's work beside the read loop, so a slow module doesn't hold up the others."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle(self, message: dict) -> None:
        kind = message.get("t")
        if kind == "start":
            self._spawn(self._start(message))
        elif kind == "awake":
            self.link.awake.set() if message.get("on") else self.link.awake.clear()
        elif kind == "event":
            event_bus.publish(str(message.get("name")), message.get("data"))
        elif kind == "http":
            self._spawn(self._http(message))
        elif kind == "check":
            self._spawn(self._check(message))
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
            await self.loader.start_modules(message.get("modules") or {}, message.get("globals") or {})
            await self.link.send({"t": "status", "gen": self.link.gen, "running": sorted(self.loader.tasks)})

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

    async def _check(self, message: dict) -> None:
        names = [n for n in message.get("names", []) if isinstance(n, str) and SAFE_NAME.match(n)]
        code, err = 1, ""
        try:
            process = await asyncio.create_subprocess_exec(
                str(message.get("python")), "-I", "-c", CHECK, *names,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), CHECK_TIMEOUT)
            except TimeoutError:
                process.kill()
                await process.wait()
                stderr = b"timed out"
            code, err = process.returncode, stderr.decode(errors="replace")[-4000:]
        except OSError as e:
            err = str(e)
        await self.link.send({"t": "reply", "id": message.get("id"), "code": code, "err": err})


async def serve(path: str) -> None:
    """Serve the core's connection on the unix socket at path, and return when it ends."""
    done = asyncio.Event()
    busy = False

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal busy
        if busy:  # one core at a time
            writer.close()
            return
        busy = True
        try:
            await Host(reader, writer).run()
        finally:
            done.set()

    server = await asyncio.start_unix_server(connected, path=path, limit=LINE_LIMIT)
    async with server:
        await done.wait()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().handlers[0].setLevel(logging.WARNING)  # the rest reaches the core's log (LogForward)
    asyncio.run(serve(sys.argv[1]))


if __name__ == "__main__":
    main()
