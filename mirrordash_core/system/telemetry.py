import asyncio
import logging
import importlib.metadata
import urllib.request
import json

logger = logging.getLogger("mirrordash.core.telemetry")

def get_uptime_seconds() -> float:
    try:
        with open("/proc/uptime", "r") as f:
            return float(f.readline().split()[0])
    except Exception:
        return 0.0

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
        with open("/proc/meminfo", "r") as f:
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
    try:
        proc = await asyncio.create_subprocess_exec(
            "timedatectl", "show", "--property=NTPSynchronized",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            output = stdout.decode("utf-8", errors="ignore").strip()
            return "NTPSynchronized=yes" in output
    except Exception:
        pass
    return True # Default to True in dev mode to avoid false alarms

async def get_wifi_info() -> dict:
    try:
        # Check active Wi-Fi connection
        proc = await asyncio.create_subprocess_exec(
            "nmcli", "-t", "-f", "active,ssid,signal", "dev", "wifi",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            lines = stdout.decode("utf-8", errors="ignore").splitlines()
            for line in lines:
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == "yes" and parts[1]:
                    try:
                        signal = int(parts[2])
                    except ValueError:
                        signal = 100
                    return {"ssid": parts[1], "signal": signal, "type": "wifi"}
    except Exception:
        pass
        
    # Check if there is an active Ethernet connection
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmcli", "-t", "-f", "NAME,TYPE,DEVICE,STATE", "connection", "show", "--active",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            lines = stdout.decode("utf-8", errors="ignore").splitlines()
            for line in lines:
                parts = line.split(":")
                if len(parts) >= 2 and "ethernet" in parts[1].lower():
                    return {"ssid": parts[0], "signal": 100, "type": "ethernet"}
    except Exception:
        pass

    # Check if captive portal hotspot is active
    try:
        from mirrordash_core.system.network import is_wifi_hotspot_active
        if await is_wifi_hotspot_active():
            return {"ssid": "MirrorDash-Setup (Hotspot)", "signal": 100, "type": "hotspot"}
    except Exception:
        pass

    return {"ssid": "N/A (Dev Mode)", "signal": 0, "type": "none"}

async def get_undervoltage_detected() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "vcgencmd", "get_throttled",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if proc.returncode == 0:
            output = stdout.decode("utf-8", errors="ignore").strip()
            if "=" in output:
                val_str = output.split("=")[1].strip()
                val = int(val_str, 16)
                # Check bits: val & 0x50005 != 0 (indicates current or historical under-voltage)
                return (val & 0x50005) != 0
    except Exception:
        pass
    return False

def get_core_version() -> str:
    try:
        return importlib.metadata.version("mirrordash")
    except Exception:
        return "unknown"

async def check_all_updates() -> dict:
    updates = {
        "core": {"update_available": False, "latest_version": get_core_version(), "current_version": get_core_version()},
        "modules": []
    }
    
    # 1. Check core update
    try:
        from mirrordash_core.api.admin_system import check_core_update
        core_res = await check_core_update()
        updates["core"] = {
            "update_available": core_res.get("update_available", False),
            "latest_version": core_res.get("latest_version", ""),
            "current_version": core_res.get("current_version", "")
        }
    except Exception:
        pass

    # 2. Check modules updates
    eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))
    
    async def check_single_module(ep) -> dict | None:
        module_name = ep.name
        package_name = ep.dist.name if ep.dist else module_name
        current_version = ep.dist.version if ep.dist else "0.0.0"
        
        def _fetch_pypi() -> str | None:
            url = f"https://pypi.org/pypi/{package_name}/json"
            try:
                with urllib.request.urlopen(url, timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("info", {}).get("version")
            except Exception:
                return None
                
        def _parse_version(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.split(".")[:3])
            except ValueError:
                return (0,)

        latest_version = await asyncio.to_thread(_fetch_pypi)
        if latest_version:
            is_newer = _parse_version(latest_version) > _parse_version(current_version)
            if is_newer:
                return {
                    "name": module_name,
                    "title": module_name.replace("mirrordash-", "").replace("mirrordash_", "").title(),
                    "current_version": current_version,
                    "latest_version": latest_version
                }
        return None

    tasks = [check_single_module(ep) for ep in eps]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if res and not isinstance(res, Exception):
            updates["modules"].append(res)
            
    return updates
