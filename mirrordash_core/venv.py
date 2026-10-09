# Licensed under the PolyForm Noncommercial License 1.0.0.
"""The A/B virtual environments on /storage, and running uv in them.

Every package change (a module install, update or uninstall, a MirrorDash update, a rebuild) goes
into the *next* environment while the active one keeps running; `venv_swap` switches the link,
and keeps the previous environment as venv_old, only when the change worked."""

import asyncio
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger("mirrordash.core.venv")

STORAGE_DIR = Path("/storage/mirrordash")
# Strip the environment so no server secret reaches a subprocess
SAFE_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV")


def get_venv_paths():
    """(venv_link, active_path, next_path), or None when not on a mirror (no /storage/mirrordash)."""
    if not STORAGE_DIR.exists():
        return None
    venv_link, venv_a, venv_b = STORAGE_DIR / "venv", STORAGE_DIR / "venv_a", STORAGE_DIR / "venv_b"
    try:
        if venv_link.is_symlink() and "venv_b" in os.readlink(venv_link):
            return venv_link, venv_b, venv_a
    except OSError:
        pass
    return venv_link, venv_a, venv_b


def _point_link(venv_link: Path, target: Path) -> None:
    """Atomically point the venv link at target (a sibling directory)."""
    tmp_link = venv_link.parent / "venv_tmp"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    os.symlink(target.name, tmp_link)
    os.replace(tmp_link, venv_link)


async def prepare_venv_next(force_clean: bool = False):
    """Copy the active venv to the next one (or create it empty) and point the link at it.
    Returns (active_path, next_path), or None when not on a mirror."""
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
            await run("uv", "venv", "--python", "3.14", str(next_path))
    except Exception as e:
        logger.error(f"Failed to clone/create virtual environment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clone/create virtual environment: {e}")
    try:
        _point_link(venv_link, next_path)
    except Exception as e:
        logger.error(f"Failed to swap symlink: {e}")
        shutil.rmtree(next_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to update symlink: {e}")
    return active_path, next_path


async def commit_venv_next(active_path: Path, next_path: Path) -> None:
    """Keep the swap: the old active venv becomes venv_old (the launcher's fallback)."""
    venv_old = active_path.parent / "venv_old"
    logger.info(f"Committing A/B swap: {next_path.name} is now active.")
    try:
        if venv_old.exists():
            shutil.rmtree(venv_old)
        if active_path.exists():
            os.rename(active_path, venv_old)
    except Exception as e:
        logger.warning(f"Failed to move active venv to venv_old: {e}")


async def revert_venv_next(active_path: Path, next_path: Path) -> None:
    """Undo the swap: point the link back at the active venv and delete the next one."""
    logger.warning(f"Reverting A/B swap to: {active_path.name}")
    try:
        _point_link(active_path.parent / "venv", active_path)
        shutil.rmtree(next_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Failed to revert symlink: {e}")


@asynccontextmanager
async def venv_swap(force_clean: bool = False):
    """Yield the Python to install into: the next A/B venv on a mirror, this process's elsewhere.
    The swap is kept when the block finishes and undone when it raises."""
    swap = await prepare_venv_next(force_clean=force_clean)
    try:
        yield str(swap[1] / "bin" / "python") if swap else sys.executable
    except BaseException:
        if swap:
            await revert_venv_next(*swap)
        raise
    if swap:
        await commit_venv_next(*swap)


async def run(*cmd: str) -> tuple[int, str, str]:
    """Run a command with the stripped environment: (returncode, stdout, stderr)."""
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, env=env)
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def uv_pip(python: str, command: str, *args: str) -> tuple[int, str, str]:
    """`uv pip <command> --python <python> <args>`."""
    return await run("uv", "pip", command, "--python", python, *args)


async def clean_uv_cache() -> None:
    """Free the space a package change left in uv's cache (/storage is small)."""
    try:
        await run("uv", "cache", "clean")
    except Exception as e:
        logger.warning(f"Failed to clean uv cache: {e}")
