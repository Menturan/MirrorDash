# SPDX-License-Identifier: AGPL-3.0-only
"""The core's side of the module host (host.py, ARCHITECTURE #41).

On a mirror the host is its own service (mirrordash-modules.service) and MIRRORDASH_MODULE_SOCKET says
where it listens; elsewhere (a developer's machine, the SDK's `start`) the core starts it as a child
process. Either way the modules run there, each instance in its own process with a connection of its own
from here, and everything they send is checked here like any other input from outside: an instance can
only put HTML in its own place and ask GPIO for itself (its connection is who it is), and its log lines
land under mirrordash.modules.<instance>.

This file is ModuleHost: the connections, starting and stopping instances, and what they send. Beside it:
  connection.py      one connection: lines of JSON, replies matched to requests
  from_instances.py  an instance's log lines, public events and GPIO requests, checked
  api_proxy.py       /api/modules/<instance>/...: calls to a module's own routes, forwarded to its process
  lan.py             what each instance may reach on the home network (its lan-url settings)
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.modules import from_instances, lan
from mirrordash_core.features.modules.connection import LINE_LIMIT, Connection
from mirrordash_core.features.modules.defaults import with_defaults
from mirrordash_core.features.modules.forkserver import MODULE_UIDS
from mirrordash_core.features.modules.module_files import (
    SAFE_NAME,
    find_entry_point,
    module_entry_points,
    scripts_allowed,
)
from mirrordash_core.features.modules.settings_process import SettingsProcesses
from mirrordash_core.host import run_in_background, run_restart
from mirrordash_core.screen import manager
from mirrordash_core.venv import get_venv_paths

logger = logging.getLogger("mirrordash.core.modules.client")

REQUEST_TIMEOUT = 35  # a little over the host's own
HTML_LIMIT = 512 * 1024  # a card; a bigger one isn't shown (every open screen gets each card)
HTML_INTERVAL = 0.5  # seconds between a card's sends to the screen: one sent sooner waits, and only the last goes
# An instance's process ending this soon after its start, this many times in a row, pauses it: one that
# runs out of memory (the kernel kills it first, oom_score_adj) or crashes would start again forever
CRASH_WINDOW = 300
CRASH_LIMIT = 3
RESTART_DELAY = 5
# Its place on the screen: changing one moves the card without restarting the module
PLACEMENT_KEYS = {"position", "carousel_group", "carousel_interval", "max_width", "max_height", "z_index", "opacity",
                  "show_in_privacy", "schedule"}


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
        "show_in_privacy": module_cfg.get("show_in_privacy") is True,
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


class ModuleHost:
    """The connections to the module host. The rest of the core asks this what runs (`running`) and has
    it reload the modules after a settings change."""

    # Who hears what an instance says about something it exposes: (instance, name, value). app.py sets it to
    # the MQTT bridge, so the modules needn't know who listens.
    on_state = staticmethod(lambda instance, name, value: None)

    def __init__(self):
        self.lan: list[str] | None = None  # what the instances may reach on the home network, once applied
        # ponytail: in memory, so a restart of the core tries them again (CRASH_LIMIT more crashes)
        self.paused: set[str] = set()  # instances that kept dying soon after their start: not started (resume)
        # What the host's self-check found open (sandbox_checks.py): no module runs while there is any
        self.sandbox_open: list[str] = []
        self._crashes: dict[str, int] = {}
        self._control: Connection | None = None
        self._instances: dict[str, Connection] = {}
        self._hidden: set[str] = set()  # the instances whose cards the screen hides (privacy.hidden_instances)
        self._uids: dict[str, int] = {}
        self._placement: dict[str, dict] = {}
        self._sync_lock = asyncio.Lock()
        self._lan_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._process: asyncio.subprocess.Process | None = None
        self._socket_dir: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._settings = SettingsProcesses(self._open_instance, self._read)  # for modules none of whose instances runs

    # The rest of the core

    @property
    def running(self) -> list[str]:
        """The instances whose run_loop runs, in the settings' order (the screen lays an area's cards out so)."""
        return [instance_id for instance_id in self._placement
                if (conn := self._instances.get(instance_id)) and conn.running]

    async def start_modules(self) -> None:
        """At startup: connect to the host (and keep reconnecting) and start the modules there."""
        if os.environ.get("MIRRORDASH_BOOT_STATUS") == "safe_mode":
            # The core runs base_venv; the host would run the venv that failed, perhaps another version
            logger.warning("Safe mode: no modules")
            return
        self._loop = asyncio.get_running_loop()
        event_bus.forward = self.forward_event
        self._tasks = [asyncio.create_task(self._run(), name="module-host"),
                       run_in_background(self._refresh_lan(), name="module-host-lan")]

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
        self._settings.close_all()  # a reinstalled module's settings process would answer with its old code
        if self._control:
            try:
                await self._sync()
            except (ConnectionError, OSError, TimeoutError) as e:  # the next connection starts them with these settings
                logger.warning(f"The module host went away while the modules were reloaded: {e!r}")

    def set_awake(self, on: bool) -> None:
        """The screen went on or off (display_power.py): the modules' fetches sleep meanwhile."""
        self._post({"t": "awake", "on": bool(on)})

    async def set_hidden(self, hidden: set[str]) -> None:
        """Which instances' cards the screen hides (their schedule, privacy mode): each one that changed is told,
        and the module hears whether its card is shown (`on_shown`, with the screen being on)."""
        changed, self._hidden = hidden ^ self._hidden, set(hidden)
        for instance_id in sorted(changed):
            if conn := self._instances.get(instance_id):
                await self._send_quietly(conn, {"t": "hidden", "on": instance_id in hidden})

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

    async def preview(self, instance_id: str, module_cfg: dict) -> str | None:
        """The card the admin's unsaved settings would show (ARCHITECTURE #43), rendered in a process of the
        module (_connection_for), never shown on the screen. None when the module ships no sample answers."""
        conn = await self._connection_for(instance_id, module_cfg)
        reply = await conn.request({"t": "preview", "instance": conn.instance, "config": module_cfg,
                                    "globals": load_config().get("globals", {})}, REQUEST_TIMEOUT)
        return reply.get("html") if isinstance(reply.get("html"), str) else None

    async def choices(self, instance_id: str, module_cfg: dict, request: dict) -> dict:
        """The choices of a list or search setting of the admin's unsaved form (ARCHITECTURE #48), from a
        process of the module: {"choices": [...]} or {"error": text}. request: {kind, key, item, query}."""
        conn = await self._connection_for(instance_id, module_cfg)
        reply = await conn.request({"t": "choices", "instance": conn.instance, "config": module_cfg,
                                    "globals": load_config().get("globals", {}), **request}, REQUEST_TIMEOUT)
        return {key: reply[key] for key in ("choices", "error") if key in reply}

    async def _connection_for(self, instance_id: str, module_cfg: dict) -> Connection:
        """A process of the module to ask for the settings drawer: the instance's own, another instance's
        of the same module, or else the module's settings process (settings_process.py), so a module
        added a moment ago answers before it is saved. ConnectionError when the module host isn't there."""
        module = str(module_cfg.get("module") or instance_id).replace("-", "_")
        conn = self._instances.get(instance_id)
        if conn is None or conn.module != module:
            conn = next((c for c in self._instances.values() if c.module == module), None)
        if conn is None:
            if self._control is None:
                raise ConnectionError("the module host isn't connected")
            conn = await self._settings.get(module)
        return conn

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
        descriptor_socket = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            descriptor_socket.connect(path + ".fds")
        except OSError:
            descriptor_socket.close()
            writer.close()
            raise
        return Connection(reader, writer, descriptor_socket)

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
        self._settings.close_all()
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
            modules = {instance_id: instance_config for instance_id, instance_config in config.get("modules", {}).items()
                       if isinstance(instance_config, dict) and SAFE_NAME.match(instance_id)}
            globals_cfg = config.get("globals", {})
            # order: the settings' order, which the screen keeps within an area (js/kiosk/core.js insertInOrder)
            eps = module_entry_points()
            self._placement = {instance_id: {**placement(cfg, instance_id), "order": order, "scripts": scripts_allowed(
                                   find_entry_point(cfg.get("module", instance_id), eps), config)}
                               for order, (instance_id, cfg) in enumerate(modules.items())}
            # Not started: those paused after crashing, and those the screen's crash guard paused (saved)
            from mirrordash_core.features.modules import guard
            held = self.paused | guard.paused(config)
            from mirrordash_core.features.modules.schema import (
                _installed_module_schemas,  # as lan.py: no import cycle
            )
            schemas = _installed_module_schemas()
            wanted = {instance_id: with_defaults(cfg, schemas.get(str(cfg.get("module", instance_id)).replace("-", "_")) or {})
                      for instance_id, cfg in modules.items() if cfg.get("enabled", True) and instance_id not in held}
            # The host answers once its self-check ran (host.PROBE_TIMEOUT at most)
            reply = await self._request({"t": "uids", "instances": list(wanted)}, 90)
            holes = reply.get("sandbox_open")
            self.sandbox_open = [str(hole) for hole in holes] if isinstance(holes, list) else []
            if self.sandbox_open:
                logger.error("No module runs while the module sandbox is open (see the errors above): "
                             "install the system update, or go back to the system before it")
                wanted = {}  # fail closed: the running ones stop, the home network closes
            uids = reply.get("uids") if isinstance(reply.get("uids"), dict) else {}
            self._uids = {instance_id: uid for instance_id, uid in uids.items()
                          if instance_id in wanted and type(uid) is int and uid in MODULE_UIDS}
            await self._apply_lan(wanted)  # before they start, so their first call gets through
            for instance_id, conn in list(self._instances.items()):
                if instance_id not in wanted or conn.key != settings_key(wanted[instance_id], globals_cfg):
                    self._stop(instance_id)
            from mirrordash_core.features.power.display_power import (
                display_power_manager,
            )
            awake = display_power_manager.awake.is_set()
            from mirrordash_core.features.power.privacy import hidden_instances
            await self.set_hidden(hidden_instances(config))  # a saved schedule or "Shown in Privacy Mode"
            for instance_id, cfg in wanted.items():
                if conn := self._instances.get(instance_id):
                    if conn.html is not None:  # for the screen that reloaded
                        await manager.broadcast({**self._placement[instance_id], "html": conn.html})
                elif instance_id in self._uids:
                    await self._start(instance_id, cfg, globals_cfg, awake)
                else:
                    logger.error(f"{instance_id}: no user left for a new module instance; not started")

    async def _start(self, instance_id: str, cfg: dict, globals_cfg: dict, awake: bool) -> None:
        """A process for the instance, started with its settings."""
        conn = await self._open_instance(instance_id)
        conn.key = settings_key(cfg, globals_cfg)
        conn.module = str(cfg.get("module") or instance_id).replace("-", "_")
        self._instances[instance_id] = conn
        # Served first: if the start can't be sent, its connection ends there, and it is started again like a crash
        run_in_background(self._serve(conn), name=f"module-{instance_id}")
        await conn.send({"t": "start", "gen": 1, "modules": {instance_id: cfg}, "globals": globals_cfg, "awake": awake,
                         "hidden": instance_id in self._hidden})

    async def _open_instance(self, instance_id: str) -> Connection:
        """A process named instance_id: one end of its two socket pairs to the host, which runs it as its user."""
        messages_ours, messages_theirs = socket.socketpair()
        descriptors_ours, descriptors_theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            socket.send_fds(self._control.descriptor_socket, [json.dumps({"instance": instance_id}).encode()],
                            [messages_theirs.fileno(), descriptors_theirs.fileno()])
        except OSError:
            messages_ours.close()
            descriptors_ours.close()
            raise
        finally:
            messages_theirs.close()
            descriptors_theirs.close()
        reader, writer = await asyncio.open_unix_connection(sock=messages_ours, limit=LINE_LIMIT)
        return Connection(reader, writer, descriptors_ours, instance_id)

    async def _serve(self, conn: Connection) -> None:
        """An instance's connection until it ends; then, unless the core stopped it, start it again or,
        after CRASH_LIMIT quick deaths in a row, pause it."""
        await self._read(conn)
        conn.close()
        instance_id = conn.instance
        # A process that died (or was stopped) inside video_is_playing() never says it stopped: the other
        # modules would update once a minute until the next restart
        await manager.set_video_playing(instance_id, False)
        if self._instances.get(instance_id) is not conn:
            return  # stopped: its settings changed, or the host went
        del self._instances[instance_id]
        crashes = self._crashes.get(instance_id, 0) + 1 if time.monotonic() - conn.started < CRASH_WINDOW else 1
        self._crashes[instance_id] = crashes
        if crashes >= CRASH_LIMIT:
            from mirrordash_core.features.modules import trial
            # Just updated: the version before goes back instead (trial.py), and the app starts over with it
            if (name := trial.site_name_of(instance_id, load_config())) and await trial.put_back(name):
                run_in_background(run_restart())
                return
            self.paused.add(instance_id)
            from mirrordash_core import notifications
            from mirrordash_core.features.modules.service import (
                instance_title,  # here: service imports this file
            )
            instance_config = load_config().get("modules", {}).get(instance_id)
            # The name the admin page shows (the owner's title, else "Clock-2"), never the bare id
            name = instance_title(instance_id, instance_config if isinstance(instance_config, dict) else {})
            notifications.add("error", f"{name} kept crashing and was stopped. Start it again on the Dashboard; "
                              "Logs → Modules tells why.")
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
            from_instances.log_line(message, conn)
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
            if event := from_instances.public_event(message, conn):
                self._post(event)
        elif kind == "status":
            if isinstance(message.get("running"), list):
                conn.running = instance_id in message["running"]
        elif kind == "state":
            # Something it exposes (ARCHITECTURE #42): published only if the owner shares it and it is declared
            self.on_state(instance_id, message.get("name"), message.get("value"))
        elif kind == "video_playing":
            await manager.set_video_playing(instance_id, bool(message.get("on")))
        elif kind == "video_window":
            request, hz = message.get("id"), message.get("hz")
            if isinstance(request, int) and request not in conn.windows and (
                    hz is None or (isinstance(hz, int) and 1 <= hz <= 240)):
                conn.windows[request] = asyncio.Event()  # here, not in the task: the end can come first
                run_in_background(self._hold_window(conn, request, bool(message.get("fullscreen")), hz))
            elif isinstance(request, int):
                await self._send_quietly(conn, {"t": "reply", "id": request, "window": None})  # the module gets an error
        elif kind == "gpio":
            if isinstance(message.get("id"), int) and (error := from_instances.hand_over_gpio_lines(message, conn)):
                logger.warning(f"{instance_id}: no GPIO {message.get('pins')}: {error}")
                await self._send_quietly(conn, {"t": "reply", "id": message["id"], "error": error})
        elif kind == "video_window_end" and message.get("id") in conn.windows:
            conn.windows[message["id"]].set()

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
        """Let each instance's user reach exactly its lan-url addresses (lan.py)."""
        async with self._lan_lock:  # a start and the refresh: the later one's addresses must win
            self.lan = await lan.apply(modules, self._uids, self.lan)

    async def _refresh_lan(self) -> None:
        # ponytail: a lan-url host that moves to another address is found again within lan.LAN_REFRESH seconds
        while True:
            await asyncio.sleep(lan.LAN_REFRESH)
            try:
                modules = {k: v for k, v in load_config().get("modules", {}).items()
                           if isinstance(v, dict) and k not in self.paused}
                await self._apply_lan(modules)
            except Exception as e:
                logger.warning(f"Could not refresh the modules' home-network addresses: {e!r}")


module_host = ModuleHost()
