# SPDX-License-Identifier: AGPL-3.0-only
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

  core -> instance  start {gen, modules, globals, awake, hidden}  start it with these settings
                    awake {on}                              the screen went on or off
                    hidden {on}                             the screen hides its card (schedule, privacy mode)
                    event {name, data}                      a hardware event for the modules' event bus
                    http {id, instance, method, path, ...}  a call to /api/modules/<instance>/...
                    preview {id, instance, config, globals}  the card the admin's unsaved form would show (preview.py)
                    choices {id, instance, config, globals, kind, key, item, query}
                                                            a list or search setting's choices (choices.py)
                    diagnose {id}                           its GPIO lines (Logs → Diagnostics)
                    reply {id, window}                      the answer to video_window
                    control {name, value}                   a command for what it exposes, checked (on_control)
  instance -> core  html {gen, instance, html}              its card (the core places it)
                    status {gen, running}                   whether its run_loop runs
                    video_playing {instance, on}
                    video_window {id, instance, fullscreen, hz}, video_window_end {id}
                    gpio {id, instance, pins, output, ...}  lines for it (features/hardware/gpio.py)
                    log {name, level, text}
                    state {name, value}                     a value of what it exposes (expose, MQTT)
                    reply {id, ...}                         the answer to http or diagnose

A GPIO request's answer is a file descriptor, sent over the instance's descriptor socket as one message
holding the request's id. An instance stops when the core closes its connection. When the core's
connection to the control process ends (the core restarted, or was updated), the host exits and systemd
starts it again, so a new venv's modules load fresh.

The modules are installed in the module site (module_files.module_site), in the host's own folder: each module in
a folder of its own (pkgs/<name>_<n>), where uv runs in a process of its own as that module's user
(site_main, forkserver.PACKAGE_UIDS: one per module and where it comes from), and the site is a link per module to its folder, which only the control
process writes. So a package's build, the files it installs and its .pth reach no other module's folder or
process, and never run as the core or the forkserver.

This file is the control process (Control, serve) and the steps it runs as a module's user (site_main,
probe_main). The module host's other files:
  forkserver.py      makes every process, as the right user
  instance.py        one instance's process: the module itself (Host, HostLink)
  sandbox_checks.py  what a module could reach that it must not (the self-check)
  site_folders.py    the module site: a folder per module, links to them, site_a and site_b
  package_names.py   the name a module's folder and user go by
"""

import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

from mirrordash_core.config import get_module_dir
from mirrordash_core.features.modules import forkserver
from mirrordash_core.features.modules.instance import (
    LINE_LIMIT,
    HostLink,
    LogForward,
    instance_main,
    read_lines,
)
from mirrordash_core.features.modules.module_files import SAFE_NAME, folder_entry_points
from mirrordash_core.features.modules.package_names import package_key, package_user
from mirrordash_core.features.modules.sandbox_checks import data_folders, sandbox_checks
from mirrordash_core.features.modules.site_folders import (
    empty_folder,
    folder_holds_package,
    link_module,
    module_name_clash,
    next_module_folder_name,
    site_links,
    site_paths,
)
from mirrordash_core.venv import point_link

logger = logging.getLogger("mirrordash.core.modules.host")

CHECK_TIMEOUT = 180
UV_TIMEOUT = 600  # one uv pip run, unless the core asks for another
SITE_STEP_TIMEOUT = 200  # the other steps
PROBE_TIMEOUT = 60
# The check of a changed site (service.py update_modules): these modules must import from it
CHECK = ("import site, sys; site.addsitedir(sys.argv[1]); from importlib.metadata import entry_points; "
         "[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in sys.argv[2:]]")




async def run_command(*cmd: str, timeout: float) -> tuple[int, str]:
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




# The processes the forkserver makes (forkserver.run_forkserver's children), each an async function of its sockets




async def site_step(message: dict, folder: Path) -> tuple[int, str]:
    """One step in one module's own folder, as that module's user (Control._site chooses both): uv pip
    install into it, import its modules from it, or empty it."""
    op, python = message.get("op"), str(message.get("python") or "")
    if op == "pip":
        args = [str(a) for a in message.get("args") or []]
        timeout = message.get("timeout") if isinstance(message.get("timeout"), int) else UV_TIMEOUT
        if args[:1] != ["install"]:
            return 1, f"not a uv pip install: {args[:1]}"
        # --no-cache: the host's folder is on /storage, which is small. --no-config: no uv.toml from the
        # working directory or HOME (forkserver.spawn_child) decides where a module's code comes from
        code, err = await run_command("uv", "pip", "install", "--python", python, "--target", str(folder),
                              "--no-cache", "--no-config", *args[1:], timeout=timeout)
        if code == 0:  # a module's process can't write its folder, so its bytecode is made here
            await run_command(python, "-I", "-m", "compileall", "-q", str(folder), timeout=timeout)
        return code, err
    elif op == "check":
        names = [n for n in message.get("names", []) if isinstance(n, str) and SAFE_NAME.match(n)]
        return await run_command(python, "-I", "-c", CHECK, str(folder), *names, timeout=CHECK_TIMEOUT)
    elif op == "clean":
        await asyncio.to_thread(empty_folder, folder)
    else:
        return 1, f"unknown site step {op!r}"
    return 0, ""


async def site_main(stream: socket.socket, folder: str) -> None:
    """One site step in pkgs/folder, as that module's user: a line in, the answer out."""
    reader, writer = await asyncio.open_unix_connection(sock=stream, limit=LINE_LIMIT)
    try:
        code, err = await site_step(json.loads(await reader.readline()), get_module_dir() / "pkgs" / folder)
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


def module_uids(names: list[str], file_name: str = "uids.json", uid_range: range = forkserver.MODULE_UIDS) -> dict[str, int]:
    """Each instance's user (or each module's, by package_user, in package_uids.json), kept in the host's folder
    (/storage outlives the image) so its files stay its own. A new one gets the next uid, never one that was
    another's: that one's files may still be there.
    ponytail: forkserver.MODULE_UIDS holds 900 instances ever made on a mirror, PACKAGE_UIDS 98 modules ever
    installed; past that a new one doesn't start or install."""
    path = get_module_dir() / file_name
    try:
        uids = {name: uid for name, uid in json.loads(path.read_text()).items() if type(uid) is int and uid in uid_range}
    except (OSError, ValueError, AttributeError):
        uids = {}
    new_names = [name for name in names if name not in uids and SAFE_NAME.match(name)]
    for name in new_names:
        next_uid = max(uids.values(), default=uid_range.start - 1) + 1  # never one given before, even if freed
        if next_uid in uid_range:
            uids[name] = next_uid
    if new_names:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(file_name + ".tmp")
        tmp.write_text(json.dumps(uids, indent=1))
        os.replace(tmp, path)
    return {name: uids[name] for name in names if name in uids}


class Control:
    """The core's connection to the control process: the users, the site and the checks."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, spawn):
        self.reader = reader
        self.link = HostLink(writer)
        self.spawn = spawn  # forkserver.Spawner, or a stand-in that runs CHILDREN here (the tests)
        self.sandboxed = bool(os.environ.get("MIRRORDASH_SANDBOX"))
        # What the self-check found open, once it ran: while anything is, no module instance starts
        self.sandbox_holes: list[str] = []
        self._sandbox_checked = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()

    async def run(self) -> None:
        forward = LogForward(self.link, asyncio.get_running_loop())
        logging.getLogger().addHandler(forward)
        if os.environ.get("MIRRORDASH_NO_USERS"):  # said once the core reads the log
            logger.error("This OS image can't give each module a user of its own, so no module runs: "
                         "install the system update (Settings → Updates)")
            self._sandbox_checked.set()
        elif self.sandboxed:
            self._spawn(self._check())
        else:
            self._sandbox_checked.set()  # off a mirror there is no sandbox to check
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
            self._spawn(self._reply_uids(request, instances))
        elif kind == "site":
            self._spawn(self._site(message))
        elif kind == "diagnose":
            self._spawn(self._diagnose(request))

    async def _reply_uids(self, request, instances: list[str]) -> None:
        """Each instance's user, once the self-check ran, and what it found open: the core starts nothing then."""
        await self._sandbox_checked.wait()
        await self.link.send({"t": "reply", "id": request, "uids": module_uids(instances),
                              "sandbox_open": self.sandbox_holes})

    def start_instance(self, data: bytes, fds: list[int]) -> None:
        """The core's request to start an instance (receive_spawns): its sockets go to its process. Refused
        until the self-check ran, and while it found the sandbox open (fail closed)."""
        instance_sockets = [socket.socket(fileno=fd) for fd in fds]  # its messages, and the descriptors it is handed
        try:
            if not self._sandbox_checked.is_set() or self.sandbox_holes:
                raise ValueError("the module sandbox isn't known to be closed")
            instance = json.loads(data).get("instance")
            uid = module_uids([instance]).get(instance) if isinstance(instance, str) else None
            if uid is None or len(instance_sockets) != 2:
                raise ValueError(f"no user for {instance!r}")
        except (ValueError, AttributeError, OSError) as e:
            logger.error(f"A module instance couldn't start: {e}")
            for instance_socket in instance_sockets:
                instance_socket.close()
            return
        self.spawn("module", uid, instance, instance_sockets)

    async def run_in_child_process(self, kind: str, uid: int, message: dict | None, timeout: float, folder: str | None = None) -> dict:
        """Run a site step (in a module's folder) or the probe in a process of its own: the message in, its
        answer out."""
        ours, theirs = socket.socketpair()
        reader, writer = await asyncio.open_unix_connection(sock=ours, limit=LINE_LIMIT)
        try:
            self.spawn(kind, uid, folder, [theirs])
            if message is not None:
                writer.write(json.dumps(message).encode() + b"\n")
                await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), timeout) or b"{}")
        finally:
            writer.close()

    async def probe(self) -> list:
        """sandbox_checks as the self-check's user, told the data folders it can't list."""
        try:
            reply = await self.run_in_child_process("probe", forkserver.PROBE_UID, {"data": data_folders()}, PROBE_TIMEOUT)
            return reply.get("checks") or []
        except (OSError, ValueError, TimeoutError) as e:
            return [(f"the self-check, which didn't finish ({e!r})", True)]

    async def _check(self) -> None:
        self.sandbox_holes = [str(what) for what, reachable in await self.probe() if reachable]
        for what in self.sandbox_holes:
            logger.error(f"The module sandbox is open: modules can reach {what}")
        self._sandbox_checked.set()

    async def _diagnose(self, request) -> None:
        await self.link.send({"t": "reply", "id": request, "checks": await self.probe(), "sandboxed": self.sandboxed})

    async def _site(self, message: dict) -> None:
        """One step of a change to the installed modules (client.py's ModuleHost.packages), like the core's
        venv_swap: prepare the next site (the active one's links, or none when fresh), install a module into
        a new folder of its own or take one out, import modules from it, switch it in or drop it. The links
        are written here, what runs a module's code in a process as that module's user. The core runs one
        change at a time."""
        step = message.get("op")  # prepare, pip, check, then commit or revert
        link, active, next_site = site_paths()
        try:
            if step == "prepare":
                await asyncio.to_thread(empty_folder, next_site, True)
                for name, folder in ({} if message.get("fresh") else site_links(active)).items():
                    link_module(next_site, name, folder)
                await self._remove_unlinked_module_folders()
                code, err = 0, ""
            elif step == "pip":
                code, err = await self._install_or_uninstall(message, next_site)
            elif step == "check":
                code, err = await self._check_modules(message, next_site)
            elif step == "revert":
                await asyncio.to_thread(empty_folder, next_site, True)
                code, err = 0, ""
            elif step == "commit":
                point_link(link, next_site)
                code, err = 0, ""
            else:
                code, err = 1, f"unknown site step {step!r}"
        except (OSError, ValueError, TimeoutError) as e:
            code, err = 1, str(e) or repr(e)
        if code:
            logger.warning(f"Module site {step} failed: {err.strip()}")
        await self.link.send({"t": "reply", "id": message.get("id"), "code": code, "err": err})

    async def _site_step_as(self, uid: int, folder: str, message: dict) -> tuple[int, str]:
        """A site step in pkgs/folder, as uid (a module's user)."""
        timeout = message.get("timeout") if isinstance(message.get("timeout"), int) else SITE_STEP_TIMEOUT
        reply = await self.run_in_child_process("site", uid, message, timeout + 30, folder)
        return (reply.get("code") if isinstance(reply.get("code"), int) else 1), \
            str(reply.get("err", "the step ended without an answer"))

    def _owner_of_module_folder(self, folder: str) -> int | None:
        """The user a module's folder in pkgs belongs to (forkserver.give_folder_to_user gave it), whom its steps run
        as; None if it is no module's. Off a mirror it is the developer's, and any module user passes the
        forkserver, which then changes none."""
        uid = os.stat(get_module_dir() / "pkgs" / folder, follow_symlinks=False).st_uid
        if uid in forkserver.PACKAGE_UIDS:
            return uid
        return None if self.sandboxed else forkserver.PACKAGE_UIDS.start

    async def _install_or_uninstall(self, message: dict, next_site: Path) -> tuple[int, str]:
        """uv pip install of one module, into a new folder of its own, linked in only if it worked and
        claims no other module's name; or uninstall: its link taken out."""
        args = [str(a) for a in message.get("args") or []]
        target = args[-1] if len(args) > 1 else ""
        package = package_key(target) if target else None
        if args[:1] not in (["install"], ["uninstall"]) or not package:
            return 1, f"not one module to install or uninstall: {args}"
        links = site_links(next_site)
        if args[0] == "uninstall":
            removed = [link for link in links if folder_holds_package(next_site / link, package)]
            for link in removed:
                (next_site / link).unlink()
            return (0, "") if removed else (1, f"{target} is not installed")
        folder = next_module_folder_name(package)
        user = package_user(target)
        if (uid := module_uids([user], "package_uids.json", forkserver.PACKAGE_UIDS).get(user)) is None:
            return 1, "no user is left for another module on this mirror"
        code, err = await self._site_step_as(uid, folder, message)
        if code:
            return code, err
        if clash := module_name_clash(package, get_module_dir() / "pkgs" / folder, links):
            other_package, shared_names = clash
            return 1, f"{target} has a module named {', '.join(sorted(shared_names))}, as {other_package} already has"
        link_module(next_site, package, folder)
        return 0, ""

    async def _check_modules(self, message: dict, next_site: Path) -> tuple[int, str]:
        """The modules named import from the next site: each in its own folder, as its own user."""
        names = {str(name).replace("-", "_") for name in message.get("names") or []}
        for name, folder in site_links(next_site).items():
            if folder_entry_points(str(next_site / name)) & names:
                if (uid := self._owner_of_module_folder(folder)) is None:
                    return 1, f"{name}'s folder ({folder}) is no module user's"
                code, err = await self._site_step_as(uid, folder, {**message, "timeout": CHECK_TIMEOUT})
                if code:
                    return code, err
        return 0, ""

    async def _remove_unlinked_module_folders(self) -> None:
        """Remove the module folders neither site links to (an older version, an uninstalled module, a
        failed install), each emptied as its user. One that won't go stays until the next change."""
        linked_folders = {folder for site in site_paths()[1:] for folder in site_links(site).values()}
        entries = []
        with contextlib.suppress(OSError):
            # A module folder is named <package>_<number> (new_module_folder)
            entries = [entry for entry in os.scandir(get_module_dir() / "pkgs") if entry.name not in linked_folders
                       and SAFE_NAME.match(entry.name) and entry.name.rpartition("_")[2].isdigit()]
        for entry in entries:
            try:
                if (uid := self._owner_of_module_folder(entry.name)) is None:
                    raise OSError("it is no module user's")
                code, err = await self._site_step_as(uid, entry.name, {"op": "clean"})
                if code:
                    raise OSError(err)
                os.rmdir(entry.path)
            except (OSError, ValueError, TimeoutError) as e:
                logger.warning(f"An old module folder ({entry.name}) couldn't be removed: {e}")


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
            loop.call_soon_threadsafe(control.start_instance, data, fds)


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
    forkserver.run_forkserver(lambda sock: serve(path, forkserver.Spawner(sock)), CHILDREN)


if __name__ == "__main__":
    main()
