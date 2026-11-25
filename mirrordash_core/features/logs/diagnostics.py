# SPDX-License-Identifier: AGPL-3.0-only
"""Diagnostics (Logs → Diagnostics): what helps find a fault without SSH, behind the admin login like the rest.

Nothing here needs more rights than the app has: the module host checks its own sandbox (host.py's
sandbox_checks), the rest is what systemctl, rauc and the logs tell any user. The download holds the
versions, the installed modules, the screen, the connection type and signal, these checks, the saved logs
and the logs: never the settings (tokens, passwords), no Wi-Fi name (SSID) and no monitor serial.

ponytail: the module host answers for itself, so a module that took it over could report a closed sandbox;
the checks find a kernel or unit that doesn't filter, not a hostile module.
"""

import asyncio
from datetime import UTC, datetime

from mirrordash_core.config import get_core_version, load_config
from mirrordash_core.features.logs.saved import SAVED_LOG_DIR, saved_logs
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.installed import installed_modules
from mirrordash_core.features.power.privacy import SCREEN_SCALES
from mirrordash_core.features.wifi.network import get_wifi_info
from mirrordash_core.host import OS_BUILD_FILE, OS_VERSION_FILE

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


def modules_list() -> str:
    try:
        lines = [f"{m['name']} ({m['package_name']}) {m['version']}, {m['type']}"
                 + (f", {m['source']}" if m.get("source") else f", local folder {m['folder_name']}" if m["type"] == "local" else "")
                 for m in installed_modules()]
    except Exception as e:
        return f"(could not list: {e})"
    return "\n".join(lines) or "None"


async def screen() -> str:
    """Resolution, rotation and scale from the settings (only these) and what the screen reports, minus
    the monitor's serial number."""
    config = load_config()
    system, scale = config.get("system") or {}, (config.get("globals") or {}).get("screen_scale")
    scale = scale if type(scale) is int and scale in SCREEN_SCALES else 100
    reported = "\n".join(line for line in (await run("wlr-randr")).splitlines() if "Serial" not in line)
    return (f"rotation: {system.get('rotation', 'normal')}\nresolution: {system.get('resolution', 'auto')}\n"
            f"screen scale: {scale} %\n{reported}")


async def network() -> str:
    """The connection type and signal; never the network's name."""
    try:
        info = await get_wifi_info()
    except Exception as e:
        return f"(could not read: {e})"
    kind = f"{info['type']} (setup mode)" if info["type"] == "hotspot" else info["type"]
    return f"type: {kind}, signal: {info['signal']} %"


def saved_logs_tails() -> str:
    """The saved logs (logs.saved) with the last 150 lines of each."""
    logs = saved_logs()
    parts = ["\n".join(f"{log['created']}  {log['kind']}  {log['filename']}  {log['size']}" for log in logs) or "None"]
    for log in logs:
        try:
            tail = "\n".join((SAVED_LOG_DIR / log["filename"]).read_text(errors="replace").splitlines()[-150:])
        except OSError as e:
            tail = f"(unreadable: {e})"
        parts.append(f"--- {log['filename']} (last 150 lines) ---\n{tail}")
    return "\n\n".join(parts)


async def report(app_log: str, module_log: str) -> str:
    """The diagnostics download: plain text, one section per heading."""
    state = await host_state()
    checks = "\n".join(f"{'OPEN  ' if reachable else 'closed'}  {what}" for what, reachable in state["checks"])
    host = "\n".join(f"{key}: {state[key]}" for key in ("since", "restarts", "memory", "peak", "limit", "lan", "gpio"))
    sections = {
        "Versions": f"MirrorDash {get_core_version()}\nOS build {read(OS_BUILD_FILE)} ({read(OS_VERSION_FILE)})\n"
                    + await run("uname", "-a"),
        "Installed modules": modules_list(),
        "Screen": await screen(),
        "Network": await network(),
        "Module sandbox": state["error"] or (checks if state["sandboxed"] else "Not sandboxed (not on a mirror)\n" + checks),
        "Module host": host + f"\ncore memory: {state['core_memory']}",
        "OS slots (rauc status)": await run("rauc", "status"),
        "Failed services": await run("systemctl", "--failed", "--no-pager"),
        "Power (vcgencmd get_throttled)": await run("vcgencmd", "get_throttled"),
        "Memory and storage": await run("free", "-m") + "\n\n" + await run("df", "-h", "/", "/storage"),
        "Saved logs": saved_logs_tails(),
        "MirrorDash log": app_log,
        "Modules' log": module_log,
        "System log, warnings since boot": await run("journalctl", "-b", "-p", "warning", "-n", "300", "--no-pager",
                                                     "--utc", "-o", "short-iso"),
    }
    head = f"MirrorDash diagnostics, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n"
    return head + "".join(f"\n=== {title} ===\n{body.strip()}\n" for title, body in sections.items())
