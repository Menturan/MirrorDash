# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
import re
import sys

logger = logging.getLogger("mirrordash.core.system.os")

_originally_read_only: bool | None = None

def is_root_read_only() -> bool:
    """Check if the root filesystem is mounted read-only."""
    try:
        if os.path.exists("/proc/mounts"):
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4 and parts[1] == "/":
                        options = parts[3].split(",")
                        return "ro" in options
    except Exception as e:
        logger.warning(f"Error checking /proc/mounts: {e}")

    # Fallback: check if we can write to the application's directory
    try:
        test_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".write_test_{os.getpid()}")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return False
    except OSError as e:
        import errno
        if e.errno == errno.EROFS:
            return True
        return False
    except Exception:
        return False

async def remount_rw() -> bool:
    """Remount the root filesystem read-write. Returns True on success."""
    global _originally_read_only
    if _originally_read_only is None:
        _originally_read_only = is_root_read_only()

    if not _originally_read_only:
        logger.debug("Filesystem is not read-only. Skipping remount_rw.")
        return True

    logger.info("Attempting to remount filesystem as Read-Write...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "mount", "-o", "remount,rw", "/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            logger.warning(
                f"remount,rw failed (code {proc.returncode}): {stderr.decode().strip()}. "
                "This may be expected if not running on OverlayFS."
            )
            return False
        logger.info("Filesystem remounted as Read-Write.")
        return True
    except asyncio.TimeoutError:
        logger.error("remount,rw timed out after 10 seconds.")
        return False
    except Exception as e:
        logger.warning(f"Failed to remount RW: {e}. This may be expected if not on OverlayFS.")
        return False

async def remount_ro() -> bool:
    """Remount the root filesystem read-only. Returns True on success."""
    global _originally_read_only
    if _originally_read_only is None:
        _originally_read_only = is_root_read_only()

    if not _originally_read_only:
        logger.debug("Filesystem was not originally read-only. Skipping remount_ro.")
        return True

    logger.info("Attempting to remount filesystem as Read-Only...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "mount", "-o", "remount,ro", "/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            logger.warning(
                f"remount,ro failed (code {proc.returncode}): {stderr.decode().strip()}"
            )
            return False
        logger.info("Filesystem remounted as Read-Only.")
        return True
    except asyncio.TimeoutError:
        logger.error("remount,ro timed out after 10 seconds.")
        return False
    except Exception as e:
        logger.warning(f"Failed to remount RO: {e}")
        return False

async def run_restart() -> None:
    """Cleanly stop uvicorn by sending SIGTERM to the current process."""
    logger.info("Restarting application via SIGTERM...")
    await asyncio.sleep(1)  # Allow HTTP responses to flush
    os.kill(os.getpid(), 15)  # SIGTERM — uvicorn handles this gracefully

async def _schedule_power_command(command: str, delay_sec: float) -> None:
    """Run `sudo -n <command>` (reboot/poweroff) after a delay, so the HTTP response gets out first."""
    logger.info(f"Scheduling system {command} in {delay_sec} seconds...")

    async def _run():
        await asyncio.sleep(delay_sec)
        try:
            logger.info(f"Executing sudo {command}...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "-n", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(f"sudo {command} failed: {stderr.decode(errors='replace').strip()}")
        except Exception as e:
            logger.error(f"{command} command failed: {e}")

    asyncio.create_task(_run())

async def sudo_allowed(command: str) -> bool:
    """True if the sudoers allow-list lets this app run `command` without a password."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "-l", command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        return await proc.wait() == 0
    except Exception:
        return False

async def reboot_system(delay_sec: float = 2.0) -> None:
    """Asynchronously trigger an OS-level reboot after a delay."""
    await _schedule_power_command("reboot", delay_sec)

async def poweroff_system(delay_sec: float = 2.0) -> None:
    """Asynchronously shut the mirror down after a delay."""
    await _schedule_power_command("poweroff", delay_sec)

async def apply_system_timezone(timezone: str) -> bool:
    """Apply system timezone using timedatectl."""
    logger.info(f"Applying system timezone: {timezone}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "timedatectl", "set-timezone", timezone,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            logger.warning(f"timedatectl failed to set timezone to {timezone}: {stderr.decode().strip()}")
            return False
        logger.info(f"System timezone successfully set to {timezone}")
        await apply_wifi_country(wifi_country_for(timezone))
        return True
    except Exception as e:
        logger.warning(f"Failed to set system timezone to {timezone}: {e}")
        return False


ZONE_TAB = "/usr/share/zoneinfo/zone.tab"
CMDLINE = "/boot/firmware/cmdline.txt"


def wifi_country_for(timezone: str) -> str | None:
    """The country a timezone belongs to (Europe/Stockholm -> SE), or None (UTC, unknown)."""
    try:
        with open(ZONE_TAB, encoding="utf-8") as f:
            for line in f:
                fields = line.split("\t")
                if len(fields) >= 3 and not line.startswith("#") and fields[2].strip() == timezone:
                    return fields[0]
    except OSError as e:
        logger.debug(f"Cannot read {ZONE_TAB}: {e}")
    return None


async def apply_wifi_country(country: str | None) -> bool:
    """Set the Wi-Fi country (which channels may be used) in cmdline.txt; it applies at the next start.
    Only written when it changes, so the boot partition isn't rewritten at every start."""
    if not country or not re.fullmatch(r"[A-Z]{2}", country):
        return False
    try:
        with open(CMDLINE, encoding="utf-8") as f:
            if f" cfg80211.ieee80211_regdom={country}" in f" {f.read().strip()} ":
                return True
    except OSError:
        return False  # not a Pi (development machine)
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", "raspi-config", "nonint", "do_wifi_country", country,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        logger.warning(f"Setting the Wi-Fi country to {country} failed: {stderr.decode(errors='replace').strip()}")
        return False
    logger.info(f"Wi-Fi country set to {country} (from the time zone); applies at the next start")
    return True

async def apply_system_password_hash(pwd_hash: str) -> bool:
    """Apply system password hash for user 'pi' using chpasswd -e."""
    logger.info("Applying system password hash...")
    try:
        chpasswd_input = f"pi:{pwd_hash}\n".encode()
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "chpasswd", "-e",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate(input=chpasswd_input)
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            logger.error(f"chpasswd -e failed: {err_msg}")
            return False
        logger.info("System password hash applied successfully.")
        return True
    except Exception as e:
        logger.error(f"Unexpected error running chpasswd -e: {e}")
        return False

