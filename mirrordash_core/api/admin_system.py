# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import os
import re
import sys
import urllib.request
from pathlib import Path
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import get_base_dir, load_config, save_config, get_core_version, version_key
from mirrordash_core.venv import get_venv_paths, run, uv_pip, venv_swap
from mirrordash_core.system import display
from mirrordash_core.system import (
    apply_brightness,
    apply_system_settings,
    get_available_resolutions,
    run_restart,
    set_screen_power,
)

logger = logging.getLogger("mirrordash.core.api.admin_system")

router = APIRouter()


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.post("/restart", dependencies=[Depends(require_api_key)])
async def restart_system() -> dict:
    logger.info("System restart requested by admin client.")
    from mirrordash_core.api.admin_shared import BOOT_ID
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Restarting...", "boot_id": BOOT_ID}


def prerelease_enabled() -> bool:
    """Whether this mirror opted in to test versions (pre-releases) of MirrorDash."""
    return load_config().get("system", {}).get("prerelease", False) is True


@router.get("/core-update-check", dependencies=[Depends(require_api_key)])
async def check_core_update() -> dict:
    """Check PyPI for a newer release of mirrordash-core.

    Returns the currently installed version, the latest version on PyPI,
    and a boolean indicating whether an update is available.
    """
    # Resolve the currently installed version
    current_version = get_core_version()

    # Fetch latest version from PyPI without blocking the event loop
    def _fetch_pypi_version(prerelease: bool) -> str:
        url = "https://pypi.org/pypi/mirrordash/json"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(f"PyPI request failed: {exc}") from exc
        if not prerelease:
            return data["info"]["version"]  # PyPI's latest is always a final release
        return max((v for v, files in data["releases"].items() if files), key=version_key)

    try:
        latest_version = await asyncio.to_thread(_fetch_pypi_version, prerelease_enabled())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    update_available = (
        current_version != "unknown"
        and version_key(latest_version) > version_key(current_version)
    )

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
    }


@router.post("/core-update", dependencies=[Depends(require_api_key)])
async def update_core() -> dict:
    """Install the latest MirrorDash into the next A/B venv, then restart into it."""
    current_version = get_core_version()
    logger.info(f"Upgrading mirrordash (current version: {current_version})")
    try:
        async with venv_swap() as python:
            # --refresh-package: ask PyPI again instead of trusting uv's cached index (PyPI lets it be
            # cached for up to 10 minutes), or a version published moments ago isn't seen yet.
            args = ["--upgrade", "--refresh-package", "mirrordash"]
            if prerelease_enabled():
                args.append("--prerelease=allow")
            code, _, err = await uv_pip(python, "install", *args, "mirrordash")
            if code != 0:
                logger.error(f"mirrordash upgrade failed: {err}")
                raise HTTPException(status_code=500, detail=f"Upgrade failed: {err}")
            # uv succeeds when there is nothing newer to install, too: only restart for a new version
            _, installed, _ = await run(python, "-c", "import importlib.metadata as m; print(m.version('mirrordash'))")
            installed = installed.strip()
            if version_key(installed) <= version_key(current_version):
                logger.warning(f"mirrordash upgrade installed {installed or 'nothing'}, not newer than {current_version}")
                raise HTTPException(status_code=409, detail="The new version isn't available yet. Try again in a few minutes.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Core upgrade failed: {e}")
    logger.info(f"mirrordash upgraded to {installed}. Restarting server...")
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Core upgraded successfully. Restarting..."}


@router.post("/rebuild-venv", dependencies=[Depends(require_api_key)])
async def rebuild_venv() -> dict:
    """Build a fresh venv with this MirrorDash version, the local modules and the configured ones."""
    if not get_venv_paths():
        raise HTTPException(status_code=500, detail="A/B updates are not supported on this filesystem layout (missing /storage/mirrordash).")
    current_version = get_core_version()
    logger.info(f"Rebuilding venv: installing mirrordash (version: {current_version})")
    try:
        async with venv_swap(force_clean=True) as python:
            code, _, err = await uv_pip(python, "install", "mirrordash" if current_version == "unknown" else f"mirrordash=={current_version}")
            if code != 0:
                logger.error(f"Failed to install mirrordash: {err}")
                raise HTTPException(status_code=500, detail=f"Failed to install core: {err}")
            modules_dir = Path(get_base_dir()) / "modules"
            local = [d for d in (modules_dir.iterdir() if modules_dir.is_dir() else []) if (d / "pyproject.toml").exists()]
            for folder in local:
                logger.info(f"Rebuilding venv: installing local module {folder.name} in editable mode")
                await uv_pip(python, "install", "-e", str(folder))
            local_names = {d.name for d in local}
            for name in load_config().get("modules", {}):
                if name not in local_names and name != "mirrordash-clock":
                    logger.info(f"Rebuilding venv: installing configured PyPI module {name}")
                    await uv_pip(python, "install", name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {e}")
    logger.info("Fresh venv rebuild completed successfully. Restarting...")
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Environment rebuilt successfully. Restarting..."}


@router.get("/disk-usage", dependencies=[Depends(require_api_key)])
async def get_disk_usage() -> dict:
    """Get persistent storage partition disk space usage."""
    import shutil
    try:
        check_path = "/storage" if os.path.ismount("/storage") or os.path.exists("/storage") else "/"
        total, used, free = shutil.disk_usage(check_path)
        percent = round((used / total) * 100, 1) if total > 0 else 0.0
        return {
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "percent_used": percent
        }
    except Exception as e:
        logger.error(f"Failed to retrieve disk usage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to retrieve disk usage: {str(e)}")


@router.get("/system", dependencies=[Depends(require_api_key)])
async def get_system_settings() -> dict:
    config = load_config()
    system_cfg = config.get("system", {})
    resolutions = await get_available_resolutions()
    from mirrordash_core.system import get_ssh_status
    ssh_active = await get_ssh_status()
    return {
        "settings": {
            "rotation": system_cfg.get("rotation", "normal"),
            "resolution": system_cfg.get("resolution", "auto"),
            "brightness": system_cfg.get("brightness", 100),
            "brightness_supported": display.brightness_supported is not False,
            "volume": system_cfg.get("volume", 80),
            "ssh": ssh_active,
            "prerelease": system_cfg.get("prerelease", False),
            "display_control": system_cfg.get("display_control", {
                "mode": "manual",
                "interval": {"start": "07:00", "end": "22:00"},
            }),
        },
        "resolutions": resolutions
    }


@router.post("/system", dependencies=[Depends(require_api_key)])
async def update_system_settings(settings: dict = Body(...)) -> dict:
    config = load_config()
    system_cfg = config.setdefault("system", {})

    # Use existing values as defaults if not provided in the incoming request
    rotation = settings.get("rotation", system_cfg.get("rotation", "normal"))
    resolution = settings.get("resolution", system_cfg.get("resolution", "auto"))
    brightness = settings.get("brightness", system_cfg.get("brightness", 100))
    volume = settings.get("volume", system_cfg.get("volume", 80))
    ssh_enabled = settings.get("ssh", system_cfg.get("ssh", True))
    prerelease = settings.get("prerelease", system_cfg.get("prerelease", False))
    
    # Merge display_control securely
    from mirrordash_core.display_power import DEFAULT_WAKE
    current_dc = system_cfg.get("display_control", {
        "mode": "manual",
        "interval": {"start": "07:00", "end": "22:00"},
        "wake": dict(DEFAULT_WAKE),
    })
    incoming_dc = settings.get("display_control", {})
    
    display_control = dict(current_dc)
    if incoming_dc:
        if "mode" in incoming_dc:
            display_control["mode"] = incoming_dc["mode"]
        if "interval" in incoming_dc:
            display_control["interval"] = {**display_control.get("interval", {}), **incoming_dc["interval"]}
        if "wake" in incoming_dc:
            display_control["wake"] = {**display_control.get("wake", {}), **incoming_dc["wake"]}

    # Validation
    if rotation not in ("normal", "left", "right", "inverted"):
        raise HTTPException(status_code=400, detail="Invalid rotation value")
    if not isinstance(brightness, int) or brightness < 10 or brightness > 100:
        raise HTTPException(status_code=400, detail="Brightness must be between 10 and 100")
    if not isinstance(volume, int) or volume < 0 or volume > 100:
        raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")

    mode = display_control.get("mode", "manual")
    if mode not in ("manual", "interval", "wake"):
        raise HTTPException(status_code=400, detail="Invalid display power mode")
    wake = display_control.get("wake", {})
    timeout = wake.get("timeout_minutes", DEFAULT_WAKE["timeout_minutes"])
    if not isinstance(timeout, int) or not 1 <= timeout <= 1440:
        raise HTTPException(status_code=400, detail="The screen timeout must be 1 to 1440 minutes")
    if not all(isinstance(wake.get(k, True), bool) for k in ("extend", "presence")):
        raise HTTPException(status_code=400, detail="Invalid wake settings")
    if not isinstance(prerelease, bool):
        raise HTTPException(status_code=400, detail="Test versions must be on or off")

    if mode == "interval":
        interval = display_control.get("interval", {})
        start = interval.get("start", "07:00")
        end = interval.get("end", "22:00")
        if not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
            raise HTTPException(status_code=400, detail="Invalid interval time format (HH:MM)")

    system_cfg["rotation"] = rotation
    system_cfg["resolution"] = resolution
    system_cfg["brightness"] = brightness
    system_cfg["volume"] = volume
    system_cfg["display_control"] = display_control
    system_cfg["ssh"] = ssh_enabled
    system_cfg["prerelease"] = prerelease

    # Only touch SSH when this request is about it: the Power tab saves just the display
    # schedule, and re-checking SSH there failed whenever the service state differed.
    if "ssh" in settings:
        # Apply SSH state; if enabling SSH, require and apply new password for pi user
        from mirrordash_core.system import set_ssh_status, get_ssh_status
        current_ssh_active = await get_ssh_status()
        if ssh_enabled:
            if not current_ssh_active:
                pi_password = settings.get("pi_password")
                if not pi_password or len(pi_password) < 8:
                    raise HTTPException(
                        status_code=400,
                        detail="A password of at least 8 characters is required to enable SSH."
                    )
                # Update the pi user's password using chpasswd
                try:
                    chpasswd_input = f"pi:{pi_password}\n".encode()
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "-n", "chpasswd",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate(input=chpasswd_input)
                    if proc.returncode != 0:
                        err_msg = stderr.decode(errors="replace").strip()
                        logger.error(f"chpasswd failed: {err_msg}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to update system password: {err_msg}"
                        )
                    logger.info("Password for user 'pi' updated successfully.")

                    # Generate secure SHA-512 crypt hash using openssl
                    proc_hash = await asyncio.create_subprocess_exec(
                        "openssl", "passwd", "-6", "-stdin",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_hash, stderr_hash = await proc_hash.communicate(input=pi_password.encode())
                    if proc_hash.returncode != 0:
                        err_msg = stderr_hash.decode(errors="replace").strip()
                        logger.error(f"openssl hash failed: {err_msg}")
                        raise HTTPException(status_code=500, detail="Failed to hash system password.")
                    pwd_hash = stdout_hash.decode().strip()

                    # Save password hash persistently
                    try:
                        hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
                        with open(hash_path, "w", encoding="utf-8") as f:
                            f.write(pwd_hash)
                        os.chmod(hash_path, 0o600)
                    except Exception as io_err:
                        logger.error(f"Failed to write password hash to disk: {io_err}")

                except HTTPException:
                    raise
                except Exception as exc:
                    logger.error(f"Unexpected error running chpasswd: {exc}")
                    raise HTTPException(status_code=500, detail="Unexpected error updating system password.")
        else:
            # Delete persistent password hash if SSH is disabled
            try:
                hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
                if os.path.exists(hash_path):
                    os.remove(hash_path)
            except Exception as io_err:
                logger.error(f"Failed to remove password hash: {io_err}")

        await set_ssh_status(ssh_enabled)

    # Saved only now: a refused SSH password must not leave "ssh: on" in the config
    save_config(config)

    # Queue display/audio settings to apply asynchronously (re-applying an unchanged
    # rotation/resolution can make the screen flicker, so only when they were sent)
    if any(k in settings for k in ("rotation", "resolution", "volume")):
        asyncio.create_task(apply_system_settings(rotation, resolution, brightness, volume))
    elif "brightness" in settings:  # the slider, or Home Assistant: only the brightness
        asyncio.create_task(apply_brightness(brightness))

    return {"status": "success", "message": "System settings saved and applied successfully"}


@router.post("/screen")
async def update_screen_state(body: dict = Body(...)) -> dict:
    """Open endpoint (e.g. for Home Assistant). {"state": "on"} wakes the screen for the
    configured screen timeout, or for "timeout_minutes" if given; {"state": "off"} turns it off."""
    state = body.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state value. Must be 'on' or 'off'")
    timeout = body.get("timeout_minutes")
    if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 1440):
        raise HTTPException(status_code=400, detail="timeout_minutes must be a number from 1 to 1440")

    from mirrordash_core.display_power import display_power_manager
    if state == "on":
        display_power_manager.wake(timeout)
    else:
        display_power_manager.turn_off()

    return {"status": "success", "message": f"Screen turned {state}"}



