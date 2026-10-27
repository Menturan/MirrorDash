# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The core's side of the module host (host.py, ARCHITECTURE #41).

On a mirror the host is its own service (mirrordash-modules.service) and MIRRORDASH_MODULE_SOCKET says
where it listens; elsewhere (a developer's machine, the SDK's `start`) the core starts it as a child
process. Either way the modules run there, each instance in its own process with a connection of its own
from here, and everything they send is checked here like any other input from outside: an instance can
only put HTML in its own place and ask GPIO for itself (its connection is who it is), and its log lines
land under mirrordash.modules.<instance>.
"""

import asyncio
import base64
import collections
import contextlib
import ipaddress
import itertools
import json
import logging
import os
import re
import shutil
import socket
import struct
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from starlette.responses import Response
from starlette.routing import get_route_path
from starlette.websockets import WebSocketClose

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.hardware.gpio import free_pins, header_chip, request_lines
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules.forkserver import MODULE_UIDS
from mirrordash_core.features.modules.loader import (
    SAFE_NAME,
    find_entry_point,
    module_entry_points,
    scripts_allowed,
)
from mirrordash_core.venv import get_venv_paths

logger = logging.getLogger("mirrordash.core.modules.client")

LINE_LIMIT = 16 * 1024 * 1024  # as host.py
BODY_LIMIT = 1024 * 1024  # a call to a module's route
REQUEST_TIMEOUT = 35  # a little over the host's own
LAN_HELPER = "/usr/local/bin/mirrordash-module-network"  # an OS image file; the home-network addresses per module
LAN_REFRESH = 600  # seconds between looking up the lan-url hosts again (a DHCP address can change)
LAN_LIMIT = 64  # the helper's
EVENT_NAME = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_.:-]{1,100}")  # a public event's: "<module>.<what>"
EVENT_LIMIT = 64 * 1024  # a public event's data, as JSON
EVENT_RATE = 20  # public events an instance may send a second; more are dropped
HTML_LIMIT = 512 * 1024  # a card; a bigger one isn't shown (every open screen gets each card)
HTML_INTERVAL = 0.5  # seconds between a card's sends to the screen: one sent sooner waits, and only the last goes
LOG_BURST, LOG_WINDOW = 100, 10  # log lines an instance may send in LOG_WINDOW seconds; more are counted, not logged
# An instance's process ending this soon after its start, this many times in a row, pauses it: one that
# runs out of memory (the kernel kills it first, oom_score_adj) or crashes would start again forever
CRASH_WINDOW = 300
CRASH_LIMIT = 3
RESTART_DELAY = 5
# Its place on the screen: changing one moves the card without restarting the module
PLACEMENT_KEYS = {"position", "carousel_group", "carousel_interval", "max_width", "max_height", "z_index", "opacity"}
# Headers that never reach a module (the admin session, the admin password) or come back from one
# (a cookie on the mirror's own site, framing the core's answer, a service worker over the whole site,
# wiping the site's logins)
REQUEST_HEADERS_DROPPED = {"cookie", "x-api-key", "host", "content-length"}
RESPONSE_HEADERS_DROPPED = {"set-cookie", "content-length", "transfer-encoding", "connection",
                            "content-security-policy", "x-content-type-options",
                            "service-worker-allowed", "clear-site-data"}
LOG_NAME = re.compile(r"^[\w.-]{1,100}$")


def placement(module_cfg: dict, instance_id: str) -> dict:
    """The screen message's fields for an instance's place (js/kiosk/core.js reads them)."""
    return {
        "position": module_cfg.get("position", "middle_center"),
        "module": instance_id,
        "carousel_group": module_cfg.get("carousel_group"),
        "carousel_interval": module_cfg.get("carousel_interval", 15),
        "max_width": module_cfg.get("max_width"),
        "max_height": module_cfg.get("max_height"),
        "z_index": module_cfg.get("z_index"),
        "opacity": module_cfg.get("opacity"),
    }


def settings_key(module_cfg: dict, globals_cfg: dict) -> str:
    """What an instance was started with, its place aside: another key restarts it."""
    return json.dumps([{k: v for k, v in module_cfg.items() if k not in PLACEMENT_KEYS}, globals_cfg],
                      sort_keys=True, default=str)


def core_python() -> str:
    """The core's Python as the host reaches it: on a mirror through the venv link on /storage (the host
    can't see /home), so modules are built for the venv that runs now."""
    paths = get_venv_paths()
    return str(paths[0] / "bin" / "python") if paths else sys.executable


def lan_hosts(modules: dict) -> dict[str, set[str]]:
    """Each enabled instance's host names in its settings marked "format": "lan-url": what the owner typed
    (or kept) in the admin page, never a schema's default alone."""
    from mirrordash_core.features.settings.schema import _installed_module_schemas
    schemas = _installed_module_schemas()
    hosts = {}
    for instance_id, cfg in modules.items():
        if not cfg.get("enabled", True):
            continue
        schema = schemas.get(str(cfg.get("module", instance_id)).replace("-", "_")) or {}
        for key, prop in schema.get("properties", {}).items():
            value = cfg.get(key)
            if isinstance(prop, dict) and prop.get("format") == "lan-url" and isinstance(value, str) and value.strip():
                value = value.strip()
                host = urllib.parse.urlsplit(value if "//" in value else "//" + value).hostname
                if host:
                    hosts.setdefault(instance_id, set()).add(host)
    return hosts


async def lan_addresses(hosts: set[str]) -> dict[str, list[str]]:
    """The home-network addresses each name has now (an IP as is, a name looked up, mDNS too)."""
    loop = asyncio.get_running_loop()

    async def lookup(host: str) -> list:  # all at once: one name that doesn't answer holds the modules' start 5 s, not 5 s each
        try:
            return await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        except (OSError, TimeoutError, UnicodeError):
            logger.warning(f"Could not look up {host} for a module's home-network address")
            return []

    names = sorted(hosts)
    found = {}
    for host, infos in zip(names, await asyncio.gather(*map(lookup, names)), strict=True):
        addresses = set()
        for *_, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
            if ip.is_private and not ip.is_loopback and not ip.is_unspecified:
                addresses.add(str(ip))
        found[host] = sorted(addresses)
    return found


class Connection:
    """One connection to the module host: the control process's, or an instance's own process's."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 fds: socket.socket | None = None, instance: str | None = None):
        self.reader, self.writer, self.fds, self.instance = reader, writer, fds, instance
        self.pending: dict[int, asyncio.Future] = {}
        self.windows: dict[int, asyncio.Event] = {}  # its video windows the core holds
        self.key: str | None = None  # an instance's settings_key
        self.html: str | None = None  # its last card, sent again when the screen reloads
        self.running = False
        self.started = time.monotonic()
        self.module = ""  # an instance's module (its package name): the start of its public events' names
        self.published: collections.deque[float] = collections.deque(maxlen=EVENT_RATE)  # when, the last ones
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
        if self.fds:
            self.fds.close()
        for event in self.windows.values():
            event.set()
        for future in self.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("the module host went away"))


class ModuleHost:
    """The connections to the module host. The rest of the core asks this what runs (`running`) and has
    it reload the modules after a settings change."""

    def __init__(self):
        self.lan: list[str] | None = None  # what the instances may reach on the home network, once applied
        # ponytail: in memory, so a restart of the core tries them again (CRASH_LIMIT more crashes)
        self.paused: set[str] = set()  # instances that kept dying soon after their start: not started (resume)
        self._crashes: dict[str, int] = {}
        self._control: Connection | None = None
        self._instances: dict[str, Connection] = {}
        self._uids: dict[str, int] = {}
        self._placement: dict[str, dict] = {}
        self._sync_lock = asyncio.Lock()
        self._lan_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._process: asyncio.subprocess.Process | None = None
        self._socket_dir: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # The rest of the core

    @property
    def running(self) -> list[str]:
        """The instances whose run_loop runs, in the settings' order (the screen lays an area's cards out so)."""
        return [i for i in self._placement if (conn := self._instances.get(i)) and conn.running]

    async def start_modules(self) -> None:
        """At startup: connect to the host (and keep reconnecting) and start the modules there."""
        if os.environ.get("MIRRORDASH_BOOT_STATUS") == "safe_mode":
            # The core runs base_venv; the host would run the venv that failed, perhaps another version
            logger.warning("Safe mode: no modules")
            return
        self._loop = asyncio.get_running_loop()
        event_bus.forward = self.forward_event
        self._tasks = [asyncio.create_task(self._run(), name="module-host"),
                       asyncio.create_task(self._refresh_lan(), name="module-host-lan")]

    async def stop_modules(self) -> None:
        """At shutdown: each instance stops when its connection ends, the host when the control's does."""
        event_bus.forward = None
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self._disconnected()
        if self._process and self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), 5)
            except TimeoutError:
                self._process.kill()
        if self._socket_dir:
            shutil.rmtree(self._socket_dir, ignore_errors=True)
            self._socket_dir = None

    async def reload_modules(self) -> None:
        """After a settings change: the screen reloads, and the instances whose settings changed start again."""
        manager.clear_cache()
        await manager.broadcast({"action": "reload"})
        if self._control:
            try:
                await self._sync()
            except (ConnectionError, OSError, TimeoutError) as e:  # the next connection starts them with these settings
                logger.warning(f"The module host went away while the modules were reloaded: {e!r}")

    def set_awake(self, on: bool) -> None:
        """The screen went on or off (display_power.py): the modules' fetches sleep meanwhile."""
        self._post({"t": "awake", "on": bool(on)})

    def forward_event(self, name: str, data) -> None:
        """Every event published in the core (the hardware's) goes to each instance's event bus."""
        self._post({"t": "event", "name": name, "data": data})

    @contextlib.asynccontextmanager
    async def packages(self, python: str | None = None, fresh: bool = False):
        """Change the installed modules as venv_swap changes the core: in a copy of the module site (empty
        when fresh), by the host's site user, so no module's build or .pth runs as the core. Switched in
        when the block finishes, dropped when it raises. Yields pip(*args, timeout=600) for uv pip install
        or uninstall, and check(names), both (code, err), run with python (default the core's, as the host
        reaches it)."""
        python = python or core_python()
        code, err = await self._site("prepare", 150, fresh=fresh)
        if code:
            raise RuntimeError(err)
        try:
            yield SimpleNamespace(
                pip=lambda *args, timeout=600: self._site("pip", timeout + 60, python=python, args=args, timeout=timeout),
                check=lambda names: self._site("check", 240, python=python, names=list(names)))
        except BaseException:
            await self._site("revert", 150)
            raise
        code, err = await self._site("commit", 30)
        if code:
            raise RuntimeError(err)

    async def _site(self, op: str, wait: float, **fields) -> tuple[int, str]:
        """One step of a module site change (host.py's Control._site), given wait seconds: (code, err), never raises."""
        try:
            reply = await self._request({"t": "site", "op": op, **fields}, wait)
        except (ConnectionError, TimeoutError) as e:
            return 1, f"The modules aren't running, so their packages couldn't be changed ({e or 'no answer'})."
        return (reply.get("code") if isinstance(reply.get("code"), int) else 1), str(reply.get("err", ""))

    async def call_route(self, message: dict) -> dict:
        conn = self._instances.get(message.get("instance"))
        if conn is None:
            raise ConnectionError("the module isn't running")
        return await conn.request({"t": "http", **message}, REQUEST_TIMEOUT)

    async def diagnose(self) -> dict:
        """The sandbox's checks (host.py's Control._diagnose) and each instance's GPIO lines."""
        reply = await self._request({"t": "diagnose"}, 90)

        async def pins(conn: Connection) -> list:
            try:
                answer = await conn.request({"t": "diagnose"}, 5)
            except (ConnectionError, TimeoutError):
                return []
            held = (answer.get("gpio") or {}).get(conn.instance) if isinstance(answer.get("gpio"), dict) else None
            return held if isinstance(held, list) else []

        conns = list(self._instances.values())
        held = await asyncio.gather(*map(pins, conns))
        return {**reply, "gpio": {conn.instance: p for conn, p in zip(conns, held, strict=True) if p}}

    async def resume(self) -> None:
        """Start the paused instances again (the Dashboard's button)."""
        self.paused.clear()
        self._crashes.clear()
        logger.info("Modules started again after the pause")
        if self._control:
            await self._sync()

    async def control(self, instance_id: str, name: str, value) -> bool:
        """A command for something the instance exposes, checked by the caller (mqtt/bridge.py); False if it
        isn't running."""
        conn = self._instances.get(instance_id)
        if conn is None:
            return False
        await conn.send({"t": "control", "name": name, "value": value})
        return True

    def knows(self, instance_id: str) -> bool:
        return instance_id in self._instances and instance_id in self._placement

    # The connections

    def _post(self, message: dict) -> None:
        """Send to every instance without waiting, from any thread (an event can be published from one)."""
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: [asyncio.ensure_future(self._send_quietly(conn, message))
                                                     for conn in list(self._instances.values())])

    async def _send_quietly(self, conn: Connection, message: dict) -> None:
        try:
            await conn.send(message)
        except (ConnectionError, OSError, TypeError, ValueError) as e:
            logger.debug(f"Could not send {message.get('t')} to {conn.instance}: {e}")

    async def _request(self, message: dict, timeout: float) -> dict:
        """A request to the control process."""
        if self._control is None:
            raise ConnectionError("not connected to the module host")
        return await self._control.request(message, timeout)

    async def _connect(self) -> Connection:
        path = os.environ.get("MIRRORDASH_MODULE_SOCKET")
        if not path:  # not on a mirror: the host is this process's child, on a socket only this user can reach
            if self._process is None or self._process.returncode is not None:
                self._socket_dir = self._socket_dir or tempfile.mkdtemp(prefix="mirrordash-modules-")
                path = os.path.join(self._socket_dir, "host.sock")
                Path(path).unlink(missing_ok=True)
                self._process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "mirrordash_core.features.modules.host", path)
            path = os.path.join(self._socket_dir, "host.sock")
            for _ in range(300):
                if os.path.exists(path) or self._process.returncode is not None:
                    break
                await asyncio.sleep(0.1)
        reader, writer = await asyncio.open_unix_connection(path, limit=LINE_LIMIT)
        # The second connection, for the instances' sockets (host.py)
        fds = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            fds.connect(path + ".fds")
        except OSError:
            fds.close()
            writer.close()
            raise
        return Connection(reader, writer, fds)

    async def _run(self) -> None:
        while True:
            try:
                self._control = await self._connect()
            except OSError as e:
                logger.debug(f"Module host not there yet: {e}")
                await asyncio.sleep(2)
                continue
            logger.info("Connected to the module host")
            reading = asyncio.create_task(self._read(self._control))  # first: the sync waits on its answers
            try:
                await self._sync()
                await reading
            except (ConnectionError, OSError, TimeoutError) as e:
                logger.warning(f"The module host's connection ended: {e!r}")
            finally:
                reading.cancel()
                self._disconnected()
            logger.warning("The module host went away; reconnecting")
            await asyncio.sleep(1)

    async def _read(self, conn: Connection) -> None:
        try:
            while line := await conn.reader.readline():
                try:
                    await self._handle(json.loads(line), conn)
                except Exception as e:
                    logger.warning(f"Bad message from {conn.instance or 'the module host'}: {e!r}")
        except (ConnectionError, ValueError) as e:  # ValueError: a line over LINE_LIMIT
            logger.warning(f"The connection of {conn.instance or 'the module host'} ended: {e}")

    def _disconnected(self) -> None:
        for instance_id in list(self._instances):
            self._stop(instance_id)
        if self._control:
            self._control.close()
        self._control = None

    def _stop(self, instance_id: str) -> None:
        """It stops when its connection closes (nothing else may signal its process: another user)."""
        if conn := self._instances.pop(instance_id, None):
            conn.close()

    async def _sync(self) -> None:
        """Have the instances of the settings run: a new one or one whose settings changed (re)started, one
        that is gone or turned off stopped, the others left running, their cards sent again."""
        async with self._sync_lock:
            if self._control is None:
                return
            config = load_config()
            modules = {k: v for k, v in config.get("modules", {}).items() if isinstance(v, dict) and SAFE_NAME.match(k)}
            globals_cfg = config.get("globals", {})
            # order: the settings' order, which the screen keeps within an area (js/kiosk/core.js insertInOrder)
            eps = module_entry_points()
            self._placement = {instance_id: {**placement(cfg, instance_id), "order": i, "scripts": scripts_allowed(
                                   find_entry_point(cfg.get("module", instance_id), eps), config)}
                               for i, (instance_id, cfg) in enumerate(modules.items())}
            wanted = {i: cfg for i, cfg in modules.items() if cfg.get("enabled", True) and i not in self.paused}
            reply = await self._request({"t": "uids", "instances": list(wanted)}, 10)
            uids = reply.get("uids") if isinstance(reply.get("uids"), dict) else {}
            self._uids = {i: uid for i, uid in uids.items() if i in wanted and type(uid) is int and uid in MODULE_UIDS}
            await self._apply_lan(wanted)  # before they start, so their first call gets through
            for instance_id, conn in list(self._instances.items()):
                if instance_id not in wanted or conn.key != settings_key(wanted[instance_id], globals_cfg):
                    self._stop(instance_id)
            from mirrordash_core.features.power.display_power import (
                display_power_manager,
            )
            awake = display_power_manager.awake.is_set()
            for instance_id, cfg in wanted.items():
                if conn := self._instances.get(instance_id):
                    if conn.html is not None:  # for the screen that reloaded
                        await manager.broadcast({**self._placement[instance_id], "html": conn.html})
                elif instance_id in self._uids:
                    await self._start(instance_id, cfg, globals_cfg, awake)
                else:
                    logger.error(f"{instance_id}: no user left for a new module instance; not started")

    async def _start(self, instance_id: str, cfg: dict, globals_cfg: dict, awake: bool) -> None:
        """A process for the instance: one end of its two socket pairs to the host, which runs it as its user."""
        ours, theirs = socket.socketpair()
        fds_ours, fds_theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            socket.send_fds(self._control.fds, [json.dumps({"instance": instance_id}).encode()],
                            [theirs.fileno(), fds_theirs.fileno()])
        except OSError:
            ours.close()
            fds_ours.close()
            raise
        finally:
            theirs.close()
            fds_theirs.close()
        reader, writer = await asyncio.open_unix_connection(sock=ours, limit=LINE_LIMIT)
        conn = Connection(reader, writer, fds_ours, instance_id)
        conn.key = settings_key(cfg, globals_cfg)
        conn.module = str(cfg.get("module") or instance_id).replace("-", "_")
        self._instances[instance_id] = conn
        await conn.send({"t": "start", "gen": 1, "modules": {instance_id: cfg}, "globals": globals_cfg, "awake": awake})
        asyncio.create_task(self._serve(conn), name=f"module-{instance_id}")

    async def _serve(self, conn: Connection) -> None:
        """An instance's connection until it ends; then, unless the core stopped it, start it again or,
        after CRASH_LIMIT quick deaths in a row, pause it."""
        await self._read(conn)
        conn.close()
        instance_id = conn.instance
        if self._instances.get(instance_id) is not conn:
            return  # stopped: its settings changed, or the host went
        del self._instances[instance_id]
        crashes = self._crashes.get(instance_id, 0) + 1 if time.monotonic() - conn.started < CRASH_WINDOW else 1
        self._crashes[instance_id] = crashes
        if crashes >= CRASH_LIMIT:
            self.paused.add(instance_id)
            logger.error(f"{instance_id} stopped {crashes} times in a row soon after it started (it used up the modules' "
                         "memory, see 'oom-kill' in Logs → Raspberry Pi, or crashed): it stays off until the modules "
                         "are started again on the Dashboard")
            return
        logger.warning(f"{instance_id}'s process ended; starting it again in {RESTART_DELAY} s")
        await asyncio.sleep(RESTART_DELAY)
        try:
            await self._sync()
        except (ConnectionError, OSError, TimeoutError) as e:
            logger.warning(f"{instance_id} couldn't start again: {e!r}")

    async def _show(self, conn: Connection) -> None:
        """Send an instance's card to the screen, at most once in HTML_INTERVAL: the latest one, when its
        time comes (a module re-rendering in a loop would otherwise keep the screen redrawing)."""
        await asyncio.sleep(max(0.0, conn.shown + HTML_INTERVAL - time.monotonic()))
        conn.showing, conn.shown = None, time.monotonic()
        if self._instances.get(conn.instance) is conn and conn.instance in self._placement:
            await manager.broadcast({**self._placement[conn.instance], "html": conn.html})

    async def _handle(self, message: dict, conn: Connection) -> None:
        """One message from the host: input from outside, checked like any other. An instance's connection
        speaks for that instance alone, whatever its message names."""
        kind, instance_id = message.get("t"), conn.instance
        if kind == "reply":
            future = conn.pending.get(message.get("id"))
            if future and not future.done():
                future.set_result(message)
        elif kind == "log":
            now = time.monotonic()
            if len(conn.logged) == LOG_BURST and now - conn.logged[0] < LOG_WINDOW:
                conn.dropped += 1  # a flood would rotate the core's own lines out of its log, and wear the card
                return
            conn.logged.append(now)
            name = str(message.get("name", ""))
            if instance_id:
                name = f"mirrordash.modules.{instance_id}"
            elif not (LOG_NAME.match(name) and name.startswith("mirrordash.modules")):
                name = "mirrordash.modules.host"
            level = message.get("level") if isinstance(message.get("level"), int) else logging.INFO
            # Each further line (a traceback) carries the name too: a line of its own can't pass for the core's
            text = str(message.get("text", ""))[:4000].replace("\r", "").replace("\n", f"\n    {name}: ")
            if conn.dropped:
                logging.getLogger(name).warning(f"{conn.dropped} log lines left out: more than {LOG_BURST} in {LOG_WINDOW} s")
                conn.dropped = 0
            logging.getLogger(name).log(min(max(level, logging.DEBUG), logging.CRITICAL), text)
        elif instance_id is None or self._instances.get(instance_id) is not conn or instance_id not in self._placement:
            return  # the control process sends replies and logs only; a stopped instance nothing
        elif kind == "html":
            html = message.get("html")
            if not isinstance(html, str):
                return
            if len(html) > HTML_LIMIT:
                if conn.html != "":  # once, not for every card of a flood
                    logger.warning(f"{instance_id}: its card is over {HTML_LIMIT // 1024} kB, not shown")
                html = ""
            conn.html = html
            if conn.showing is None:
                conn.showing = asyncio.create_task(self._show(conn))
        elif kind == "publish":
            # A module's public event, to every instance (its own too) but never the core's own subscribers:
            # named after its module, so it can't pass for the hardware's or another module's
            name, data, now = message.get("name"), message.get("data"), time.monotonic()
            if len(conn.published) == EVENT_RATE and now - conn.published[0] < 1:
                return  # over EVENT_RATE a second: dropped without a log line, which a flood would flood too
            conn.published.append(now)
            if not (isinstance(name, str) and name.startswith(conn.module + ".") and EVENT_NAME.fullmatch(name)):
                logger.warning(f"{instance_id}: a public event's name must start with '{conn.module}.': {str(name)[:100]!r}")
            elif len(json.dumps(data)) > EVENT_LIMIT:
                logger.warning(f"{instance_id}: public event {name} is over {EVENT_LIMIT // 1024} kB, dropped")
            else:
                self._post({"t": "event", "name": name, "data": data})
        elif kind == "status":
            if isinstance(message.get("running"), list):
                conn.running = instance_id in message["running"]
        elif kind == "state":
            # Something it exposes (ARCHITECTURE #42): published only if the owner shares it and it is declared
            from mirrordash_core.features.mqtt.bridge import mqtt_bridge
            mqtt_bridge.module_state(instance_id, message.get("name"), message.get("value"))
        elif kind == "video_playing":
            await manager.set_video_playing(instance_id, bool(message.get("on")))
        elif kind == "video_window":
            request, hz = message.get("id"), message.get("hz")
            if isinstance(request, int) and request not in conn.windows and (
                    hz is None or (isinstance(hz, int) and 1 <= hz <= 240)):
                conn.windows[request] = asyncio.Event()  # here, not in the task: the end can come first
                asyncio.create_task(self._hold_window(conn, request, bool(message.get("fullscreen")), hz))
            elif isinstance(request, int):
                await self._send_quietly(conn, {"t": "reply", "id": request, "window": None})  # the module gets an error
        elif kind == "gpio":
            if isinstance(message.get("id"), int) and (error := self._gpio(message, conn)):
                logger.warning(f"{instance_id}: no GPIO {message.get('pins')}: {error}")
                await self._send_quietly(conn, {"t": "reply", "id": message["id"], "error": error})
        elif kind == "video_window_end":
            if message.get("id") in conn.windows:
                conn.windows[message["id"]].set()

    def _gpio(self, message: dict, conn: Connection) -> str | None:
        """An instance's GPIO request (features/hardware/gpio.py): its lines' file descriptor sent to its
        process over its own descriptor socket, or why not. Only free header pins, whatever it asks."""
        instance_id, pins = conn.instance, message.get("pins")
        options = {k: message[k] for k in ("output", "initial", "pull", "edge", "active_low", "debounce_ms") if k in message}
        if instance_id not in self._placement:
            return "not an instance of this mirror"
        if not isinstance(pins, list) or not pins or len(set(pins)) != len(pins) or any(type(p) is not int for p in pins):
            return "pins must be a list of GPIO numbers"
        if any(type(options.get(k, False)) is not bool for k in ("output", "active_low")) \
                or options.get("initial", 0) not in (0, 1) or type(options.get("debounce_ms", 0)) is not int:
            return "output and active_low are true or false, initial 0 or 1, debounce_ms a number"
        if taken := sorted(set(pins) - free_pins(load_config())):
            return f"GPIO {', '.join(map(str, taken))} isn't free for modules (not on the header, or a device in the Hardware tab uses it)"
        if not (chip := header_chip()):
            return "this machine has no GPIO header"
        try:
            fd = request_lines(chip, pins, f"mirrordash:{instance_id}", **options)
        except (OSError, ValueError) as e:
            return str(e)
        try:
            socket.send_fds(conn.fds, [struct.pack("<Q", message["id"])], [fd])
        except OSError as e:
            return f"couldn't hand the pins over: {e}"
        finally:
            os.close(fd)  # the instance's copy holds the lines now
        logger.info(f"{instance_id} has GPIO {', '.join(map(str, pins))}")
        return None

    async def _hold_window(self, conn: Connection, request: int, fullscreen: bool, hz: int | None) -> None:
        from mirrordash_core.features.video.window import video_window
        answered = False
        try:
            async with video_window(conn.instance, fullscreen=fullscreen, hz=hz) as window:
                await conn.send({"t": "reply", "id": request, "window": {
                    "app_id": window.app_id, "rect": window.rect, "fullscreen": window.fullscreen}})
                answered = True
                await conn.windows[request].wait()
        except Exception as e:
            logger.warning(f"{conn.instance}: video window failed: {e!r}")
            if not answered:
                await self._send_quietly(conn, {"t": "reply", "id": request, "window": None})
        finally:
            conn.windows.pop(request, None)

    # The home network

    async def _apply_lan(self, modules: dict) -> None:
        """Let each instance's user reach exactly its lan-url addresses (on a mirror; the sudo rule allows the
        helper, which sets the host's IPAddressAllow to all of them and nftables per user)."""
        if not os.environ.get("MIRRORDASH_MODULE_SOCKET") or not os.path.exists(LAN_HELPER):
            return
        async with self._lan_lock:  # a start and the refresh: the later one's addresses must win
            hosts = {i: names for i, names in lan_hosts(modules).items() if i in self._uids}
            found = await lan_addresses(set().union(*hosts.values()))
            allowed = sorted({(i, a) for i, names in hosts.items() for name in names for a in found.get(name, [])})[:LAN_LIMIT]
            pairs = [f"{self._uids[i]}={a}" for i, a in allowed]
            if pairs == self.lan:
                return
            process = await asyncio.create_subprocess_exec("sudo", "-n", LAN_HELPER, *pairs,
                                                           stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, err = await process.communicate()
            if process.returncode == 0:
                self.lan = pairs
                logger.info("Modules may reach on the home network: " + (", ".join(f"{i} {a}" for i, a in allowed) or "nothing"))
            else:
                logger.error(f"Could not set the modules' home-network addresses: {err.decode(errors='replace').strip()}")

    async def _refresh_lan(self) -> None:
        # ponytail: a lan-url host that moves to another address is found again within LAN_REFRESH seconds
        while True:
            await asyncio.sleep(LAN_REFRESH)
            try:
                modules = {k: v for k, v in load_config().get("modules", {}).items()
                           if isinstance(v, dict) and k not in self.paused}
                await self._apply_lan(modules)
            except Exception as e:
                logger.warning(f"Could not refresh the modules' home-network addresses: {e!r}")


module_host = ModuleHost()


async def module_routes(scope, receive, send) -> None:
    """Mounted at /api/modules: /api/modules/<instance>/... goes to that instance's `router` in its own
    process, and nowhere else, so a module can't add or shadow a route of the core. No login in front: the
    module checks its callers itself. Its answer can't set a cookie on the mirror's site, and runs as a
    sandboxed page in a browser, so HTML from a module can't act as the admin page."""
    if scope["type"] != "http":
        await WebSocketClose(code=1008)(scope, receive, send)  # ponytail: HTTP only, no websockets
        return
    instance_id, _, rest = get_route_path(scope).lstrip("/").partition("/")
    if not module_host.knows(instance_id):
        await Response('{"detail":"Not Found"}', 404, media_type="application/json")(scope, receive, send)
        return

    body, more = b"", True
    while more:
        event = await receive()
        if event["type"] == "http.disconnect":
            return
        body += event.get("body", b"")
        more = event.get("more_body", False)
        if len(body) > BODY_LIMIT:
            await Response("Request too large", 413)(scope, receive, send)
            return

    client = scope.get("client")
    try:
        reply = await module_host.call_route({
            "instance": instance_id, "method": scope["method"], "path": "/" + rest,
            "query": scope.get("query_string", b"").decode("latin-1"),
            "headers": [[k.decode("latin-1"), v.decode("latin-1")] for k, v in scope["headers"]
                        if k.decode("latin-1").lower() not in REQUEST_HEADERS_DROPPED],
            "body": base64.b64encode(body).decode(), "client": client[0] if client else None})
        status = reply.get("status")
        content = base64.b64decode(reply.get("body") or "")
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise ValueError(f"bad status {status!r}")
    except (ConnectionError, TimeoutError, ValueError, TypeError) as e:
        logger.warning(f"{instance_id}: {scope['method']} /{rest} got no answer from the module: {e!r}")
        await Response("The module didn't answer", 504 if isinstance(e, TimeoutError) else 502)(scope, receive, send)
        return

    response = Response(content, status)
    for item in reply.get("headers") or []:
        if (isinstance(item, list) and len(item) == 2 and str(item[0]).lower() not in RESPONSE_HEADERS_DROPPED
                and not any(c in str(item[0]) + str(item[1]) for c in "\r\n\0")):
            try:
                response.headers.append(str(item[0]), str(item[1]))
            except (ValueError, UnicodeEncodeError):
                pass
    response.headers["Content-Security-Policy"] = "sandbox"
    response.headers["X-Content-Type-Options"] = "nosniff"
    await response(scope, receive, send)
