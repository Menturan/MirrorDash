# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The module host's first process (ARCHITECTURE #41): it holds what it takes to give every module a user
of its own (the unit's ambient CAP_SETUID, CAP_SETGID and CAP_CHOWN) and does nothing but fork.

It runs one thread and imports only the core's code (host.py's imports: fastapi and the rest of the venv),
never the module site's: a package there could otherwise run with its capabilities. Its first fork is the
control process (host.py's serve: the core's connection, as the unit's user, capabilities cleared); the
control process asks it, over a SOCK_SEQPACKET pair, for the others, one message each with their sockets:

  {"kind": "module", "uid": 70100.., "instance": id}   one module instance (its stream and GPIO sockets)
  {"kind": "site", "uid": SITE_UID}                    one step of a module install (uv, its builds)
  {"kind": "probe", "uid": PROBE_UID}                  the sandbox's self-check

Each child gets its own uid, no group of the modules' folder, no capabilities (cleared by hand: changing
from one non-root uid to another keeps them, and a module could become root again), dies with the
forkserver, is the first the kernel kills when memory runs out, and keeps no descriptor but its own.
Off a mirror (MIRRORDASH_SANDBOX unset) the same processes run as the developer's user.
"""

import asyncio
import contextlib
import ctypes
import gc
import json
import os
import select
import signal
import socket
import stat
import sys
import threading
import time
import traceback

from mirrordash_core.config import get_module_dir
from mirrordash_core.features.modules.loader import SAFE_NAME

SITE_UID = 70000  # uv and what a package's build runs
PROBE_UID = 70001  # the self-check (host.py probe_main)
MODULE_UIDS = range(70100, 71000)  # one per instance, kept in uids.json (host.py module_uids), never reused
UIDS = range(70000, 71000)  # all of them (mirrordash-module-network's nftables rules)
STOP_GRACE = 5  # seconds a module has to stop after its connection closed, before it is ended
OOM_SCORE_ADJ = "500"  # the kernel kills a module, not the forkserver or the control process

PR_SET_PDEATHSIG, PR_SET_DUMPABLE, PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL = 1, 4, 47, 4
CAP_VERSION_3 = 0x20080522
libc = ctypes.CDLL(None, use_errno=True)


def prctl(*args: int) -> None:
    if libc.prctl(*(ctypes.c_ulong(a) for a in args)) != 0:
        raise OSError(ctypes.get_errno(), f"prctl{args}")


def capabilities() -> list[str]:
    """This process's capability sets that aren't empty (the bounding set aside: it only limits)."""
    with open("/proc/self/status") as f:
        return [line.split(":")[0] for line in f
                if line.startswith(("CapInh", "CapPrm", "CapEff", "CapAmb")) and int(line.split()[1], 16)]


def clear_capabilities() -> None:
    prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)
    header, data = (ctypes.c_uint32 * 2)(CAP_VERSION_3, 0), (ctypes.c_uint32 * 6)()
    if libc.capset(header, data) != 0:
        raise OSError(ctypes.get_errno(), "capset")
    if held := capabilities():
        raise OSError(f"capabilities still held: {held}")


def give(dir_fd: int | None, name: str, uid: int, readable: bool) -> None:
    """One entry to uid, by its file descriptor (a link swapped in meanwhile isn't followed), its group kept.
    readable: others may read it too (the site), set while the forkserver still owns it (after the chown it
    would need CAP_FOWNER), never setgid (RestrictSUIDSGID refuses it). A hard-linked file is left alone:
    it can be something else's."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if st.st_uid == uid or stat.S_ISLNK(st.st_mode):
            return
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dir_fd)
    except OSError:
        return
    try:
        st = os.fstat(fd)
        folder = stat.S_ISDIR(st.st_mode)
        if st.st_uid == uid or (not folder and st.st_nlink > 1):
            return
        if readable and st.st_uid == os.getuid():
            os.fchmod(fd, (st.st_mode & 0o777) | (0o5 if folder or st.st_mode & 0o100 else 0o4))
        os.fchown(fd, uid, -1)
    except OSError as e:
        print(f"forkserver: could not give {name} to {uid}: {e}", file=sys.stderr)
    finally:
        os.close(fd)


def own_folder(path: str, uid: int | None, readable: bool = False) -> None:
    """path (made if missing) and all in it to uid, the folder itself last. A module's data folder stays in
    the group mirrordash-mod (setgid, from its parent), so the core (pi) reads and restores it through the
    group; what it restored is pi's, given back here before the module starts. readable (the site): only
    until the folder is uid's, as from then on uid writes all in it (the site's folders are big for a Pi).
    uid None (off a mirror): only made."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o770)
    if uid is None or (readable and os.lstat(path).st_uid == uid):
        return
    for _, dirs, files, root_fd in os.fwalk(path, follow_symlinks=False):
        for name in dirs + files:
            give(root_fd, name, uid, readable)
    give(None, path, uid, readable)


def become(uid: int) -> None:
    """uid, a gid of the same number, and the unit's groups but the modules' folder's (i2c and spi stay)."""
    os.setgroups([g for g in os.getgroups() if g != os.getgid()])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)


def watch(sock: socket.socket) -> None:
    """In a thread: when the other end closes, give the process STOP_GRACE seconds and end it. A module whose
    loop is stuck never reads the close, and nothing else may signal it (another uid)."""
    poll = select.poll()
    poll.register(sock.fileno(), select.POLLRDHUP)
    while not poll.poll():
        pass
    time.sleep(STOP_GRACE)
    os._exit(0)


def fork(main, socks: list[socket.socket], uid: int | None, env: dict, umask: int, oom: bool = True) -> int:
    """Run main(*socks) in a child as uid (None: this one), with only stdio and socks open; oom: the first
    the kernel kills. The parent returns the pid; the child never returns."""
    parent = os.getpid()
    pid = os.fork()
    if pid:
        return pid
    code = 1
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # the forkserver ignores it; uv and its runs need it
        keep = sorted({0, 1, 2, *(s.fileno() for s in socks)})
        for low, high in zip(keep, [*keep[1:], os.sysconf("SC_OPEN_MAX")], strict=True):
            os.closerange(low + 1, high)
        if uid is not None:
            become(uid)
        clear_capabilities()
        prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)  # after the uid change, which clears it
        if os.getppid() != parent:
            return
        if oom:
            # Its own /proc files are root's while it isn't dumpable (the forkserver's setting, and a uid change's):
            # dumpable again, as its own user only it reaches them
            prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write(OOM_SCORE_ADJ)
        os.environ.update(env)
        os.umask(umask)
        sys.pycache_prefix, sys.dont_write_bytecode = None, True  # the unit's prefix is the forkserver's own
        threading.Thread(target=watch, args=(socks[0],), daemon=True).start()
        asyncio.run(main(*socks))
        code = 0
    except BaseException:
        traceback.print_exc()
    finally:
        os._exit(code)


def own_site(owner: int | None) -> None:
    """Both site folders to the site's user, readable by the modules' users (a site the host's user wrote
    before, the first time)."""
    for name in ("site_a", "site_b"):
        own_folder(os.path.join(get_module_dir(), name), owner and SITE_UID, readable=True)


def spawn(request: dict, fds: list[int], children: dict, sandboxed: bool) -> None:
    """One child the control process asked for, if the request is one it may ask."""
    kind, uid, instance = request.get("kind"), request.get("uid"), request.get("instance")
    uids = {"site": range(SITE_UID, SITE_UID + 1), "probe": range(PROBE_UID, PROBE_UID + 1), "module": MODULE_UIDS}
    if kind not in children or type(uid) is not int or uid not in uids[kind] or len(fds) != (2 if kind == "module" else 1) \
            or (kind == "module" and not (isinstance(instance, str) and SAFE_NAME.match(instance))):
        raise ValueError(f"refused: {request}")
    root = str(get_module_dir())
    owner = uid if sandboxed else None
    if kind == "module":
        data, cache = os.path.join(root, "data", instance), os.path.join(root, "cache", instance)
        own_folder(data, owner)
        own_folder(cache, owner)
        env, umask = {"HOME": data, "TMPDIR": cache}, 0o007  # pi reads the data through the group (backups)
    else:
        if kind == "site":
            own_site(owner)
        env, umask = {"HOME": "/tmp", "TMPDIR": "/tmp"}, 0o022  # the site: every module reads it
    socks = [socket.socket(fileno=fd) for fd in fds]
    try:
        fork(lambda *s: children[kind](*s, instance), socks, owner, env, umask)
    finally:
        for s in socks:
            s.close()


def run(control, children: dict) -> None:
    """The forkserver: fork control (an async function of its socket to here), then children[kind] for each
    request, until control ends."""
    sandboxed = bool(os.environ.get("MIRRORDASH_SANDBOX"))
    if threading.active_count() != 1:
        sys.exit("forkserver: a thread runs, so forking isn't safe")
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)  # no /proc/<pid>/mem from the unit's user
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # children are reaped by the kernel
    if sandboxed and "CapEff" not in capabilities():
        os.environ["MIRRORDASH_NO_USERS"] = "1"  # an OS image before this: the control process says so
    elif sandboxed:
        own_site(SITE_UID)  # before any module starts: it must read the site
    gc.freeze()  # what is loaded stays shared with every child
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fork(control, [theirs], None, {}, 0o007, oom=False)
    theirs.close()
    while True:
        try:
            data, fds, _, _ = socket.recv_fds(ours, 4096, 2)
        except InterruptedError:
            continue
        if not data:
            return  # the control process ended: the unit restarts
        try:
            spawn(json.loads(data), fds, children, sandboxed)
        except Exception as e:
            print(f"forkserver: {e}", file=sys.stderr)
            for fd in fds:
                with contextlib.suppress(OSError):
                    os.close(fd)


class Spawner:
    """The control process's way to the forkserver: spawn(kind, uid, instance, socks) sends the request and
    the sockets, then closes them here (the child holds them)."""

    def __init__(self, sock: socket.socket):
        self.sock = sock

    def __call__(self, kind: str, uid: int, instance: str | None, socks: list[socket.socket]) -> None:
        try:
            message = json.dumps({"kind": kind, "uid": uid, "instance": instance}).encode()
            socket.send_fds(self.sock, [message], [s.fileno() for s in socks])
        finally:
            for s in socks:
                s.close()
