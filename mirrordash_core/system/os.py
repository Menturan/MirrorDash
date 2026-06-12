# Required Notice: Copyright (C) 2026 Jonas Öhlander (https://github.com/Menturan/MirrorDash)
# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
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
            "sudo", "mount", "-o", "remount,rw", "/",
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
            "sudo", "mount", "-o", "remount,ro", "/",
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

async def reboot_system(delay_sec: float = 2.0) -> None:
    """Asynchronously trigger an OS-level reboot after a delay."""
    logger.info(f"Scheduling system reboot in {delay_sec} seconds...")

    async def _do_reboot():
        await asyncio.sleep(delay_sec)
        try:
            logger.info("Executing sudo reboot...")
            proc = await asyncio.create_subprocess_exec(
                "sudo", "reboot",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
        except Exception as e:
            logger.error(f"Reboot command failed: {e}")

    asyncio.create_task(_do_reboot())

async def apply_system_timezone(timezone: str) -> bool:
    """Apply system timezone using timedatectl."""
    logger.info(f"Applying system timezone: {timezone}")
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "timedatectl", "set-timezone", timezone,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            logger.warning(f"timedatectl failed to set timezone to {timezone}: {stderr.decode().strip()}")
            return False
        logger.info(f"System timezone successfully set to {timezone}")
        return True
    except Exception as e:
        logger.warning(f"Failed to set system timezone to {timezone}: {e}")
        return False

async def apply_system_password_hash(pwd_hash: str) -> bool:
    """Apply system password hash for user 'pi' using chpasswd -e."""
    logger.info("Applying system password hash...")
    try:
        chpasswd_input = f"pi:{pwd_hash}\n".encode()
        proc = await asyncio.create_subprocess_exec(
            "sudo", "chpasswd", "-e",
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

