# SPDX-License-Identifier: AGPL-3.0-only
"""Wi-Fi through NetworkManager's nmcli: scanning, joining, the saved networks, and the setup hotspot
(MirrorDash-Setup) the mirror opens when it has no network."""

import logging
import os
import time

from mirrordash_core.host import EXIT_CODE_TIMED_OUT, run_command

logger = logging.getLogger("mirrordash.core.wifi")


# Where NetworkManager keeps its saved networks; on the read-only root it is a link to /storage
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
# The networks seen just before the hotspot started (mirrordash-wifi-check.sh writes it): while the
# hotspot is up the Pi's single radio can't scan
SCAN_BEFORE_HOTSPOT_FILE = "/var/lib/mirrordash-wifi-scan.cache"


async def _nmcli(*args: str, timeout: float = 15) -> tuple[int, str]:
    """`nmcli <args>`: (exit_code, its output, or its error when it failed)."""
    exit_code, output, error_output = await run_command("nmcli", *args, timeout=timeout)
    return exit_code, (output if exit_code == 0 else error_output).strip()


def split_nmcli_terse_line(line: str) -> list[str]:
    """One line of `nmcli -t` output as its fields: ':' separates them, and nmcli writes a ':' or '\\'
    inside a field as '\\:' or '\\\\' (an SSID like "Home:5G")."""
    fields, current_field, characters = [], "", iter(line)
    for character in characters:
        if character == "\\":
            current_field += next(characters, "")
        elif character == ":":
            fields.append(current_field)
            current_field = ""
        else:
            current_field += character
    return [*fields, current_field]


def unescape_nmcli_value(value: str) -> str:
    """One value from `nmcli -t` or `nmcli -g`, which write a ':' or '\\' in it as '\\:' or '\\\\'."""
    return ":".join(split_nmcli_terse_line(value))


async def scan_wifi_networks() -> list[str]:
    """Nearby WiFi network names, without the mirror's own setup hotspot.

    While the hotspot is up, the Pi's single radio can't scan; nmcli then fails or returns an
    empty list, and the list saved just before the hotspot started is used instead."""
    def without_hotspot(ssids: list[str]) -> list[str]:
        return [ssid for ssid in ssids if ssid != HOTSPOT_SSID]
    return without_hotspot(await _scan_live()) or without_hotspot(_load_cached_scan())


async def _scan_live() -> list[str]:
    """Scan with nmcli: each name once, in nmcli's order (strongest first); [] when scanning isn't possible."""
    logger.info("Scanning for WiFi networks...")
    exit_code, output = await _nmcli("-t", "-f", "SSID", "dev", "wifi", "list")
    if exit_code != 0:
        logger.warning(f"WiFi scan failed: {output}")
        return []
    ssids = [unescape_nmcli_value(line) for line in output.splitlines()]
    return list(dict.fromkeys(ssid for ssid in ssids if ssid.strip()))


def _load_cached_scan() -> list[str]:
    """The networks seen just before the hotspot started, or [] when there is no such list."""
    try:
        with open(SCAN_BEFORE_HOTSPOT_FILE, encoding="utf-8") as f:
            ssids = [unescape_nmcli_value(line.strip()) for line in f if line.strip()]
    except FileNotFoundError:
        logger.warning("No cached WiFi scan found. AP may be active and scanning is unavailable.")
        return []
    except Exception as e:
        logger.error(f"Failed to read cached WiFi scan: {e}")
        return []
    logger.info(f"Loaded {len(ssids)} cached WiFi networks from {SCAN_BEFORE_HOTSPOT_FILE}")
    return ssids


async def connect_wifi(ssid: str, password: str | None = None) -> tuple[bool, str]:
    """Join a Wi-Fi network: (joined, nmcli's message). The setup hotspot is taken down first, since
    the Pi's single radio can't be a hotspot and a client at once."""
    logger.info(f"Attempting to connect to WiFi SSID: {ssid}")
    await _teardown_captive_ap()

    # ponytail: the password shows in `ps` while nmcli runs (local only); passwd-file would close that.
    # nmcli gives up by itself (--wait 30) before our timeout, so a slow join isn't cut off while it still runs
    password_arguments = ["password", password] if password else []
    exit_code, message = await _nmcli("--wait", "30", "dev", "wifi", "connect", ssid, *password_arguments, timeout=40)
    if exit_code == 0:
        logger.info(f"WiFi connection successful: {message}")
        return True, message
    if exit_code == EXIT_CODE_TIMED_OUT:
        logger.error("WiFi connection timed out.")
        return False, "Connection timed out after 40 seconds."
    logger.warning(f"WiFi connection failed (code {exit_code}): {message}")
    return False, message


async def _wifi_profiles(*flags: str) -> list[str]:
    """Names of the saved client Wi-Fi profiles (flags: "--active" for the one in use)."""
    _, output = await _nmcli("-t", "-f", "NAME,TYPE", "connection", "show", *flags)
    profiles = [split_nmcli_terse_line(line) for line in output.splitlines()]
    return [fields[0] for fields in profiles
            if len(fields) == 2 and fields[1] == "802-11-wireless" and fields[0] != HOTSPOT_SSID]


async def saved_wifi() -> list[dict]:
    """The saved Wi-Fi networks with their passwords, for an encrypted backup: [{"ssid", "psk"}]
    (psk "" for an open one). -s shows the secrets: polkit's settings.modify.system allows pi that."""
    networks = []
    for name in await _wifi_profiles():
        exit_code, output = await _nmcli("-s", "-g", "802-11-wireless.ssid,802-11-wireless-security.psk",
                                         "connection", "show", "id", name)
        if exit_code == 0 and output:
            ssid, _, psk = output.partition("\n")  # -g writes one field per line
            networks.append({"ssid": unescape_nmcli_value(ssid), "psk": unescape_nmcli_value(psk)})
    return networks


async def add_wifi(networks: list[dict]) -> None:
    """Save Wi-Fi networks (from a backup) without joining them; one already saved is left alone.
    ponytail: the password shows in `ps` while nmcli runs, as in connect_wifi."""
    saved = await _wifi_profiles()
    for network in networks:
        ssid, psk = str(network.get("ssid") or ""), str(network.get("psk") or "")
        if not ssid or ssid in saved or ssid == HOTSPOT_SSID:
            continue
        security_arguments = ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk] if psk else []
        exit_code, output = await _nmcli("connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid, *security_arguments)
        if exit_code != 0:
            logger.warning(f"Could not save the Wi-Fi network '{ssid}' from the backup: {output}")


# The last change from the admin page that failed, shown on the Wi-Fi card until the next one
failed_switch: dict = {}


async def switch_wifi(ssid: str, password: str | None) -> None:
    """Move the mirror to another Wi-Fi. If it can't join, it goes back to the one it was on, and a
    profile this attempt created (with a wrong password) is deleted again."""
    active, saved = await _wifi_profiles("--active"), await _wifi_profiles()
    ok, message = await connect_wifi(ssid, password)
    if ok:
        failed_switch.clear()
        return
    if ssid not in saved:
        await _nmcli("connection", "delete", "id", ssid)
    if active:
        await _nmcli("connection", "up", "id", active[0], timeout=45)
    failed_switch.update(ssid=ssid, reason=message, previous=active[0] if active else "")


# ponytail: a short cache so the captive-portal middleware doesn't run nmcli on every request.
# It must never be permanent: the hotspot comes up ~40 s after boot, and a cached early
# "False" kept the kiosk on the admin setup page. Upgrade path: NM D-Bus signal subscription.
HOTSPOT_CACHE_TTL = 5.0
_hotspot_active_cached: bool | None = None  # None: ask NetworkManager at the next check
_hotspot_checked_at = 0.0


def _remember_hotspot_state(active: bool) -> None:
    global _hotspot_active_cached, _hotspot_checked_at
    _hotspot_active_cached, _hotspot_checked_at = active, time.monotonic()


def forget_hotspot_state() -> None:
    """The hotspot was switched outside the app: the next check asks NetworkManager again."""
    global _hotspot_active_cached
    _hotspot_active_cached = None


async def is_wifi_hotspot_active() -> bool:
    """Check if the MirrorDash-Setup WiFi hotspot is currently active in NetworkManager."""
    if _hotspot_active_cached is not None and time.monotonic() - _hotspot_checked_at < HOTSPOT_CACHE_TTL:
        return _hotspot_active_cached
    exit_code, output = await _nmcli("-t", "-f", "NAME", "connection", "show", "--active", timeout=10)
    if exit_code != 0:
        logger.error(f"Checking the WiFi hotspot failed: {output}")
        return False
    active = HOTSPOT_SSID in output.splitlines()
    _remember_hotspot_state(active)
    return active


async def get_hotspot_password() -> str:
    """The setup hotspot's password, read from its NetworkManager profile; "" if it can't be read."""
    exit_code, output = await _nmcli("-s", "-g", "802-11-wireless-security.psk", "connection", "show", HOTSPOT_SSID, timeout=10)
    if exit_code != 0:
        logger.debug(f"Reading the hotspot password failed: {output}")
        return ""
    return output


async def restore_captive_ap() -> bool:
    """Bring the setup hotspot back after a failed connection, with the same profile and password.
    Returns False if NetworkManager couldn't start it."""
    exit_code, output = await _nmcli("connection", "up", HOTSPOT_SSID, timeout=30)
    if exit_code != 0:
        logger.warning(f"Bringing the setup hotspot back failed: {output}")
        return False
    _remember_hotspot_state(True)
    logger.info("The setup hotspot is back.")
    return True


async def _teardown_captive_ap() -> None:
    """Deactivate the captive-portal AP connection so wlan0 can become a client.

    The profile is kept (it doesn't autoconnect), so the mirror's hotspot password stays the same
    the next time Wi-Fi setup is needed.
    """
    _remember_hotspot_state(False)
    exit_code, output = await _nmcli("connection", "down", HOTSPOT_SSID)
    if exit_code == 0:
        logger.info("The setup hotspot is down.")
    else:
        logger.debug(f"AP teardown: nmcli connection down returned {exit_code}: {output}")
