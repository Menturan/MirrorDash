# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The module host (ARCHITECTURE #41): `python -m mirrordash_core.features.modules.host <socket>`.

Every module runs apart from the core: on a mirror in its own sandbox (mirrordash-modules.service), each
instance in a process of its own as a user of its own, with no way to the core's files, its routes, the
home network or the other modules. forkserver.py makes the processes; the control process (serve, as the
unit's user) answers the core's connection on the unix socket, and both send one JSON object per line:

  core -> control  uids {id, instances}              each instance's user (module_uids)
                   site {id, op, ...}                a change to the installed modules (site_step)
                   diagnose {id}                     the sandbox's checks, made as a module's user (probe_main)
  control -> core  reply {id, ...}, log {name, level, text}

To start an instance the core makes two socket pairs, for its messages and for GPIO's descriptors, and
sends one end of each over the second connection (<socket>.fds, SOCK_SEQPACKET) with {"instance": id}: the
process the forkserver makes for it gets them, so its connection is who it is. Over it, the host protocol
for that one instance (Host):

  core -> instance  start {gen, modules, globals, awake}   start it with these settings
                    awake {on}                              the screen went on or off
                    event {name, data}                      a hardware event for the modules' event bus
                    http {id, instance, method, path, ...}  a call to /api/modules/<instance>/...
                    diagnose {id}                           its GPIO lines (Logs → Diagnostics)
                    reply {id, window}                      the answer to video_window
  instance -> core  html {gen, instance, html}              its card (the core places it)
                    status {gen, running}                   whether its run_loop runs
                    video_playing {instance, on}
                    video_window {id, instance, fullscreen, hz}, video_window_end {id}
                    gpio {id, instance, pins, output, ...}  lines for it (features/hardware/gpio.py)
                    log {name, level, text}
                    reply {id, ...}                         the answer to http or diagnose

A GPIO request's answer is a file descriptor, sent over the instance's descriptor socket as one message
holding the request's id. An instance stops when the core closes its connection. When the core's
connection to the control process ends (the core restarted, or was updated), the host exits and systemd
starts it again, so a new venv's modules load fresh.

The modules are installed in the module site (loader.module_site), in the host's own folder: uv runs in a
process of its own (site_main, as forkserver.SITE_UID), so a package's build and its .pth never run as the
core, the forkserver or another module.
"""

import asyncio
import base64
import contextlib
import glob
import itertools
import json
import logging
import os
import shutil
import site
import socket
import struct
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI

from mirrordash_core.config import get_module_dir
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.hardware.gpio import GpioLines
from mirrordash_core.features.modules import forkserver
from mirrordash_core.features.modules.loader import SAFE_NAME, ModuleLoader, module_site
from mirrordash_core.venv import point_link

logger = logging.getLogger("mirrordash.core.modules.host")

LINE_LIMIT = 16 * 1024 * 1024  # one message; a bigger one ends the connection
RESPONSE_LIMIT = 4 * 1024 * 1024  # a module route's answer
REQUEST_TIMEOUT = 30
GPIO_TIMEOUT = 10
CHECK_TIMEOUT = 180
UV_TIMEOUT = 600  # one uv pip run, unless the core asks for another
SITE_STEP_TIMEOUT = 200  # the other steps
PROBE_TIMEOUT = 60
VIDEO_WINDOW_TIMEOUT = 30  # the core asks the page (5 s) and switches the refresh rate first
# The check of a changed site (service.py update_modules): these modules must import from it
CHECK = ("import site, sys; site.addsitedir(sys.argv[1]); from importlib.metadata import entry_points; "
         "[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in sys.argv[2:]]")


SANDBOX_PORTS = (80, 8000)  # the mirror's own site (nginx) and the core behind it
ROUTES = "/proc/self/net/route"  # the router, the way into the home network (/proc/net is hidden by ProcSubset=pid)
# mirrordash-modules.service's InaccessiblePaths and ProtectHome
SANDBOX_PATHS = {"/storage/mirrordash/data": "the core's data", "/storage/mirrordash/ssh": "the SSH keys",
                 "/storage/mirrordash/system-connections": "the Wi-Fi passwords", "/storage/rauc": "the OS update keys",
                 "/home": "the home folders", "/run/dbus": "the system bus"}
# Its DevicePolicy=closed and the I2C rule (90-mirrordash-i2c.rules): GPIO only through the core, no other I2C bus
SANDBOX_DEVICES = ("/dev/gpiochip*", "/dev/gpiomem*", "/dev/mem", "/dev/i2c-*", "/dev/input/event*", "/dev/video*")
MODULE_DEVICES = {"/dev/i2c-1"}  # the header's I2C bus


def reaches(host: str, port: int) -> bool:
    """A connection gets through, or an answer comes back (refused): the address is in reach. A filter says EPERM."""
    try:
        socket.create_connection((host, port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    except OSError:
        return False
    return True


def opens(path: str) -> bool:
    try:
        os.close(os.open(path, os.O_RDWR | os.O_NONBLOCK))
    except OSError:
        return False
    return True


def default_gateway() -> str | None:
    with contextlib.suppress(OSError, ValueError, IndexError), open(ROUTES) as f:
        for fields in (line.split() for line in f.readlines()[1:]):
            if fields[1] == "00000000" and fields[2] != "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return None


def data_folders() -> list[str]:
    """The instances' data folders, as the control process sees them (a module's user can't list them)."""
    with contextlib.suppress(OSError):
        return sorted(e.name for e in os.scandir(get_module_dir() / "data") if SAFE_NAME.match(e.name))
    return []


def sandbox_checks(data: list[str] | None = None) -> list[tuple[str, bool]]:
    """What the sandbox keeps from a module, looked at as one (probe_main): (what, reachable), each should
    be False. systemd only warns when the kernel lacks what a filter needs (cgroup BPF for IPAddressDeny, the
    device cgroup, namespaces), and a user per module holds only if its capabilities are gone and the
    folders' modes are right, so the host looks: when the core connects, and from Logs → Diagnostics.
    data: the instances' data folders (default: those this process can list)."""
    checks = [(f"the mirror's own port {port}", reaches("127.0.0.1", port)) for port in SANDBOX_PORTS]
    if gateway := default_gateway():
        checks.append((f"the router ({gateway})", reaches(gateway, 53)))
    checks += [(f"{what} ({path})", os.access(path, os.R_OK)) for path, what in SANDBOX_PATHS.items()]
    devices = sorted({p for pattern in SANDBOX_DEVICES for p in glob.glob(pattern)} - MODULE_DEVICES)
    checks += [(path, opens(path)) for path in devices]
    checks += [(f"another module's data ({name})", os.access(get_module_dir() / "data" / name, os.R_OK))
               for name in (data_folders() if data is None else data)]
    checks.append((f"writing the module site ({module_site()})", os.access(module_site(), os.W_OK)))
    checks.append(("capabilities (" + (", ".join(forkserver.capabilities()) or "none") + ")",
                   bool(forkserver.capabilities())))
    return checks


def site_paths() -> tuple[Path, Path, Path]:
    """(link, active, next): the module site is a link to site_a or site_b, as the core's venv is."""
    link = module_site()
    site_a, site_b = link.with_name("site_a"), link.with_name("site_b")
    active = site_b if link.is_symlink() and os.readlink(link) == "site_b" else site_a
    return link, active, site_a if active == site_b else site_b


def copy_site(src: Path, dst: Path) -> None:
    """shutil.copytree(src, dst, symlinks=True), but the folders' modes aren't copied: they carry the modules
    folder's setgid bit, and setting it is what RestrictSUIDSGID= forbids (EPERM: every module change after
    the first failed on the test mirror). A new folder gets the bit from its parent anyway."""
    for root, dirs, files in os.walk(src):
        target = dst / os.path.relpath(root, src)
        target.mkdir(parents=True, exist_ok=True)
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            path = os.path.join(root, name)
            if os.path.islink(path):
                os.symlink(os.readlink(path), target / name)
            else:
                shutil.copy2(path, target / name)


def empty(folder: Path) -> None:
    """Everything in folder, not the folder: the site's folders are the site user's in the host's, which it
    can't write (forkserver.own_folder makes them)."""
    folder.mkdir(parents=True, exist_ok=True)
    for entry in os.scandir(folder):
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path)
        else:
            os.unlink(entry.path)


async def run(*cmd: str, timeout: float) -> tuple[int, str]:
    """(returncode, the end of stderr); 124 after timeout seconds, like timeout(1)."""
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                       stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        return 1, str(e)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, f"took more than {timeout:.0f} s"
    return process.returncode, stderr.decode(errors="replace")[-4000:]


class HostLink:
    """The modules' way to the core: what loader.py's helpers and broadcast_func call."""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.awake = asyncio.Event()  # set while the screen is on: fetch_json waits on it
        self.awake.set()
        self.gen = 0
        self.pending: dict[int, asyncio.Future] = {}
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
        elif kind == "event":
            event_bus.publish(str(message.get("name")), message.get("data"))
        elif kind == "http":
            self._spawn(self._http(message))
        elif kind == "diagnose":
            gpio = {i: [p for lines in held for p in lines.pins] for i, held in self.link.gpio_lines.items()}
            self._spawn(self.link.send({"t": "reply", "id": message.get("id"), "gpio": gpio}))
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


# The processes the forkserver makes (forkserver.run's children), each an async function of its sockets


async def instance_main(stream: socket.socket, fds: socket.socket, instance: str) -> None:
    """One module instance, as its own user: the core's connection for it and the module."""
    # After the core's own packages: a module's copy of one (uv installs every dependency) never shadows them.
    # Only here, as the module's user: the site's .pth files run on adding it.
    site.addsitedir(str(module_site()))
    reader, writer = await asyncio.open_unix_connection(sock=stream, limit=LINE_LIMIT)
    host = Host(reader, writer)
    threading.Thread(target=receive_fds, args=(fds, host.link, asyncio.get_running_loop()), daemon=True).start()
    await host.run()


async def site_step(message: dict) -> tuple[int, str]:
    """One step of a change to the installed modules (client.py's ModuleHost.packages), like the core's
    venv_swap: prepare the next site (a copy of the active one, or empty when fresh), run uv pip in it,
    import modules from it, or drop it. The control process switches the link (commit)."""
    op, python = message.get("op"), str(message.get("python") or "")
    _, active, next_site = site_paths()
    if op == "prepare":
        await asyncio.to_thread(empty, next_site)
        if active.is_dir() and not message.get("fresh"):
            await asyncio.to_thread(copy_site, active, next_site)
    elif op == "pip":
        args = [str(a) for a in message.get("args") or []]
        timeout = message.get("timeout") if isinstance(message.get("timeout"), int) else UV_TIMEOUT
        if args[:1] not in (["install"], ["uninstall"]):
            return 1, f"not a uv pip change: {args[:1]}"
        # --no-cache: the host's folder is on /storage, which is small
        code, err = await run("uv", "pip", args[0], "--python", python, "--target", str(next_site),
                              "--no-cache", *args[1:], timeout=timeout)
        if code == 0:  # a module's process can't write the site, so its bytecode is made here
            await run(python, "-I", "-m", "compileall", "-q", str(next_site), timeout=timeout)
        return code, err
    elif op == "check":
        names = [n for n in message.get("names", []) if isinstance(n, str) and SAFE_NAME.match(n)]
        return await run(python, "-I", "-c", CHECK, str(next_site), *names, timeout=CHECK_TIMEOUT)
    elif op == "revert":
        await asyncio.to_thread(empty, next_site)
    else:
        return 1, f"unknown site step {op!r}"
    return 0, ""


async def site_main(stream: socket.socket, _instance=None) -> None:
    """One site step, as the site's user: a line in, the answer out."""
    reader, writer = await asyncio.open_unix_connection(sock=stream, limit=LINE_LIMIT)
    try:
        code, err = await site_step(json.loads(await reader.readline()))
    except (OSError, ValueError) as e:
        code, err = 1, str(e)
    writer.write(json.dumps({"code": code, "err": err}).encode() + b"\n")
    await writer.drain()
    writer.close()


async def probe_main(stream: socket.socket, _instance=None) -> None:
    """The sandbox's checks, as a module's user would see it (forkserver.PROBE_UID)."""
    reader, writer = await asyncio.open_unix_connection(sock=stream)
    data = json.loads(await reader.readline()).get("data")
    data = [n for n in data if isinstance(n, str) and SAFE_NAME.match(n)] if isinstance(data, list) else []
    writer.write(json.dumps({"checks": await asyncio.to_thread(sandbox_checks, data)}).encode() + b"\n")
    await writer.drain()
    writer.close()


CHILDREN = {"module": instance_main, "site": site_main, "probe": probe_main}


def module_uids(instances: list[str]) -> dict[str, int]:
    """Each instance's user, kept in the host's folder (/storage outlives the image) so its files stay its
    own. A new instance gets the next uid, never one that was another's: that one's data may still be there.
    ponytail: forkserver.MODULE_UIDS holds 900 instances ever made on a mirror; past that a new one doesn't start."""
    path = get_module_dir() / "uids.json"
    try:
        uids = {k: v for k, v in json.loads(path.read_text()).items() if type(v) is int and v in forkserver.MODULE_UIDS}
    except (OSError, ValueError, AttributeError):
        uids = {}
    new = [i for i in instances if i not in uids and SAFE_NAME.match(i)]
    for instance in new:
        uid = max(uids.values(), default=forkserver.MODULE_UIDS.start - 1) + 1
        if uid in forkserver.MODULE_UIDS:
            uids[instance] = uid
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("uids.json.tmp")
        tmp.write_text(json.dumps(uids, indent=1))
        os.replace(tmp, path)
    return {i: uids[i] for i in instances if i in uids}


class Control:
    """The core's connection to the control process: the users, the site and the checks."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, spawn):
        self.reader = reader
        self.link = HostLink(writer)
        self.spawn = spawn  # forkserver.Spawner, or a stand-in that runs CHILDREN here (the tests)
        self.sandboxed = bool(os.environ.get("MIRRORDASH_SANDBOX"))
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        forward = LogForward(self.link, asyncio.get_running_loop())
        logging.getLogger().addHandler(forward)
        if os.environ.get("MIRRORDASH_NO_USERS"):  # said once the core reads the log
            logger.error("This OS image can't give each module a user of its own, so no module runs: "
                         "install the system update (Settings → Updates)")
        elif self.sandboxed:
            self._spawn(self._check())
        try:
            await read_lines(self.reader, self._handle)
        finally:
            logging.getLogger().removeHandler(forward)
            for task in self._tasks:
                task.cancel()
            self.link.writer.close()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _handle(self, message: dict) -> None:
        kind, request = message.get("t"), message.get("id")
        if kind == "uids":
            instances = [i for i in message.get("instances") or [] if isinstance(i, str)]
            self._spawn(self.link.send({"t": "reply", "id": request, "uids": module_uids(instances)}))
        elif kind == "site":
            self._spawn(self._site(message))
        elif kind == "diagnose":
            self._spawn(self._diagnose(request))

    def instance(self, data: bytes, fds: list[int]) -> None:
        """The core's request to start an instance (receive_spawns): its sockets go to its process."""
        socks = [socket.socket(fileno=fd) for fd in fds]
        try:
            instance = json.loads(data).get("instance")
            uid = module_uids([instance]).get(instance) if isinstance(instance, str) else None
            if uid is None or len(socks) != 2:
                raise ValueError(f"no user for {instance!r}")
        except (ValueError, AttributeError, OSError) as e:
            logger.error(f"A module instance couldn't start: {e}")
            for s in socks:
                s.close()
            return
        self.spawn("module", uid, instance, socks)

    async def child(self, kind: str, uid: int, message: dict | None, timeout: float) -> dict:
        """Run a site step or the probe in a process of its own: the message in, its answer out."""
        ours, theirs = socket.socketpair()
        reader, writer = await asyncio.open_unix_connection(sock=ours, limit=LINE_LIMIT)
        try:
            self.spawn(kind, uid, None, [theirs])
            if message is not None:
                writer.write(json.dumps(message).encode() + b"\n")
                await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), timeout) or b"{}")
        finally:
            writer.close()

    async def probe(self) -> list:
        """sandbox_checks as the self-check's user, told the data folders it can't list."""
        try:
            reply = await self.child("probe", forkserver.PROBE_UID, {"data": data_folders()}, PROBE_TIMEOUT)
            return reply.get("checks") or []
        except (OSError, ValueError, TimeoutError) as e:
            return [(f"the self-check, which didn't finish ({e!r})", True)]

    async def _check(self) -> None:
        for what, reachable in await self.probe():
            if reachable:
                logger.error(f"The module sandbox is open: modules can reach {what}")

    async def _diagnose(self, request) -> None:
        await self.link.send({"t": "reply", "id": request, "checks": await self.probe(), "sandboxed": self.sandboxed})

    async def _site(self, message: dict) -> None:
        """One step of a change to the installed modules: in the site's process, but the link's switch (the
        host's folder is the unit's user's). The core runs one change at a time."""
        code, err = 0, ""
        try:
            if message.get("op") == "commit":
                link, _, next_site = site_paths()
                point_link(link, next_site)
            else:
                timeout = message.get("timeout") if isinstance(message.get("timeout"), int) else SITE_STEP_TIMEOUT
                reply = await self.child("site", forkserver.SITE_UID, message, timeout + 30)
                code = reply.get("code") if isinstance(reply.get("code"), int) else 1
                err = str(reply.get("err", "the step ended without an answer"))
        except (OSError, ValueError, TimeoutError) as e:
            code, err = 1, str(e) or repr(e)
        if code:
            logger.warning(f"Module site {message.get('op')} failed: {err.strip()}")
        await self.link.send({"t": "reply", "id": message.get("id"), "code": code, "err": err})


def receive_spawns(listener: socket.socket, control: Control, loop: asyncio.AbstractEventLoop) -> None:
    """In a thread: the core's second connection, each message an instance to start with its two sockets."""
    try:
        conn, _ = listener.accept()
    except OSError:
        return
    with conn:
        while True:
            try:
                data, fds, _, _ = socket.recv_fds(conn, 4096, 2)
            except OSError:
                return
            if not data:
                return
            loop.call_soon_threadsafe(control.instance, data, fds)


async def serve(path: str, spawn) -> None:
    """Serve the core's connection on the unix socket at path, and return when it ends."""
    done = asyncio.Event()
    busy = False
    # Before the main socket: the core connects here right after it (the instances' sockets)
    Path(path + ".fds").unlink(missing_ok=True)
    fd_listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fd_listener.bind(path + ".fds")
    fd_listener.listen(1)

    async def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal busy
        if busy:  # one core at a time
            writer.close()
            return
        busy = True
        control = Control(reader, writer, spawn)
        threading.Thread(target=receive_spawns, args=(fd_listener, control, asyncio.get_running_loop()),
                         daemon=True).start()
        try:
            await control.run()
        finally:
            done.set()

    server = await asyncio.start_unix_server(connected, path=path, limit=LINE_LIMIT)
    try:
        async with server:
            await done.wait()
    finally:
        with contextlib.suppress(OSError):
            fd_listener.shutdown(socket.SHUT_RDWR)  # wakes the thread's accept, if the core never came
        fd_listener.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().handlers[0].setLevel(logging.WARNING)  # the rest reaches the core's log (LogForward)
    path = sys.argv[1]
    forkserver.run(lambda sock: serve(path, forkserver.Spawner(sock)), CHILDREN)


if __name__ == "__main__":
    main()
