# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import json
import logging
import os
import urllib.request
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from mirrordash_core.admin import require_api_key
from mirrordash_core.config import get_base_dir, load_config, get_core_version, version_key
from mirrordash_core.venv import get_venv_paths, run, uv_pip, venv_swap
from mirrordash_core.host import run_restart

logger = logging.getLogger("mirrordash.core.updates")
router = APIRouter(prefix="/admin")


@router.post("/restart", dependencies=[Depends(require_api_key)])
async def restart_system() -> dict:
    logger.info("System restart requested by admin client.")
    from mirrordash_core.admin import BOOT_ID
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
