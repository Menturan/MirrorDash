# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
import time

logger = logging.getLogger("mirrordash.core.wifi")


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


HOTSPOT_SSID = "MirrorDash-Setup"


async def scan_wifi_networks() -> list[str]:
    """Nearby WiFi network names, without the mirror's own setup hotspot.

    While the hotspot is up, the Pi's single radio can't scan; nmcli then fails or returns an
    empty list, and the list saved just before the hotspot started is used instead."""
    def without_hotspot(ssids: list[str]) -> list[str]:
        return [ssid for ssid in ssids if ssid != HOTSPOT_SSID]
    return without_hotspot(await _scan_live()) or without_hotspot(_load_cached_scan())


async def _scan_live() -> list[str]:
    """Scan with nmcli; [] when scanning isn't possible."""
    logger.info("Scanning for WiFi networks...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "nmcli", "-t", "-f", "SSID", "dev", "wifi", "list",
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

    try:
        await _teardown_captive_ap()
    except Exception as e:
        logger.warning(f"Captive AP teardown encountered an issue (continuing): {e}")

    cmd = ["sudo", "-n", "nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd.extend(["password", password])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 0:
            msg = stdout.decode().strip()
            logger.info(f"WiFi connection successful: {msg}")
            return True, msg
        else:
            msg = stderr.decode().strip() or stdout.decode().strip()
            logger.warning(f"WiFi connection failed (code {proc.returncode}): {msg}")
            return False, msg

    except asyncio.TimeoutError:
        logger.error("WiFi connection timed out.")
        return False, "Connection timed out after 30 seconds."
    except Exception as e:
        logger.error(f"WiFi connection error: {e}")
        return False, str(e)


_hotspot_active_cached = None


_hotspot_checked_at = 0.0


# ponytail: short TTL so the captive-portal middleware doesn't spawn nmcli on every request.
# It must never be permanent: the hotspot comes up ~40 s after boot, and a cached early
# "False" kept the kiosk on the admin setup page. Upgrade path: NM D-Bus signal subscription.
HOTSPOT_CACHE_TTL = 5.0


async def is_wifi_hotspot_active() -> bool:
    """Check if the MirrorDash-Setup WiFi hotspot is currently active in NetworkManager."""
    global _hotspot_active_cached, _hotspot_checked_at
    if _hotspot_active_cached is not None and time.monotonic() - _hotspot_checked_at < HOTSPOT_CACHE_TTL:
        return _hotspot_active_cached

    # Fast path: read active connections without sudo (no TTY needed)
    for cmd in (
        ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
        ["sudo", "-n", "nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
    ):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                lines = stdout.decode("utf-8", errors="ignore").splitlines()
                active = HOTSPOT_SSID in lines
                _hotspot_active_cached = active
                _hotspot_checked_at = time.monotonic()
                return active
        except asyncio.TimeoutError:
            continue
        except Exception:
            continue
    logger.error("Timed out or failed checking WiFi hotspot status")
    return False


async def get_hotspot_password() -> str:
    """The setup hotspot's password, read from its NetworkManager profile; "" if it can't be read."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", HOTSPOT_SSID,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="ignore").strip()
    except Exception as e:
        logger.debug(f"Reading the hotspot password failed: {e}")
    return ""


async def restore_captive_ap() -> bool:
    """Bring the setup hotspot back after a failed connection, with the same profile and password.
    Returns False if NetworkManager couldn't start it."""
    global _hotspot_active_cached, _hotspot_checked_at
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "nmcli", "connection", "up", HOTSPOT_SSID,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        logger.warning(f"Bringing the setup hotspot back failed: {e}")
        return False
    if proc.returncode != 0:
        logger.warning(f"Bringing the setup hotspot back failed: {stderr.decode(errors='replace').strip()}")
        return False
    _hotspot_active_cached, _hotspot_checked_at = True, time.monotonic()
    logger.info("The setup hotspot is back.")
    return True


async def _teardown_captive_ap() -> None:
    """Deactivate the captive-portal AP connection so wlan0 can become a client.

    The profile is kept (it doesn't autoconnect), so the mirror's hotspot password stays the same
    the next time Wi-Fi setup is needed.
    """
    global _hotspot_active_cached, _hotspot_checked_at
    _hotspot_active_cached = False
    _hotspot_checked_at = time.monotonic()
    cmd = ["sudo", "-n", "nmcli", "connection", "down", HOTSPOT_SSID]
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
