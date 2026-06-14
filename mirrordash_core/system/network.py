# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
from mirrordash_core.system.os import remount_rw, remount_ro

logger = logging.getLogger("mirrordash.core.system.network")

async def scan_wifi_networks() -> list[str]:
    """Scan for nearby WiFi networks using nmcli. Returns a list of SSIDs."""
    logger.info("Scanning for WiFi networks...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "nmcli", "-t", "-f", "SSID", "dev", "wifi", "list",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            logger.warning(f"WiFi scan failed: {stderr.decode().strip()}")
            return []

        # Parse SSIDs, filter duplicates and empty lines
        ssids = []
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            ssid = line.strip()
            if ssid and ssid not in ssids:
                ssids.append(ssid)
        return ssids
    except Exception as e:
        logger.error(f"Error scanning WiFi: {e}")
        return []

async def connect_wifi(ssid: str, password: str | None = None) -> tuple[bool, str]:
    """Connect to a WiFi network.

    1. Remount filesystem read-write.
    2. Connect using NetworkManager.
    3. Remount filesystem read-only.
    Returns (success_bool, message_str).
    """
    logger.info(f"Attempting to connect to WiFi SSID: {ssid}")

    # Unlock FS
    await remount_rw()

    cmd = ["sudo", "nmcli"]
    if password:
        cmd.append("--ask")
    cmd.extend(["dev", "wifi", "connect", ssid])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        if password:
            stdout, stderr = await asyncio.wait_for(proc.communicate(input=f"{password}\n".encode()), timeout=30)
        else:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        # Lock FS
        await remount_ro()

        if proc.returncode == 0:
            msg = stdout.decode().strip()
            logger.info(f"WiFi connection successful: {msg}")
            return True, msg
        else:
            msg = stderr.decode().strip() or stdout.decode().strip()
            logger.warning(f"WiFi connection failed (code {proc.returncode}): {msg}")
            return False, msg

    except asyncio.TimeoutError:
        await remount_ro()
        logger.error("WiFi connection timed out.")
        return False, "Connection timed out after 30 seconds."
    except Exception as e:
        await remount_ro()
        logger.error(f"WiFi connection error: {e}")
        return False, str(e)

async def get_ssh_status() -> bool:
    """Check if the SSH systemd service is active/enabled."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "ssh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip() == "active"
    except Exception:
        return False

async def set_ssh_status(enabled: bool) -> bool:
    """Enable/start or disable/stop the SSH service."""
    await remount_rw()
    try:
        action = "enable" if enabled else "disable"
        proc1 = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", action, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc1.wait()

        start_stop = "start" if enabled else "stop"
        proc2 = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", start_stop, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc2.wait()

        await remount_ro()
        return True
    except Exception as e:
        logger.error(f"Failed to change SSH status: {e}")
        await remount_ro()
        return False

async def is_wifi_hotspot_active() -> bool:
    """Check if the MirrorDash-Setup WiFi hotspot is currently active in NetworkManager."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "nmcli", "-t", "-f", "NAME", "connection", "show", "--active",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        lines = stdout.decode("utf-8", errors="ignore").splitlines()
        return "MirrorDash-Setup" in lines
    except Exception as e:
        logger.error(f"Failed to check if hotspot is active: {e}")
        return False
