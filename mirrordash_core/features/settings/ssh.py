# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import os

from fastapi import HTTPException
import logging

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
    """Enable/start or disable/stop the SSH service."""
    try:
        action = "enable" if enabled else "disable"
        proc1 = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", action, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc1.wait()

        start_stop = "start" if enabled else "stop"
        proc2 = await asyncio.create_subprocess_exec(
            "sudo", "-n", "systemctl", start_stop, "ssh",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc2.wait()

        return True
    except Exception as e:
        logger.error(f"Failed to change SSH status: {e}")
        return False


# The pi user's password, as a crypt hash on /storage: re-applied at boot, since /etc is a RAM overlay
PASSWORD_HASH_FILE = "/home/pi/.mirrordash/data/pi_password.hash"


async def _set_pi_password(password: str) -> None:
    """chpasswd now, and remember the hash for the next boot. HTTPException when it fails."""
    proc = await asyncio.create_subprocess_exec("sudo", "-n", "chpasswd", stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate(input=f"pi:{password}\n".encode())
    if proc.returncode != 0:
        err = err.decode(errors="replace").strip()
        logger.error(f"chpasswd failed: {err}")
        raise HTTPException(status_code=500, detail=f"Failed to update system password: {err}")
    logger.info("Password for user 'pi' updated successfully.")
    proc = await asyncio.create_subprocess_exec("openssl", "passwd", "-6", "-stdin", stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate(input=password.encode())
    if proc.returncode != 0:
        logger.error(f"openssl hash failed: {err.decode(errors='replace').strip()}")
        raise HTTPException(status_code=500, detail="Failed to hash system password.")
    try:
        with open(PASSWORD_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(out.decode().strip())
        os.chmod(PASSWORD_HASH_FILE, 0o600)
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
    await set_ssh_status(enabled)
