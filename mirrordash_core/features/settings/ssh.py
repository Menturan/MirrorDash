# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
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
