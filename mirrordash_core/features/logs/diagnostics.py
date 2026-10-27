# SPDX-License-Identifier: AGPL-3.0-only
"""Diagnostics (Logs → Diagnostics): what helps find a fault without SSH, behind the admin login like the rest.

Nothing here needs more rights than the app has: the module host checks its own sandbox (host.py's
sandbox_checks), the rest is what systemctl, rauc and the logs tell any user. The download holds the
versions, these checks and the logs, never the settings (tokens, passwords).

ponytail: the module host answers for itself, so a module that took it over could report a closed sandbox;
the checks find a kernel or unit that doesn't filter, not a hostile module (systemd-analyze security in
the download is the outside view).
"""

import asyncio
from datetime import UTC, datetime

from mirrordash_core.config import get_core_version
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.updates.service import OS_BUILD_FILE, OS_VERSION_FILE

HOST_UNIT = "mirrordash-modules.service"
SHOW = ("ActiveEnterTimestamp", "NRestarts", "MemoryCurrent", "MemoryPeak", "MemoryMax", "IPAddressAllow")
RESOLVER = "127.0.0.53/32"  # always allowed: the modules' DNS, not the home network


async def run(*cmd: str, timeout: float = 20) -> str:
    """A command's output (stdout and stderr), or why there is none; never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
    except OSError as e:
        return f"({cmd[0]}: {e.strerror})"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"({cmd[0]}: took more than {timeout:.0f} s)"
    return out.decode(errors="replace").strip()


def megabytes(value: str | None) -> str | None:
    return f"{int(value) / 1048576:.0f} MB" if value and value.isdigit() else None


def core_memory() -> str | None:
    try:
        with open("/proc/self/status") as f:
            kb = next(line.split()[1] for line in f if line.startswith("VmRSS:"))
    except (OSError, StopIteration):
        return None
    return f"{int(kb) / 1024:.0f} MB"


async def host_state() -> dict:
    """The module host for the panel: its sandbox checks and GPIO (its own answer, checked as input) and
    what systemd knows of it."""
    show = await run("systemctl", "show", HOST_UNIT, "-p", ",".join(SHOW))
    props = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    state = {"core_memory": core_memory(), "since": props.get("ActiveEnterTimestamp") or None,
             "restarts": props.get("NRestarts"), "memory": megabytes(props.get("MemoryCurrent")),
             "peak": megabytes(props.get("MemoryPeak")), "limit": megabytes(props.get("MemoryMax")),
             "lan": [a for a in props.get("IPAddressAllow", "").split() if a != RESOLVER],
             "checks": [], "gpio": {}, "sandboxed": False, "error": None}
    try:
        reply = await module_host.diagnose()
    except (ConnectionError, TimeoutError) as e:
        state["error"] = f"The module host didn't answer ({e or 'no answer in 30 s'})."
        return state
    checks, gpio = reply.get("checks"), reply.get("gpio")
    if isinstance(checks, list):
        state["checks"] = [(str(c[0])[:200], c[1] is True) for c in checks if isinstance(c, list) and len(c) == 2]
    if isinstance(gpio, dict):
        state["gpio"] = {str(k)[:100]: [p for p in v if type(p) is int] for k, v in gpio.items() if isinstance(v, list)}
    state["sandboxed"] = reply.get("sandboxed") is True
    return state


def read(path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return "-"


async def report(app_log: str, module_log: str) -> str:
    """The diagnostics download: plain text, one section per heading."""
    state = await host_state()
    checks = "\n".join(f"{'OPEN  ' if reachable else 'closed'}  {what}" for what, reachable in state["checks"])
    host = "\n".join(f"{key}: {state[key]}" for key in ("since", "restarts", "memory", "peak", "limit", "lan", "gpio"))
    sections = {
        "Versions": f"MirrorDash {get_core_version()}\nOS build {read(OS_BUILD_FILE)} ({read(OS_VERSION_FILE)})\n"
                    + await run("uname", "-a"),
        "Module sandbox": state["error"] or (checks if state["sandboxed"] else "Not sandboxed (not on a mirror)\n" + checks),
        "Module host": host + f"\ncore memory: {state['core_memory']}",
        "Module host's sandbox (systemd-analyze security)": await run("systemd-analyze", "security", "--no-pager", HOST_UNIT),
        "OS slots (rauc status)": await run("rauc", "status"),
        "Failed services": await run("systemctl", "--failed", "--no-pager"),
        "Power (vcgencmd get_throttled)": await run("vcgencmd", "get_throttled"),
        "Memory and storage": await run("free", "-m") + "\n\n" + await run("df", "-h", "/", "/storage"),
        "MirrorDash log": app_log,
        "Modules' log": module_log,
        "System log, warnings since boot": await run("journalctl", "-b", "-p", "warning", "-n", "300", "--no-pager",
                                                     "--utc", "-o", "short-iso"),
    }
    head = f"MirrorDash diagnostics, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n"
    return head + "".join(f"\n=== {title} ===\n{body.strip()}\n" for title, body in sections.items())
