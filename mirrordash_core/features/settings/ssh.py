# SPDX-License-Identifier: AGPL-3.0-only
"""SSH on or off (Settings → Developer), and the pi user's password it needs."""

import logging
import os

from fastapi import HTTPException

from mirrordash_core.config import write_atomic
from mirrordash_core.host import apply_system_password_hash, run_command

logger = logging.getLogger("mirrordash.core.settings")


async def get_ssh_status() -> bool:
    """Whether the SSH service is running."""
    _, output, _ = await run_command("systemctl", "is-active", "ssh")
    return output.strip() == "active"


async def _sudo_systemctl_ssh(action: str) -> int:
    """`sudo systemctl <action> ssh` (one of the sudo rules in sudoers.d/mirrordash), its exit code."""
    exit_code, _, _ = await run_command("sudo", "-n", "systemctl", action, "ssh", timeout=30)
    return exit_code


async def set_ssh_status(enabled: bool) -> bool:
    """Enable/start or disable/stop the SSH service. Never starts it without the owner's own pi password
    (its hash on /storage): a stray `ssh: true` in the config once started it with the image's password."""
    if enabled and not os.path.exists(PASSWORD_HASH_FILE):
        logger.warning("Not starting SSH: no password for pi has been set (Settings → SSH)")
        enabled = False
    try:
        at_boot_action = "enable" if enabled else "disable"
        enable_exit_code = await _sudo_systemctl_ssh(at_boot_action)
        now_action = "start" if enabled else "stop"
        start_exit_code = await _sudo_systemctl_ssh(now_action)
        if enable_exit_code != 0 or start_exit_code != 0:
            logger.error(f"systemctl {at_boot_action}/{now_action} ssh failed (exit {enable_exit_code}, {start_exit_code})")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to change SSH status: {e}")
        return False


# The pi user's password, as a crypt hash on /storage: re-applied at boot, since /etc is a RAM overlay
PASSWORD_HASH_FILE = "/home/pi/.mirrordash/data/pi_password.hash"


async def _set_pi_password(password: str) -> None:
    """Hash it, apply the hash now and remember it for the next boot. HTTPException when it fails."""
    # The password goes in on stdin: on the command line `ps` would show it
    exit_code, output, error_output = await run_command("openssl", "passwd", "-6", "-stdin", input_text=password)
    if exit_code != 0:
        logger.error(f"openssl hash failed: {error_output.strip()}")
        raise HTTPException(status_code=500, detail="Failed to hash system password.")
    password_hash = output.strip()
    if not await apply_system_password_hash(password_hash):
        raise HTTPException(status_code=500, detail="Failed to update system password.")
    logger.info("Password for user 'pi' updated successfully.")
    try:
        write_atomic(PASSWORD_HASH_FILE, password_hash, 0o600)
    except OSError as e:
        logger.error(f"Failed to write password hash to disk: {e}")


async def apply_ssh(enabled: bool, password: str | None) -> None:
    """Turn SSH on or off. Turning it on needs a new password for pi (at least 8 characters)."""
    if enabled and not await get_ssh_status():
        if not password or len(password) < 8:
            raise HTTPException(status_code=400, detail="A password of at least 8 characters is required to enable SSH.")
        try:
            await _set_pi_password(password)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error running chpasswd: {e}")
            raise HTTPException(status_code=500, detail="Unexpected error updating system password.")
    elif not enabled:
        try:
            os.remove(PASSWORD_HASH_FILE)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.error(f"Failed to remove password hash: {e}")
    if not await set_ssh_status(enabled):
        raise HTTPException(status_code=500, detail=f"Could not turn SSH {'on' if enabled else 'off'}. Check the mirror's log.")
