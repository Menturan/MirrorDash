# SPDX-License-Identifier: AGPL-3.0-only
"""The A/B virtual environments on /storage, and running uv in them.

Every change of the core's packages (a MirrorDash update, a rebuild) goes into the *next* environment while the active one keeps running; `venv_swap` switches the link,
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
# A package change needs room for a copy of the venv (~50 MB), what it installs and uv's cache, and a
# new Python another ~90 MB. ponytail: one fixed margin, not an estimate per change.
MIN_FREE_BYTES = 300 * 1024 * 1024
# Strip the environment so no server secret reaches a subprocess
SAFE_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV", "UV_PYTHON_INSTALL_DIR")


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


def point_link(venv_link: Path, target: Path) -> None:
    """Atomically point the link (the venv's, or the module site's) at target, a sibling directory."""
    tmp_link = venv_link.with_name(venv_link.name + "_tmp")
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    os.symlink(target.name, tmp_link)
    os.replace(tmp_link, venv_link)


def running_python() -> str:
    """This process's Python version, e.g. "3.14": what a new venv gets unless told otherwise."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


async def prepare_venv_next(force_clean: bool = False, python: str | None = None):
    """Copy the active venv to the next one (or create it empty, with `python`, default the running
    one) and point the link at it. Returns (active_path, next_path), or None when not on a mirror."""
    paths = get_venv_paths()
    if not paths:
        return None
    venv_link, active_path, next_path = paths
    # A full card mid-install leaves a half-built venv and can stop settings from being saved
    # Copying and deleting a venv takes tens of seconds on a Pi's card: in a thread, so the screen
    # and the admin page keep their connection meanwhile
    free = shutil.disk_usage(STORAGE_DIR).free + (await asyncio.to_thread(_size, next_path) if next_path.exists() else 0)
    if free < MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail=f"Not enough free space on the mirror's storage "
                                                    f"({free // 1048576} MB free, {MIN_FREE_BYTES // 1048576} MB needed). "
                                                    "Remove a module or a saved backup first.")
    logger.info(f"Preparing A/B swap: Active={active_path.name}, Next={next_path.name}, Clean={force_clean}")
    try:
        if next_path.exists():
            await asyncio.to_thread(shutil.rmtree, next_path)
        if active_path.exists() and not force_clean:
            await asyncio.to_thread(shutil.copytree, active_path, next_path, symlinks=True)
        else:
            next_path.parent.mkdir(parents=True, exist_ok=True)
            code, _, err = await run("uv", "venv", "--python", python or running_python(), str(next_path))
            if code != 0:
                raise RuntimeError(err.strip() or "uv venv failed")
    except Exception as e:
        logger.error(f"Failed to clone/create virtual environment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clone/create virtual environment: {e}")
    try:
        point_link(venv_link, next_path)
    except Exception as e:
        logger.error(f"Failed to swap symlink: {e}")
        await asyncio.to_thread(shutil.rmtree, next_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to update symlink: {e}")
    return active_path, next_path


def _size(path: Path) -> int:
    """Bytes in a folder: an old next venv is deleted first, so its space counts as free."""
    return sum(f.stat(follow_symlinks=False).st_size for f in path.rglob("*") if not f.is_symlink())


async def commit_venv_next(active_path: Path, next_path: Path) -> None:
    """Keep the swap: the old active venv becomes venv_old (the launcher's fallback)."""
    venv_old = active_path.parent / "venv_old"
    logger.info(f"Committing A/B swap: {next_path.name} is now active.")
    try:
        if venv_old.exists():
            await asyncio.to_thread(shutil.rmtree, venv_old)
        if active_path.exists():
            os.rename(active_path, venv_old)
    except Exception as e:
        logger.warning(f"Failed to move active venv to venv_old: {e}")
    await asyncio.to_thread(remove_unused_pythons)


def remove_unused_pythons() -> None:
    """Delete the Pythons on /storage that no venv the launcher can start uses any more (venv_a,
    venv_b, and venv_old, its fallback), so each Python upgrade doesn't leave ~90 MB behind.
    venv_failed needs none: only its files are read (Safe mode's Reinstall). Only on a mirror, where
    UV_PYTHON_INSTALL_DIR is set (mirrordash.service): never a developer's own uv Pythons."""
    py_dir = os.environ.get("UV_PYTHON_INSTALL_DIR")
    if not py_dir or not Path(py_dir).is_dir():
        return
    py_dir = Path(py_dir)
    keep = set()
    for name in ("venv_a", "venv_b", "venv_old"):
        try:
            cfg = (STORAGE_DIR / name / "pyvenv.cfg").read_text()
        except OSError:
            continue
        home = next((line.split("=", 1)[1].strip() for line in cfg.splitlines() if line.startswith("home")), "")
        # home is uv's minor link (cpython-3.14-...) /bin: keep the link and the folder it points to
        if home.startswith(f"{py_dir}/"):
            link = Path(home).parent
            keep |= {link.name, link.resolve().name}
    if not keep:
        return  # nothing reads as a venv of ours: never empty the folder on a guess
    for entry in py_dir.iterdir():
        if entry.name.startswith(".") or entry.name in keep:
            continue  # uv's own .lock, .temp
        logger.info(f"Removing Python no venv uses any more: {entry.name}")
        try:
            entry.unlink() if entry.is_symlink() or not entry.is_dir() else shutil.rmtree(entry)
        except OSError as e:
            logger.warning(f"Could not remove {entry}: {e}")


async def revert_venv_next(active_path: Path, next_path: Path) -> None:
    """Undo the swap: point the link back at the active venv and delete the next one."""
    logger.warning(f"Reverting A/B swap to: {active_path.name}")
    try:
        point_link(active_path.parent / "venv", active_path)
        await asyncio.to_thread(shutil.rmtree, next_path, ignore_errors=True)
    except Exception as e:
        logger.error(f"Failed to revert symlink: {e}")


@asynccontextmanager
async def venv_swap(force_clean: bool = False, python: str | None = None):
    """Yield the Python to install into: the next A/B venv on a mirror, this process's elsewhere.
    The swap is kept when the block finishes and undone when it raises. python: the version of an
    empty venv (force_clean), e.g. a newer one a MirrorDash update needs."""
    swap = await prepare_venv_next(force_clean=force_clean, python=python)
    try:
        yield str(swap[1] / "bin" / "python") if swap else sys.executable
    except BaseException:
        if swap:
            await revert_venv_next(*swap)
        raise
    if swap:
        await commit_venv_next(*swap)


async def run(*cmd: str, timeout: float | None = None) -> tuple[int, str, str]:
    """Run a command with the stripped environment: (returncode, stdout, stderr). After timeout
    seconds it is stopped and returns 124, like timeout(1)."""
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, env=env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"took more than {timeout:.0f} s"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


async def uv_pip(python: str, command: str, *args: str, timeout: float | None = None) -> tuple[int, str, str]:
    """`uv pip <command> --python <python> <args>`."""
    return await run("uv", "pip", command, "--python", python, *args, timeout=timeout)
