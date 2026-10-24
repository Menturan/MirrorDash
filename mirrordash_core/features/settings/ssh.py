# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os

from fastapi import HTTPException

from mirrordash_core.config import write_atomic
from mirrordash_core.host import apply_system_password_hash

logger = logging.getLogger("mirrordash.core.settings")


async def get_ssh_status() -> bool:
    """Check if the SSH systemd service is active/enabled."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", "ssh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return stdout.decode().strip() == "active"
    except Exception:
        return False


async def set_ssh_status(enabled: bool) -> bool:
    """Enable/start or disable/stop the SSH service. Never starts it without the owner's own pi password
    (its hash on /storage): a stray `ssh: true` in the config once started it with the image's password."""
    if enabled and not os.path.exists(PASSWORD_HASH_FILE):
        logger.warning("Not starting SSH: no password for pi has been set (Settings → SSH)")
        enabled = False
    try:
        action = "enable" if enabled else "disable"
        proc1 = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", action, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        rc1 = await proc1.wait()

        start_stop = "start" if enabled else "stop"
        proc2 = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", start_stop, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        rc2 = await proc2.wait()

        if rc1 != 0 or rc2 != 0:
            logger.error(f"systemctl {action}/{start_stop} ssh failed (exit {rc1}, {rc2})")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to change SSH status: {e}")
        return False


# The pi user's password, as a crypt hash on /storage: re-applied at boot, since /etc is a RAM overlay
PASSWORD_HASH_FILE = "/home/pi/.mirrordash/data/pi_password.hash"


async def _set_pi_password(password: str) -> None:
    """Hash it, apply the hash now and remember it for the next boot. HTTPException when it fails."""
    proc = await asyncio.create_subprocess_exec("openssl", "passwd", "-6", "-stdin", stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(input=password.encode())
    if proc.returncode != 0:
        logger.error(f"openssl hash failed: {err.decode(errors='replace').strip()}")
        raise HTTPException(status_code=500, detail="Failed to hash system password.")
    pwd_hash = out.decode().strip()
    if not await apply_system_password_hash(pwd_hash):
        raise HTTPException(status_code=500, detail="Failed to update system password.")
    logger.info("Password for user 'pi' updated successfully.")
    try:
        write_atomic(PASSWORD_HASH_FILE, pwd_hash, 0o600)
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
