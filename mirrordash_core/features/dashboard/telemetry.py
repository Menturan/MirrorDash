# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import os

from mirrordash_core.config import get_core_version
from mirrordash_core.features.wifi.network import (
    is_wifi_hotspot_active,
    split_nmcli_terse_line,
)
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.dashboard")


def get_uptime_seconds() -> float:
    try:
        with open("/proc/uptime") as f:
            return float(f.readline().split()[0])
    except Exception:
        return 0.0


def get_cpu_temperature() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def get_cpu_load() -> float | None:
    """CPU load in percent of all cores, None where the OS has no load average."""
    # ponytail: the 1-minute load average, not an instantaneous %; it also counts processes waiting
    # on I/O. Upgrade path: the /proc/stat busy/idle delta between two dashboard polls.
    try:
        return round(min(os.getloadavg()[0] / (os.cpu_count() or 1) * 100, 100.0), 1)
    except OSError:
        return None


def get_uptime_string() -> str:
    uptime_sec = get_uptime_seconds()
    if uptime_sec == 0.0:
        return "N/A (Dev Mode)"
    
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def get_ram_usage() -> dict:
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        
        total_kb = meminfo.get("MemTotal", 0)
        free_kb = meminfo.get("MemFree", 0)
        buffers_kb = meminfo.get("Buffers", 0)
        cached_kb = meminfo.get("Cached", 0)
        available_kb = meminfo.get("MemAvailable", free_kb + buffers_kb + cached_kb)
        
        used_kb = total_kb - available_kb
        if total_kb > 0:
            percent_used = round((used_kb / total_kb) * 100, 1)
            return {
                "total_mb": round(total_kb / 1024),
                "used_mb": round(used_kb / 1024),
                "percent_used": percent_used
            }
    except Exception:
        pass
    
    # Fallback / Dev mode
    return {
        "total_mb": 4096,
        "used_mb": 1024,
        "percent_used": 25.0
    }


async def get_ntp_status() -> bool:
    """Whether the clock is set from the internet; True where timedatectl can't say (development machine),
    so no false alarm."""
    exit_code, output, _ = await run_command("timedatectl", "show", "--property=NTPSynchronized", timeout=3)
    return exit_code != 0 or "NTPSynchronized=yes" in output


async def get_wifi_info() -> dict:
    """The connection the mirror is on: its Wi-Fi (with signal), its Ethernet, or the setup hotspot."""
    exit_code, output, _ = await run_command("nmcli", "-t", "-f", "active,ssid,signal", "dev", "wifi", timeout=3)
    for line in output.splitlines() if exit_code == 0 else []:
        fields = split_nmcli_terse_line(line)
        if len(fields) >= 3 and fields[0] == "yes" and fields[1]:
            signal = int(fields[2]) if fields[2].isdigit() else 100
            return {"ssid": fields[1], "signal": signal, "type": "wifi"}

    exit_code, output, _ = await run_command("nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active", timeout=3)
    for line in output.splitlines() if exit_code == 0 else []:
        fields = split_nmcli_terse_line(line)
        if len(fields) >= 2 and "ethernet" in fields[1].lower():
            return {"ssid": fields[0], "signal": 100, "type": "ethernet"}

    if await is_wifi_hotspot_active():
        return {"ssid": "MirrorDash-Setup (Hotspot)", "signal": 100, "type": "hotspot"}

    return {"ssid": "N/A (Dev Mode)", "signal": 0, "type": "none"}


# The bits of `vcgencmd get_throttled` that are about the power supply: bit 0 "under-voltage now" and bit 16
# "under-voltage since boot". Bits 2 and 18 (throttled) also come from heat, so they say nothing about the supply.
UNDERVOLTAGE_NOW_OR_SINCE_BOOT = 0x10001


async def get_undervoltage_detected() -> bool:
    """Whether the Pi's power supply has dropped too low since it started (the firmware keeps count)."""
    exit_code, output, _ = await run_command("vcgencmd", "get_throttled", timeout=3)
    if exit_code != 0 or "=" not in output:
        return False
    try:
        throttled_bits = int(output.split("=")[1].strip(), 16)  # "throttled=0x50005"
    except ValueError:
        return False
    return bool(throttled_bits & UNDERVOLTAGE_NOW_OR_SINCE_BOOT)


async def check_all_updates() -> dict:
    """MirrorDash's and the modules' updates, for the dashboard banner."""
    updates = {
        "core": {"update_available": False, "latest_version": get_core_version(), "current_version": get_core_version()},
        "modules": [],
    }
    try:
        from mirrordash_core.features.modules.service import module_updates
        updates["modules"] = [u["package_name"] for u in (await module_updates())[0]]
    except Exception:
        pass
    try:
        from mirrordash_core.features.updates.service import check_core_update
        core_res = await check_core_update()
        updates["core"] = {
            "update_available": core_res.get("update_available", False),
            "latest_version": core_res.get("latest_version", ""),
            "current_version": core_res.get("current_version", "")
        }
    except Exception:
        pass
    return updates
