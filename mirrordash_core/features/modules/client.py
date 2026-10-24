# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The core's side of the module host (host.py, ARCHITECTURE #41).

On a mirror the host is its own service (mirrordash-modules.service, as the user mirrordash-mod) and
MIRRORDASH_MODULE_SOCKET says where it listens; elsewhere (a developer's machine, the SDK's `start`) the
core starts it as a child process. Either way the modules run there, and everything the host sends is
checked here like any other input from outside: it can only put HTML in the places of the instances the
config has, and its log lines land under mirrordash.modules.
"""

import asyncio
import base64
import ipaddress
import itertools
import json
import logging
import os
import re
import shutil
import socket
import sys
import tempfile
import urllib.parse
from pathlib import Path

from starlette.responses import Response
from starlette.routing import get_route_path
from starlette.websockets import WebSocketClose

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules.loader import SAFE_NAME

logger = logging.getLogger("mirrordash.core.modules.client")

LINE_LIMIT = 16 * 1024 * 1024  # as host.py
BODY_LIMIT = 1024 * 1024  # a call to a module's route
REQUEST_TIMEOUT = 35  # a little over the host's own
LAN_HELPER = "/usr/local/bin/mirrordash-module-network"  # an OS image file; sets the host's IPAddressAllow
LAN_REFRESH = 600  # seconds between looking up the lan-url hosts again (a DHCP address can change)
# Headers that never reach a module (the admin session, the admin password) or come back from one
# (a cookie on the mirror's own site, framing the core's answer)
REQUEST_HEADERS_DROPPED = {"cookie", "x-api-key", "host", "content-length"}
RESPONSE_HEADERS_DROPPED = {"set-cookie", "content-length", "transfer-encoding", "connection",
                            "content-security-policy", "x-content-type-options"}
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


def lan_hosts(modules: dict) -> set[str]:
    """The host names in the settings marked "format": "lan-url" of the enabled instances: what the owner
    typed (or kept) in the admin page, never a schema's default alone."""
    from mirrordash_core.features.settings.schema import _installed_module_schemas
    schemas = _installed_module_schemas()
    hosts = set()
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
                    hosts.add(host)
    return hosts


async def lan_addresses(hosts: set[str]) -> list[str]:
    """The home-network addresses those names have now (an IP as is, a name looked up, mDNS too)."""
    loop = asyncio.get_running_loop()
    addresses = set()
    for host in hosts:
        try:
            infos = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        except (OSError, TimeoutError, UnicodeError):
            logger.warning(f"Could not look up {host} for a module's home-network address")
            continue
        for *_, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
            if ip.is_private and not ip.is_loopback and not ip.is_unspecified:
                addresses.add(str(ip))
    return sorted(addresses)[:32]


class ModuleHost:
    """The connection to the module host. The rest of the core asks this what runs (`running`) and has
    it reload the modules after a settings change."""

    def __init__(self):
        self.running: list[str] = []
        self.lan: list[str] | None = None  # what the host may reach on the home network, once applied
        self._writer: asyncio.StreamWriter | None = None
        self._gen = 0
        self._placement: dict[str, dict] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._windows: dict[int, asyncio.Event] = {}
        self._ids = itertools.count(1)
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._process: asyncio.subprocess.Process | None = None
        self._socket_dir: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # The rest of the core

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
        """At shutdown: the host stops its modules when the connection ends."""
        event_bus.forward = None
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._writer:
            self._writer.close()
        if self._process and self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), 5)
            except TimeoutError:
                self._process.kill()
        if self._socket_dir:
            shutil.rmtree(self._socket_dir, ignore_errors=True)
            self._socket_dir = None

    async def reload_modules(self) -> None:
        """After a settings change: the screen reloads and the host starts the modules again."""
        manager.clear_cache()
        await manager.broadcast({"action": "reload"})
        if self._writer:
            try:
                await self._send_start()
            except (ConnectionError, OSError) as e:  # the next connection starts them with these settings
                logger.warning(f"The module host went away while the modules were reloaded: {e}")

    def set_awake(self, on: bool) -> None:
        """The screen went on or off (display_power.py): the modules' fetches sleep meanwhile."""
        self._post({"t": "awake", "on": bool(on)})

    def forward_event(self, name: str, data) -> None:
        """Every event published in the core (the hardware's) goes to the modules' event bus."""
        self._post({"t": "event", "name": name, "data": data})

    async def check_venv(self, python: str, names: list[str]) -> tuple[int, str]:
        """Do these modules import in that venv? Run by the host, so in its sandbox, never as the core."""
        try:
            reply = await self._request({"t": "check", "python": python, "names": names}, 200)
        except (ConnectionError, TimeoutError) as e:
            return 1, f"The modules aren't running, so the new version couldn't be checked ({e or 'no answer'})."
        return (reply.get("code") if isinstance(reply.get("code"), int) else 1), str(reply.get("err", ""))

    async def call_route(self, message: dict) -> dict:
        return await self._request({"t": "http", **message}, REQUEST_TIMEOUT)

    def knows(self, instance_id: str) -> bool:
        return self._writer is not None and instance_id in self._placement

    # The connection

    def _post(self, message: dict) -> None:
        """Send without waiting, from any thread (an event can be published from one)."""
        if self._writer and self._loop:
            self._loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._send_quietly(message)))

    async def _send_quietly(self, message: dict) -> None:
        try:
            await self._send(message)
        except (ConnectionError, OSError, TypeError, ValueError) as e:
            logger.debug(f"Could not send {message.get('t')} to the module host: {e}")

    async def _send(self, message: dict) -> None:
        writer = self._writer
        if writer is None:
            raise ConnectionError("not connected to the module host")
        line = json.dumps(message).encode() + b"\n"
        async with self._lock:
            writer.write(line)
            await writer.drain()

    async def _request(self, message: dict, timeout: float) -> dict:
        request = next(self._ids)
        future = self._pending[request] = asyncio.get_running_loop().create_future()
        try:
            await self._send({**message, "id": request})
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request, None)

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
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
        return await asyncio.open_unix_connection(path, limit=LINE_LIMIT)

    async def _run(self) -> None:
        while True:
            try:
                reader, self._writer = await self._connect()
            except OSError as e:
                logger.debug(f"Module host not there yet: {e}")
                await asyncio.sleep(2)
                continue
            logger.info("Connected to the module host")
            try:
                await self._send_start()
                while line := await reader.readline():
                    try:
                        await self._handle(json.loads(line))
                    except Exception as e:
                        logger.warning(f"Bad message from the module host: {e!r}")
            except (ConnectionError, ValueError) as e:  # ValueError: a line over LINE_LIMIT
                logger.warning(f"The module host's connection ended: {e}")
            finally:
                self._disconnected()
            logger.warning("The module host went away; reconnecting")
            await asyncio.sleep(1)

    def _disconnected(self) -> None:
        if self._writer:
            self._writer.close()
        self._writer = None
        self.running = []
        for event in self._windows.values():
            event.set()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("the module host went away"))

    async def _send_start(self) -> None:
        config = load_config()
        modules = {k: v for k, v in config.get("modules", {}).items() if isinstance(v, dict) and SAFE_NAME.match(k)}
        self._gen += 1
        self._placement = {instance_id: placement(cfg, instance_id) for instance_id, cfg in modules.items()}
        self.running = []
        await self._apply_lan(modules)  # before the modules start, so their first call gets through
        from mirrordash_core.features.power.display_power import display_power_manager
        await self._send({"t": "start", "gen": self._gen, "modules": modules, "globals": config.get("globals", {}),
                          "awake": display_power_manager.awake.is_set()})

    async def _handle(self, message: dict) -> None:
        """One message from the host: input from outside, checked like any other."""
        kind, instance_id = message.get("t"), message.get("instance")
        if kind == "html":
            if message.get("gen") == self._gen and instance_id in self._placement and isinstance(message.get("html"), str):
                await manager.broadcast({**self._placement[instance_id], "html": message["html"]})
        elif kind == "status":
            if message.get("gen") == self._gen and isinstance(message.get("running"), list):
                self.running = [i for i in message["running"] if i in self._placement]
        elif kind == "video_playing":
            if instance_id in self._placement:
                await manager.set_video_playing(instance_id, bool(message.get("on")))
        elif kind == "video_window":
            request, hz = message.get("id"), message.get("hz")
            if instance_id in self._placement and isinstance(request, int) and request not in self._windows and (
                    hz is None or (isinstance(hz, int) and 1 <= hz <= 240)):
                self._windows[request] = asyncio.Event()  # here, not in the task: the end can come first
                asyncio.create_task(self._hold_window(request, instance_id, bool(message.get("fullscreen")), hz))
            elif isinstance(request, int):
                await self._send_quietly({"t": "reply", "id": request, "window": None})  # the module gets an error
        elif kind == "video_window_end":
            if message.get("id") in self._windows:
                self._windows[message["id"]].set()
        elif kind == "reply":
            future = self._pending.get(message.get("id"))
            if future and not future.done():
                future.set_result(message)
        elif kind == "log":
            name = str(message.get("name", ""))
            name = name if LOG_NAME.match(name) and name.startswith("mirrordash.modules") else "mirrordash.modules.host"
            level = message.get("level") if isinstance(message.get("level"), int) else logging.INFO
            # Each further line (a traceback) carries the name too: a line of its own can't pass for the core's
            text = str(message.get("text", ""))[:4000].replace("\r", "").replace("\n", f"\n    {name}: ")
            logging.getLogger(name).log(min(max(level, logging.DEBUG), logging.CRITICAL), text)

    async def _hold_window(self, request: int, instance_id: str, fullscreen: bool, hz: int | None) -> None:
        from mirrordash_core.features.video.window import video_window
        answered = False
        try:
            async with video_window(instance_id, fullscreen=fullscreen, hz=hz) as window:
                await self._send({"t": "reply", "id": request, "window": {
                    "app_id": window.app_id, "rect": window.rect, "fullscreen": window.fullscreen}})
                answered = True
                await self._windows[request].wait()
        except Exception as e:
            logger.warning(f"{instance_id}: video window failed: {e!r}")
            if not answered:
                await self._send_quietly({"t": "reply", "id": request, "window": None})
        finally:
            self._windows.pop(request, None)

    # The home network

    async def _apply_lan(self, modules: dict) -> None:
        """Let the host reach exactly the lan-url addresses (on a mirror; the sudo rule allows the helper)."""
        if not os.environ.get("MIRRORDASH_MODULE_SOCKET") or not os.path.exists(LAN_HELPER):
            return
        addresses = await lan_addresses(lan_hosts(modules))
        if addresses == self.lan:
            return
        process = await asyncio.create_subprocess_exec("sudo", "-n", LAN_HELPER, *addresses,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await process.communicate()
        if process.returncode == 0:
            self.lan = addresses
            logger.info(f"Modules may reach on the home network: {', '.join(addresses) or 'nothing'}")
        else:
            logger.error(f"Could not set the modules' home-network addresses: {err.decode(errors='replace').strip()}")

    async def _refresh_lan(self) -> None:
        # ponytail: a lan-url host that moves to another address is found again within LAN_REFRESH seconds
        while True:
            await asyncio.sleep(LAN_REFRESH)
            try:
                modules = {k: v for k, v in load_config().get("modules", {}).items() if isinstance(v, dict)}
                await self._apply_lan(modules)
            except Exception as e:
                logger.warning(f"Could not refresh the modules' home-network addresses: {e!r}")


module_host = ModuleHost()


async def module_routes(scope, receive, send) -> None:
    """Mounted at /api/modules: /api/modules/<instance>/... goes to that instance's `router` in the module
    host, and nowhere else, so a module can't add or shadow a route of the core. No login in front: the
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
