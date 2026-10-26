import asyncio
import contextlib
import os
import threading
from pathlib import Path

from fastapi import APIRouter


def attempt(fn):
    try:
        return fn()
    except OSError as e:
        return f"{type(e).__name__}"


def fill(folder):
    """Write 20 MB into a RAM folder (the unit's are 16 MB), then remove it: OSError when the cap holds."""
    path = f"{folder}/fill-{os.getuid()}"
    try:
        with open(path, "wb") as f:
            for _ in range(20):
                f.write(b"x" * 1048576)
        return "WROTE 20 MB"
    except OSError as e:
        return type(e).__name__
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def threads_started(n):
    """How many of n threads start (each waits until all tried): RLIMIT_NPROC stops a fork bomb."""
    done, started = threading.Event(), 0
    for _ in range(n):
        try:
            threading.Thread(target=done.wait, daemon=True).start()
        except RuntimeError:
            break
        started += 1
    done.set()
    return started


class Module:
    def __init__(self, config):
        self.config = config
        self.router = APIRouter()
        data, other = config["data_dir"], config["data_dir"].rsplit("/", 1)[0] + "/" + config.get("other", "x")

        @self.router.get("/who")
        async def who():
            status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("Cap"))
            site = "/storage/mirrordash/modules/site"
            return {
                "pid": os.getpid(), "ppid": os.getppid(), "uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups(),
                "caps": {k: v.strip() for k, v in status.items()},
                "setuid0": attempt(lambda: (os.setuid(0), "BECAME ROOT")[1]),
                "write_own": attempt(lambda: (open(data + "/mine", "w").write("x"), "ok")[1]),
                "append_restored": attempt(lambda: (open(data + "/restored", "a").write("x"), "ok")[1])
                if os.path.exists(data + "/restored") else "-",
                "read_other": attempt(lambda: open(other + "/secret").read()),
                "list_other": attempt(lambda: os.listdir(other)),
                "list_modules": attempt(lambda: os.listdir("/storage/mirrordash/modules")),
                "write_site": attempt(lambda: (open(site + "/evil.py", "w").write("x"), "WROTE")[1]),
                "write_code": attempt(lambda: (open(__file__, "a").write(""), "WROTE")[1]),  # its own module's
                "threads": threads_started(300),
                "read_uids": attempt(lambda: open("/storage/mirrordash/modules/uids.json").read()),
                "parent_mem": attempt(lambda: (open(f"/proc/{os.getppid()}/mem", "rb").close(), "OPENED")[1]),
                "fill_tmp": fill("/tmp"), "fill_shm": fill("/dev/shm"),
                "home": os.environ.get("HOME"), "tmp": os.environ.get("TMPDIR"), "umask": oct(os.umask(0o007)),
                "oom": open("/proc/self/oom_score_adj").read().strip(),
                "fds": sorted(os.listdir("/proc/self/fd")),
            }

    async def run_loop(self, broadcast):
        await broadcast("x", f"<p>{os.getpid()}</p>")
        held = []
        while self.config.get("leak"):
            held.append(bytearray(20 * 1024 * 1024))
            await asyncio.sleep(0.05)
        await asyncio.Event().wait()
