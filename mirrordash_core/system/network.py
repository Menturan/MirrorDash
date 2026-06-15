# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
from mirrordash_core.system.os import remount_rw, remount_ro

logger = logging.getLogger("mirrordash.core.system.network")

NM_CONN_DIR = "/etc/NetworkManager/system-connections"
NM_PERSISTENT_LINK = "/storage/mirrordash/system-connections"


def ensure_nm_wifi_persistence() -> None:
    """Ensure NetworkManager WiFi profiles survive OverlayFS by symlinking to the persistent partition.

    Idempotent: safe to run on every boot. Migrates existing profiles on first run.
    """
    if os.path.islink(NM_CONN_DIR):
        target = os.path.realpath(NM_CONN_DIR)
        if target == NM_PERSISTENT_LINK:
            return  # Already correct
        logger.warning(f"NM connections dir is a symlink to unexpected target: {target}")

    if not os.path.isdir(NM_CONN_DIR):
        logger.debug(f"NM connections dir does not exist yet: {NM_CONN_DIR}")
        return

    # Migrate existing profiles to persistent storage before replacing the directory
    os.makedirs(NM_PERSISTENT_LINK, exist_ok=True)
    for entry in os.listdir(NM_CONN_DIR):
        src = os.path.join(NM_CONN_DIR, entry)
        dst = os.path.join(NM_PERSISTENT_LINK, entry)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                os.replace(src, dst)
                logger.info(f"Migrated NM profile to persistent storage: {entry}")
            except Exception as e:
                logger.warning(f"Failed to migrate NM profile {entry}: {e}")

    # Replace directory with symlink
    try:
        os.rmdir(NM_CONN_DIR)
    except OSError:
        # Directory not empty or other issue — log and continue
        logger.warning(f"Could not remove old NM connections dir {NM_CONN_DIR}, it may contain unmigrated files.")

    try:
        os.symlink(NM_PERSISTENT_LINK, NM_CONN_DIR)
        logger.info(f"Created symlink: {NM_CONN_DIR} -> {NM_PERSISTENT_LINK}")
    except OSError as e:
        logger.error(f"Failed to create NM connections symlink: {e}")

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
            return _load_cached_scan()

        # Parse SSIDs, filter duplicates and empty lines
        ssids = []
        for line in stdout.decode("utf-8", errors="ignore").splitlines():
            ssid = line.strip()
            if ssid and ssid not in ssids:
                ssids.append(ssid)
        return ssids
    except Exception as e:
        logger.error(f"Error scanning WiFi: {e}")
        return _load_cached_scan()


def _load_cached_scan() -> list[str]:
    """Return the pre-AP scan cache if it exists."""
    cache_path = "/var/lib/mirrordash-wifi-scan.cache"
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return []
        ssids = [line.strip() for line in content.splitlines() if line.strip()]
        logger.info(f"Loaded {len(ssids)} cached WiFi networks from {cache_path}")
        return ssids
    except FileNotFoundError:
        logger.warning("No cached WiFi scan found. AP may be active and scanning is unavailable.")
        return []
    except Exception as e:
        logger.error(f"Failed to read cached WiFi scan: {e}")
        return []

async def connect_wifi(ssid: str, password: str | None = None) -> tuple[bool, str]:
    """Connect to a WiFi network.

    1. Remount filesystem read-write.
    2. Delete the captive-portal AP profile so wlan0 can connect as a client.
    3. Connect using NetworkManager.
    4. Remount filesystem read-only.
    Returns (success_bool, message_str).
    """
    logger.info(f"Attempting to connect to WiFi SSID: {ssid}")

    await remount_rw()

    try:
        await _teardown_captive_ap()
    except Exception as e:
        logger.warning(f"Captive AP teardown encountered an issue (continuing): {e}")

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


async def _teardown_captive_ap() -> None:
    """Delete and deactivate the captive-portal AP connection so wlan0 can become a client."""
    for cmd in (
        ["sudo", "nmcli", "connection", "down", "MirrorDash-Setup"],
        ["sudo", "nmcli", "connection", "delete", "MirrorDash-Setup"],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode == 0:
                logger.info(f"Executed: {' '.join(cmd)}")
            else:
                logger.debug(f"AP teardown: {' '.join(cmd)} returned {proc.returncode}")
        except Exception as e:
            logger.debug(f"AP teardown: {' '.join(cmd)} failed: {e}")
