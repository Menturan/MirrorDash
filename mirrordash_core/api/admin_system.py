# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import os
import re
import urllib.request
from pathlib import Path
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import load_config, save_config
from mirrordash_core.system import (
    apply_system_settings,
    get_available_resolutions,
    remount_ro,
    remount_rw,
    run_restart,
    set_screen_power,
)

logger = logging.getLogger("mirrordash.core.api.admin_system")

router = APIRouter()


# ---------------------------------------------------------------------------
# Virtual Environment A/B Swapping Helpers
# ---------------------------------------------------------------------------

def get_venv_paths():
    """Get venv paths: (venv_link, active_path, next_path).
    Returns None if not running on a system with /storage/mirrordash.
    """
    storage_dir = Path("/storage/mirrordash")
    if not storage_dir.exists():
        return None
    venv_link = storage_dir / "venv"
    venv_a = storage_dir / "venv_a"
    venv_b = storage_dir / "venv_b"
    active_path = venv_a
    next_path = venv_b
    if venv_link.exists() and venv_link.is_symlink():
        try:
            target = os.readlink(str(venv_link))
            if "venv_b" in target:
                active_path = venv_b
                next_path = venv_a
        except Exception:
            pass
    return venv_link, active_path, next_path


async def prepare_venv_next(force_clean: bool = False):
    """Clone active venv to next venv and point symlink to next.
    If force_clean is True, starts with a completely clean virtual environment.
    Returns (active_path, next_path) or None.
    """
    import shutil
    paths = get_venv_paths()
    if not paths:
        return None
    venv_link, active_path, next_path = paths
    logger.info(f"Preparing A/B swap: Active={active_path.name}, Next={next_path.name}, Clean={force_clean}")
    try:
        if next_path.exists():
            shutil.rmtree(next_path)
        if active_path.exists() and not force_clean:
            shutil.copytree(active_path, next_path, symlinks=True)
        else:
            next_path.parent.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "uv", "venv", "--python", "3.14", str(next_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
    except Exception as e:
        logger.error(f"Failed to clone/create virtual environment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clone/create virtual environment: {e}")

    # Point symlink to next_path
    tmp_link = venv_link.parent / "venv_tmp"
    if tmp_link.exists() or tmp_link.is_symlink():
        tmp_link.unlink()
    try:
        os.symlink(next_path.name, tmp_link)
        os.replace(tmp_link, venv_link)
    except Exception as e:
        logger.error(f"Failed to swap symlink: {e}")
        if next_path.exists():
            shutil.rmtree(next_path)
        raise HTTPException(status_code=500, detail=f"Failed to update symlink: {e}")
    return active_path, next_path


async def commit_venv_next(active_path, next_path):
    """Confirm the swap, moving the old active path to venv_old."""
    import shutil
    venv_old = active_path.parent / "venv_old"
    logger.info(f"Committing A/B swap: {next_path.name} is now active.")
    try:
        if venv_old.exists():
            shutil.rmtree(venv_old)
        if active_path.exists():
            os.rename(active_path, venv_old)
    except Exception as e:
        logger.warning(f"Failed to move active venv to venv_old: {e}")


async def revert_venv_next(active_path, next_path):
    """Cancel the swap, reverting the symlink and wiping next_path."""
    import shutil
    paths = get_venv_paths()
    if not paths:
        return
    venv_link = paths[0]
    logger.warning(f"Reverting A/B swap to: {active_path.name}")
    tmp_link = venv_link.parent / "venv_tmp"
    try:
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        os.symlink(active_path.name, tmp_link)
        os.replace(tmp_link, venv_link)
        if next_path.exists():
            shutil.rmtree(next_path)
    except Exception as e:
        logger.error(f"Failed to revert symlink: {e}")


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.post("/restart", dependencies=[Depends(require_api_key)])
async def restart_system() -> dict:
    logger.info("System restart requested by admin client.")
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Restarting..."}


@router.get("/core-update-check", dependencies=[Depends(require_api_key)])
async def check_core_update() -> dict:
    """Check PyPI for a newer release of mirrordash-core.

    Returns the currently installed version, the latest version on PyPI,
    and a boolean indicating whether an update is available.
    """
    # Resolve the currently installed version
    current_version = "unknown"
    for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
        try:
            current_version = importlib.metadata.version(pkg_name)
            break
        except importlib.metadata.PackageNotFoundError:
            continue

    # Fetch latest version from PyPI without blocking the event loop
    def _fetch_pypi_version() -> str:
        url = "https://pypi.org/pypi/mirrordash/json"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read())
            return data["info"]["version"]
        except Exception as exc:
            raise RuntimeError(f"PyPI request failed: {exc}") from exc

    try:
        latest_version = await asyncio.to_thread(_fetch_pypi_version)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    def _parse_version(v: str) -> tuple:
        """Return a comparable tuple for a PEP-440-style version string."""
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)

    update_available = (
        current_version != "unknown"
        and _parse_version(latest_version) > _parse_version(current_version)
    )

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "update_available": update_available,
    }


@router.post("/core-update", dependencies=[Depends(require_api_key)])
async def update_core() -> dict:
    """Upgrade mirrordash-core to the latest version from PyPI.

    Uses the same remount-rw / remount-ro guard as the module upgrade endpoint,
    then triggers a server restart on success.
    """
    # Capture current version for logging / potential rollback reference
    current_version = "unknown"
    for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
        try:
            current_version = importlib.metadata.version(pkg_name)
            break
        except importlib.metadata.PackageNotFoundError:
            continue

    swap_info = await prepare_venv_next()
    safe_env = {k: v for k, v in os.environ.items() if k in (
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
    )}

    await remount_rw()
    try:
        logger.info(f"Upgrading mirrordash (current version: {current_version})")
        cmd = ["uv", "pip", "install", "--upgrade"]
        if swap_info:
            active_path, next_path = swap_info
            cmd.extend(["--python", str(Path(next_path) / "bin" / "python")])
        cmd.append("mirrordash")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")
            logger.error(f"mirrordash upgrade failed: {err_msg}")
            if swap_info:
                await revert_venv_next(*swap_info)
            raise HTTPException(status_code=500, detail=f"Upgrade failed: {err_msg}")

        logger.info("mirrordash upgraded successfully. Restarting server...")
        if swap_info:
            await commit_venv_next(*swap_info)
        asyncio.create_task(run_restart())
        return {"status": "success", "message": "Core upgraded successfully. Restarting..."}
    except Exception as e:
        if swap_info:
            await revert_venv_next(*swap_info)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Core upgrade failed: {e}")
    finally:
        await remount_ro()


@router.post("/rebuild-venv", dependencies=[Depends(require_api_key)])
async def rebuild_venv() -> dict:
    """Wipe the current A/B virtual environment and rebuild it from scratch.

    Installs the core package and all local/configured modules, then restarts.
    """
    logger.info("Starting fresh rebuild of the virtual environment...")

    swap_info = await prepare_venv_next(force_clean=True)
    if not swap_info:
        raise HTTPException(
            status_code=500,
            detail="A/B updates are not supported on this filesystem layout (missing /storage/mirrordash)."
        )

    active_path, next_path = swap_info

    safe_env = {k: v for k, v in os.environ.items() if k in (
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
    )}

    await remount_rw()
    try:
        # 1. Install mirrordash
        current_version = "unknown"
        for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
            try:
                current_version = importlib.metadata.version(pkg_name)
                break
            except importlib.metadata.PackageNotFoundError:
                continue

        logger.info(f"Rebuilding venv: installing mirrordash (version: {current_version})")

        install_target = "mirrordash"
        if current_version != "unknown":
            install_target = f"mirrordash=={current_version}"

        cmd_core = ["uv", "pip", "install"]
        if swap_info:
            active_path, next_path = swap_info
            cmd_core.extend(["--python", str(Path(next_path) / "bin" / "python")])
        cmd_core.append(install_target)

        proc = await asyncio.create_subprocess_exec(
            *cmd_core,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")
            logger.error(f"Failed to install mirrordash: {err_msg}")
            await revert_venv_next(*swap_info)
            raise HTTPException(status_code=500, detail=f"Failed to install core: {err_msg}")

        # 2. Find and install local modules
        from mirrordash_core.config import get_base_dir
        base_dir = get_base_dir()
        modules_dir = Path(base_dir) / "modules"
        local_module_names = []
        if modules_dir.exists() and modules_dir.is_dir():
            for folder in modules_dir.iterdir():
                if folder.is_dir() and (folder / "pyproject.toml").exists():
                    logger.info(f"Rebuilding venv: installing local module {folder.name} in editable mode")
                    cmd_local = ["uv", "pip", "install"]
                    if swap_info:
                        cmd_local.extend(["--python", str(Path(next_path) / "bin" / "python")])
                    cmd_local.extend(["-e", str(folder)])

                    proc_local = await asyncio.create_subprocess_exec(
                        *cmd_local,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=safe_env,
                    )
                    await proc_local.communicate()
                    local_module_names.append(folder.name)

        # 3. Find and install configured PyPI modules
        config = load_config()
        configured_modules = config.get("modules", {})
        for mod_name in configured_modules.keys():
            if mod_name not in local_module_names and mod_name != "mirrordash-clock":
                logger.info(f"Rebuilding venv: installing configured PyPI module {mod_name}")
                cmd_pypi = ["uv", "pip", "install"]
                if swap_info:
                    cmd_pypi.extend(["--python", str(Path(next_path) / "bin" / "python")])
                cmd_pypi.append(mod_name)

                proc_pypi = await asyncio.create_subprocess_exec(
                    *cmd_pypi,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=safe_env,
                )
                await proc_pypi.communicate()

        logger.info("Fresh venv rebuild completed successfully. Committing swap and restarting...")
        await commit_venv_next(*swap_info)
        asyncio.create_task(run_restart())
        return {"status": "success", "message": "Environment rebuilt successfully. Restarting..."}
    except Exception as e:
        await revert_venv_next(*swap_info)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {e}")
    finally:
        await remount_ro()


@router.get("/disk-usage", dependencies=[Depends(require_api_key)])
async def get_disk_usage() -> dict:
    """Get persistent storage partition disk space usage."""
    import shutil
    import os
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
            "volume": system_cfg.get("volume", 80),
            "ssh": ssh_active,
            "display_control": system_cfg.get("display_control", {
                "mode": "manual",
                "interval": {"start": "07:00", "end": "22:00"},
                "pir": {"pin": 18, "timeout_minutes": 5},
                "button": {"pin": 23}
            })
        },
        "resolutions": resolutions
    }


@router.post("/system", dependencies=[Depends(require_api_key)])
async def update_system_settings(settings: dict = Body(...)) -> dict:
    config = load_config()
    system_cfg = config.setdefault("system", {})

    rotation = settings.get("rotation", "normal")
    resolution = settings.get("resolution", "auto")
    brightness = settings.get("brightness", 100)
    volume = settings.get("volume", 80)
    ssh_enabled = settings.get("ssh", True)
    display_control = settings.get("display_control", {})

    # Validation
    if rotation not in ("normal", "left", "right", "inverted"):
        raise HTTPException(status_code=400, detail="Invalid rotation value")
    if not isinstance(brightness, int) or brightness < 10 or brightness > 100:
        raise HTTPException(status_code=400, detail="Brightness must be between 10 and 100")
    if not isinstance(volume, int) or volume < 0 or volume > 100:
        raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")

    mode = display_control.get("mode", "manual")
    if mode not in ("manual", "interval", "pir", "button"):
        raise HTTPException(status_code=400, detail="Invalid display power mode")

    if mode == "interval":
        interval = display_control.get("interval", {})
        start = interval.get("start", "07:00")
        end = interval.get("end", "22:00")
        if not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
            raise HTTPException(status_code=400, detail="Invalid interval time format (HH:MM)")
    elif mode == "pir":
        pir = display_control.get("pir", {})
        pin = pir.get("pin", 18)
        timeout = pir.get("timeout_minutes", 5)
        if not isinstance(pin, int) or pin < 1 or pin > 40:
            raise HTTPException(status_code=400, detail="Invalid PIR GPIO pin")
        if not isinstance(timeout, int) or timeout < 1:
            raise HTTPException(status_code=400, detail="Invalid PIR timeout")
    elif mode == "button":
        btn = display_control.get("button", {})
        pin = btn.get("pin", 23)
        if not isinstance(pin, int) or pin < 1 or pin > 40:
            raise HTTPException(status_code=400, detail="Invalid Button GPIO pin")

    system_cfg["rotation"] = rotation
    system_cfg["resolution"] = resolution
    system_cfg["brightness"] = brightness
    system_cfg["volume"] = volume
    system_cfg["display_control"] = display_control
    system_cfg["ssh"] = ssh_enabled

    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()

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
                    "sudo", "chpasswd",
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
                await remount_rw()
                try:
                    hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
                    with open(hash_path, "w", encoding="utf-8") as f:
                        f.write(pwd_hash)
                    os.chmod(hash_path, 0o600)
                except Exception as io_err:
                    logger.error(f"Failed to write password hash to disk: {io_err}")
                finally:
                    await remount_ro()

            except HTTPException:
                raise
            except Exception as exc:
                logger.error(f"Unexpected error running chpasswd: {exc}")
                raise HTTPException(status_code=500, detail="Unexpected error updating system password.")
    else:
        # Delete persistent password hash if SSH is disabled
        await remount_rw()
        try:
            hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
            if os.path.exists(hash_path):
                os.remove(hash_path)
        except Exception as io_err:
            logger.error(f"Failed to remove password hash: {io_err}")
        finally:
            await remount_ro()

    await set_ssh_status(ssh_enabled)

    # Queue settings to apply asynchronously
    asyncio.create_task(apply_system_settings(rotation, resolution, brightness, volume))

    return {"status": "success", "message": "System settings saved and applied successfully"}


@router.post("/screen")
async def update_screen_state(body: dict = Body(...)) -> dict:
    state = body.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state value. Must be 'on' or 'off'")

    from mirrordash_core.display_power import display_power_manager
    asyncio.create_task(display_power_manager.set_state(state == "on"))

    return {"status": "success", "message": f"Screen power command to turn '{state}' queued successfully"}



