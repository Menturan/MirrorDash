# SPDX-License-Identifier: AGPL-3.0-only
"""The Matter node (ARCHITECTURE #35): started with a token of its own, stopped, and made a new device."""

import json
import logging
import secrets
import shutil

from mirrordash_core.admin import token_hash
from mirrordash_core.config import get_base_dir, load_config, save_config, write_atomic
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.matter")

# Next to data/, not in it: a backup carries neither the node's keys nor its token
MATTER_DIR = get_base_dir() / "matter"  # the node's own storage (fabrics, keys) and pairing.json
ENV_FILE = get_base_dir() / "matter.env"
# The spec rules these out (Matter Core 5.1.7.1)
FORBIDDEN_PASSCODES = {0, 11111111, 22222222, 33333333, 44444444, 55555555, 66666666, 77777777,
                       88888888, 99999999, 12345678, 87654321}


async def systemctl(action: str) -> bool:
    """`sudo systemctl <action> mirrordash-matter`: whether it worked."""
    exit_code, _, error_output = await run_command("sudo", "-n", "systemctl", action, "mirrordash-matter", timeout=30)
    if exit_code != 0:
        logger.error(f"systemctl {action} mirrordash-matter failed: {error_output.strip()}")
    return exit_code == 0


async def start_matter() -> bool:
    """Start the node, with a new token every time: only its hash is kept, so nothing has to survive
    a restart or a restore. The pairing codes are made once per mirror."""
    token = secrets.token_urlsafe(32)
    config = load_config()
    matter = config.setdefault("matter", {})
    if "passcode" not in matter:
        while (passcode := secrets.randbelow(99999998) + 1) in FORBIDDEN_PASSCODES:
            pass
        matter.update(passcode=passcode, discriminator=secrets.randbelow(4096))
    # The node's serial number and UniqueID: two mirrors in one home must not look like one device
    matter.setdefault("unique_id", f"mirrordash-{secrets.token_hex(4)}")
    matter.update(enabled=True, token_hash=token_hash(token))
    save_config(config)
    MATTER_DIR.mkdir(parents=True, exist_ok=True)
    write_atomic(ENV_FILE, f"MIRRORDASH_TOKEN={token}\nMATTER_PASSCODE={matter['passcode']}\n"
                           f"MATTER_DISCRIMINATOR={matter['discriminator']}\nMATTER_UNIQUE_ID={matter['unique_id']}\n"
                           f"MATTER_STORAGE_PATH={MATTER_DIR}\n", 0o600)
    return await systemctl("restart")  # restart: a running node picks up the new token


async def stop_matter() -> None:
    await systemctl("stop")
    config = load_config()
    matter = config.setdefault("matter", {})
    matter["enabled"] = False
    matter.pop("token_hash", None)
    save_config(config)


async def forget_homes() -> None:
    """Forget every home the mirror is paired with; the next start offers it for pairing again."""
    await systemctl("stop")
    shutil.rmtree(MATTER_DIR, ignore_errors=True)
    config = load_config()
    config.setdefault("matter", {}).pop("unique_id", None)  # the spec asks for a new UniqueID after a factory reset
    save_config(config)


def pairing() -> tuple[bool, dict]:
    """(whether Matter is on, the node's pairing codes: {} until the node has written them)."""
    enabled = load_config().get("matter", {}).get("enabled", False)
    try:
        return enabled, json.loads((MATTER_DIR / "pairing.json").read_text(encoding="utf-8")) if enabled else {}
    except (OSError, ValueError):
        return enabled, {}  # the node hasn't written it yet: the card asks again
