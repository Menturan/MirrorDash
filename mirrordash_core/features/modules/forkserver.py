# SPDX-License-Identifier: AGPL-3.0-only
"""The module host's first process (ARCHITECTURE #41): it holds what it takes to give every module a user
of its own (the unit's ambient CAP_SETUID, CAP_SETGID and CAP_CHOWN) and does nothing but fork.

It runs one thread and imports only the core's code (host.py's imports: fastapi and the rest of the venv),
never the module site's: a package there could otherwise run with its capabilities. Its first fork is the
control process (host.py's serve: the core's connection, as the unit's user, capabilities cleared); the
control process asks it, over a SOCK_SEQPACKET pair, for the others, one message each with their sockets:

  {"kind": "module", "uid": 70100.., "instance": id}   one module instance (its stream and GPIO sockets)
  {"kind": "site", "uid": 70002.., "instance": folder} one step of a module install, in its folder (uv, its build)
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
import resource
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

# One per module and where it comes from (host.py package_user), for uv and what its build runs, in that
# module's own folders (pkgs/<name>_<n>) alone; kept in package_uids.json (host.py module_uids), never
# given to another module, nor to one of its name from elsewhere
PACKAGE_UIDS = range(70002, 70100)
PROBE_UID = 70001  # the self-check (host.py probe_main)
MODULE_UIDS = range(70100, 71000)  # one per instance, kept in uids.json (host.py module_uids), never reused
ALL_SANDBOX_UIDS = range(70000, 71000)  # all of them (mirrordash-module-network's nftables rules)
UIDS_ALLOWED_PER_CHILD_KIND = {"site": PACKAGE_UIDS, "probe": range(PROBE_UID, PROBE_UID + 1), "module": MODULE_UIDS}
STOP_GRACE_SECONDS = 5  # a module has this long to stop after its connection closed, before it is ended
# /proc/self/oom_score_adj, from -1000 (never killed) to 1000 (killed first): the kernel kills a module,
# not the forkserver or the control process
OOM_SCORE_ADJUSTMENT = "500"
# Processes and threads a module's user may have (RLIMIT_NPROC, counted per uid): a fork bomb stops there, not
# at the unit's memory limit with every module. uv's threads and builds need far fewer.
MAX_PROCESSES_PER_MODULE_USER = 256

# Linux's prctl(2) options and arguments (linux/prctl.h): Python has no wrapper for them
PR_SET_PDEATHSIG = 1  # the signal this process gets when its parent dies
PR_SET_DUMPABLE = 4  # 0: no core dumps, and other processes of the same user can't read its memory
PR_CAP_AMBIENT = 47  # the ambient capability set, which a process passes on to the programs it runs
PR_CAP_AMBIENT_CLEAR_ALL = 4  # PR_CAP_AMBIENT's argument: empty it
# capset(2)'s header version for 64-bit capability sets (linux/capability.h)
LINUX_CAPABILITY_VERSION_3 = 0x20080522
libc = ctypes.CDLL(None, use_errno=True)


def prctl(*args: int) -> None:
    """Linux's prctl(2) through libc, raising OSError when it fails."""
    if libc.prctl(*(ctypes.c_ulong(a) for a in args)) != 0:
        raise OSError(ctypes.get_errno(), f"prctl{args}")


def held_capability_sets() -> list[str]:
    """This process's capability sets that aren't empty (the bounding set aside: it only limits), as named in
    /proc/self/status: CapInh (inheritable), CapPrm (permitted), CapEff (effective), CapAmb (ambient)."""
    with open("/proc/self/status") as status:
        return [line.split(":")[0] for line in status
                if line.startswith(("CapInh", "CapPrm", "CapEff", "CapAmb")) and int(line.split()[1], 16)]


def drop_all_capabilities() -> None:
    """Empty every capability set of this process, and check that they are: a module that kept one could
    become root again."""
    prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)
    # capset(2) with all-zero data: effective, permitted and inheritable empty. Version 3 takes two
    # 32-bit words per set; struct __user_cap_header_struct is {version, pid}, pid 0 meaning this process
    capability_header = (ctypes.c_uint32 * 2)(LINUX_CAPABILITY_VERSION_3, 0)
    empty_capability_data = (ctypes.c_uint32 * 6)()
    if libc.capset(capability_header, empty_capability_data) != 0:
        raise OSError(ctypes.get_errno(), "capset")
    if still_held := held_capability_sets():
        raise OSError(f"capabilities still held: {still_held}")


def mode_readable_by_everyone(mode: int, is_folder: bool) -> int:
    """mode's permission bits with read for others, and execute too for a folder (to enter it) or a file
    its owner may run."""
    permissions = mode & 0o777
    is_executable = is_folder or bool(mode & stat.S_IXUSR)
    read_and_maybe_execute = stat.S_IROTH | stat.S_IXOTH if is_executable else stat.S_IROTH
    return permissions | read_and_maybe_execute


def give_entry_to_user(folder_fd: int | None, name: str, uid: int, make_readable_by_everyone: bool) -> None:
    """One entry to uid, by its file descriptor (a link swapped in meanwhile isn't followed), its group kept.
    make_readable_by_everyone: others may read it too (the site), set while the forkserver still owns it
    (after the chown it would need CAP_FOWNER), never setgid (RestrictSUIDSGID refuses it). A hard-linked
    file is left alone: it can be something else's."""
    try:
        entry_stat = os.stat(name, dir_fd=folder_fd, follow_symlinks=False)
        if entry_stat.st_uid == uid or stat.S_ISLNK(entry_stat.st_mode):
            return
        entry_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=folder_fd)
    except OSError:
        return
    try:
        # Looked at again through the descriptor: what was at name a moment ago may have been swapped
        entry_stat = os.fstat(entry_fd)
        is_folder = stat.S_ISDIR(entry_stat.st_mode)
        is_hard_linked_file = not is_folder and entry_stat.st_nlink > 1
        if entry_stat.st_uid == uid or is_hard_linked_file:
            return
        if make_readable_by_everyone and entry_stat.st_uid == os.getuid():
            os.fchmod(entry_fd, mode_readable_by_everyone(entry_stat.st_mode, is_folder))
        os.fchown(entry_fd, uid, -1)
    except OSError as e:
        print(f"forkserver: could not give {name} to {uid}: {e}", file=sys.stderr)
    finally:
        os.close(entry_fd)


def give_folder_to_user(path: str, uid: int | None, make_readable_by_everyone: bool = False) -> None:
    """path (made if missing) and all in it to uid, the folder itself last. A module's data folder stays in
    the group mirrordash-mod (setgid, from its parent), so the core (pi) reads and restores it through the
    group; what it restored is pi's, given back here before the module starts. make_readable_by_everyone
    (the site): only until the folder is uid's, as from then on uid writes all in it (the site's folders
    are big for a Pi). uid None (off a mirror): only made."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o770)
    if uid is None or (make_readable_by_everyone and os.lstat(path).st_uid == uid):
        return
    for _, folder_names, file_names, folder_fd in os.fwalk(path, follow_symlinks=False):
        for name in folder_names + file_names:
            give_entry_to_user(folder_fd, name, uid, make_readable_by_everyone)
    give_entry_to_user(None, path, uid, make_readable_by_everyone)


def switch_to_user(uid: int) -> None:
    """uid, a gid of the same number, and the unit's groups but the modules' folder's (i2c and spi stay).
    Real, effective and saved ids alike, so there is no way back."""
    modules_folder_group = os.getgid()
    os.setgroups([group for group in os.getgroups() if group != modules_folder_group])
    os.setresgid(uid, uid, uid)
    os.setresuid(uid, uid, uid)


def exit_when_connection_closes(connection: socket.socket) -> None:
    """In a thread: when the other end closes, give the process STOP_GRACE_SECONDS and end it. A module whose
    loop is stuck never reads the close, and nothing else may signal it (another uid)."""
    poll = select.poll()
    poll.register(connection.fileno(), select.POLLRDHUP)  # POLLRDHUP: the other end closed
    while not poll.poll():
        pass
    time.sleep(STOP_GRACE_SECONDS)
    os._exit(0)


def close_all_descriptors_except(keep_open: list[int]) -> None:
    """Close every file descriptor but those in keep_open: a fork inherits all of the forkserver's."""
    kept = sorted(set(keep_open))
    highest_possible = os.sysconf("SC_OPEN_MAX")
    for kept_fd, next_kept_fd in zip(kept, [*kept[1:], highest_possible], strict=True):
        os.closerange(kept_fd + 1, next_kept_fd)


def make_first_to_die_when_memory_runs_out(uid: int | None) -> None:
    """Raise this process's oom_score_adj, and cap its user's processes."""
    # Its own /proc files are root's while it isn't dumpable (the forkserver's setting, and a uid change's):
    # dumpable again, as its own user only it reaches them
    prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)
    with open("/proc/self/oom_score_adj", "w") as oom_score_adj:
        oom_score_adj.write(OOM_SCORE_ADJUSTMENT)
    if uid is not None:  # the count is the uid's: off a mirror the developer's, with all their processes
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES_PER_MODULE_USER, MAX_PROCESSES_PER_MODULE_USER))


def fork_child(main, sockets: list[socket.socket], uid: int | None, environment: dict, umask: int,
               first_to_die_when_memory_runs_out: bool = True) -> int:
    """Run main(*sockets) in a child as uid (None: this one), with only stdio and sockets open. The parent
    returns the pid; the child never returns."""
    parent_pid = os.getpid()
    child_pid = os.fork()
    if child_pid:
        return child_pid
    exit_code = 1
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # the forkserver ignores it; uv and its runs need it
        close_all_descriptors_except([0, 1, 2, *(s.fileno() for s in sockets)])
        if uid is not None:
            switch_to_user(uid)
        drop_all_capabilities()
        prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)  # after the uid change, which clears it
        if os.getppid() != parent_pid:  # the forkserver died before PR_SET_PDEATHSIG was set
            return
        if first_to_die_when_memory_runs_out:
            make_first_to_die_when_memory_runs_out(uid)
        os.environ.update(environment)
        os.umask(umask)
        sys.pycache_prefix, sys.dont_write_bytecode = None, True  # the unit's prefix is the forkserver's own
        threading.Thread(target=exit_when_connection_closes, args=(sockets[0],), daemon=True).start()
        asyncio.run(main(*sockets))
        exit_code = 0
    except BaseException:
        traceback.print_exc()
    finally:
        os._exit(exit_code)


def take_module_site_folders() -> None:
    """The site's two folders of links and the folder of the modules' own folders to this process's user
    (the control process writes the links, no module's user can), readable by all; and what an older site
    left in them, so the control process can empty them."""
    for name, mode in (("site_a", 0o755), ("site_b", 0o755), ("pkgs", 0o751)):
        path = os.path.join(get_module_dir(), name)
        if name == "pkgs":  # never what is in it: each module's folders are that module's user's
            os.makedirs(path, exist_ok=True)
        else:
            give_folder_to_user(path, os.getuid())
        os.chmod(path, mode)  # without the setgid bit: RestrictSUIDSGID refuses setting it, not dropping it


def check_spawn_request(request: dict, descriptors: list[int], children: dict) -> tuple[str, int, str | None]:
    """(kind, uid, instance) of a request from the control process, or ValueError if it asks for anything
    it may not: a kind there is no child for, a uid outside that kind's range, the wrong number of
    sockets, or an instance that isn't a safe folder name."""
    kind, uid, instance = request.get("kind"), request.get("uid"), request.get("instance")
    expected_socket_count = 2 if kind == "module" else 1
    if kind not in children:
        raise ValueError(f"refused, no such kind: {request}")
    if type(uid) is not int or uid not in UIDS_ALLOWED_PER_CHILD_KIND[kind]:
        raise ValueError(f"refused, not a uid for a {kind}: {request}")
    if len(descriptors) != expected_socket_count:
        raise ValueError(f"refused, {len(descriptors)} sockets: {request}")
    if kind != "probe" and not (isinstance(instance, str) and SAFE_NAME.match(instance)):
        raise ValueError(f"refused, not a safe name: {request}")
    return kind, uid, instance


def spawn_child(request: dict, descriptors: list[int], children: dict, sandboxed: bool) -> None:
    """One child the control process asked for, if the request is one it may ask."""
    kind, uid, instance = check_spawn_request(request, descriptors, children)
    modules_folder = str(get_module_dir())
    owner = uid if sandboxed else None
    if kind == "module":
        data_folder = os.path.join(modules_folder, "data", instance)
        cache_folder = os.path.join(modules_folder, "cache", instance)
        give_folder_to_user(data_folder, owner)
        give_folder_to_user(cache_folder, owner)
        environment, umask = {"HOME": data_folder, "TMPDIR": cache_folder}, 0o007  # pi reads the data through the group (backups)
    else:
        home = "/tmp"
        if kind == "site":  # its module's folder, which only it writes
            home = os.path.join(modules_folder, "pkgs", instance)
            give_folder_to_user(home, owner, make_readable_by_everyone=True)
        # The site's HOME is its module's folder: uv and git read their config from HOME, and the unit's /tmp
        # is every module's, so another module could plant one there (an index, a git insteadOf or hook).
        # Its TMPDIR too: uv unpacks there (babel alone is 30 MB), more than the unit's /tmp holds, and on
        # the same file system as the folder uv links instead of copying
        environment, umask = {"HOME": home, "TMPDIR": home}, 0o022  # the site: every module reads it
    sockets = [socket.socket(fileno=fd) for fd in descriptors]
    try:
        fork_child(lambda *s: children[kind](*s, instance), sockets, owner, environment, umask)
    finally:
        for s in sockets:
            s.close()


def run_forkserver(control, children: dict) -> None:
    """The forkserver: fork control (an async function of its socket to here), then children[kind] for each
    request, until control ends."""
    sandboxed = bool(os.environ.get("MIRRORDASH_SANDBOX"))
    if threading.active_count() != 1:
        sys.exit("forkserver: a thread runs, so forking isn't safe")
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)  # no /proc/<pid>/mem from the unit's user
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # children are reaped by the kernel
    if sandboxed and "CapEff" not in held_capability_sets():
        os.environ["MIRRORDASH_NO_USERS"] = "1"  # an OS image before this: the control process says so
    elif sandboxed:
        take_module_site_folders()  # before any module starts: it must read the site
    gc.freeze()  # what is loaded stays shared with every child
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    fork_child(control, [theirs], None, {}, 0o007, first_to_die_when_memory_runs_out=False)
    theirs.close()
    while True:
        try:
            data, descriptors, _, _ = socket.recv_fds(ours, 4096, 2)
        except InterruptedError:
            continue
        if not data:
            return  # the control process ended: the unit restarts
        try:
            spawn_child(json.loads(data), descriptors, children, sandboxed)
        except Exception as e:
            print(f"forkserver: {e}", file=sys.stderr)
            for fd in descriptors:
                with contextlib.suppress(OSError):
                    os.close(fd)


class Spawner:
    """The control process's way to the forkserver: spawn(kind, uid, instance, sockets) sends the request and
    the sockets, then closes them here (the child holds them)."""

    def __init__(self, forkserver_socket: socket.socket):
        self.forkserver_socket = forkserver_socket

    def __call__(self, kind: str, uid: int, instance: str | None, sockets: list[socket.socket]) -> None:
        try:
            message = json.dumps({"kind": kind, "uid": uid, "instance": instance}).encode()
            socket.send_fds(self.forkserver_socket, [message], [s.fileno() for s in sockets])
        finally:
            for s in sockets:
                s.close()


if __name__ == "__main__":
    # The unit's ExecStart, rather than host: an app from before this file can't start, so it never runs its
    # modules with the unit's capabilities
    from mirrordash_core.features.modules.host import main
    main()
