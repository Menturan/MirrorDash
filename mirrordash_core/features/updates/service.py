# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from mirrordash_core.admin import require_api_key
from mirrordash_core.config import get_core_version, load_config, version_key
from mirrordash_core.features.logs.saved import save_log
from mirrordash_core.host import reboot_system, run_restart
from mirrordash_core.venv import get_venv_paths, run, running_python, uv_pip, venv_swap

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


async def check_core_update() -> dict:
    """Check PyPI for a newer release of mirrordash-core.

    Returns the currently installed version, the latest version on PyPI,
    and a boolean indicating whether an update is available.
    """
    # Resolve the currently installed version
    current_version = get_core_version()

    # Fetch latest version from PyPI without blocking the event loop, and the Python it needs
    def _fetch_pypi_version(prerelease: bool) -> tuple[str, str | None]:
        url = "https://pypi.org/pypi/mirrordash/json"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(f"PyPI request failed: {exc}") from exc
        if not prerelease:
            # PyPI's latest is always a final release
            return data["info"]["version"], data["info"].get("requires_python")
        version = max((v for v, files in data["releases"].items() if files), key=version_key)
        return version, data["releases"][version][0].get("requires_python")

    try:
        latest_version, requires_python = await asyncio.to_thread(_fetch_pypi_version, prerelease_enabled())
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
        "needs_python": needed_python(requires_python),
    }


def needed_python(requires_python: str | None) -> str | None:
    """The newer Python a release needs ("3.15"), from PyPI's requires_python, or None when the
    running one will do. ponytail: reads only the ">=3.N" part, all our pyproject has ever used."""
    match = re.search(r">=\s*3\.(\d+)", requires_python or "")
    return f"3.{match[1]}" if match and int(match[1]) > sys.version_info.minor else None


# The last update to a newer Python that some modules stopped (see _update_to_new_python), for the
# update check to offer "Install anyway": {"version", "python", "modules": {name: reason}}. In memory:
# a restart forgets it, and the next attempt finds the same again.
incompatible: dict = {}

def module_target(mod: dict) -> list[str]:
    """What `uv pip install` takes to reinstall a module from where it came (installed_modules)."""
    from mirrordash_core.features.backup.service import find_local_module_dir
    local_dir = find_local_module_dir(mod["package_name"]) if mod["type"] == "local" else None
    return ["-e", str(local_dir)] if local_dir else [mod.get("source") or f"{mod['package_name']}=={mod['version']}"]


async def _update_to_new_python(python: str, version: str, skip: tuple[str, ...]) -> dict:
    """A MirrorDash that needs a newer Python (ARCHITECTURE #16). The Python goes next to the running
    one on /storage, an empty venv gets the new MirrorDash, an empty module site every module built for
    it, and each module is loaded once from it (by the module host, in its sandbox: ARCHITECTURE #41).
    Only when all of them work, or the admin chose to turn the ones that don't off (skip), are both
    switched in; otherwise nothing has changed."""
    from mirrordash_core.features.backup.service import installed_modules
    from mirrordash_core.features.modules.client import module_host
    code, _, err = await run("uv", "python", "install", python, timeout=900)
    if code != 0:
        raise HTTPException(status_code=500, detail=f"Installing Python {python} failed: {err.strip()}")
    modules = [m for m in installed_modules() if m["name"] not in skip]  # from the running module site
    args = ["--prerelease=allow"] if prerelease_enabled() else []
    try:
        # The site is switched in with the venv; launch.sh's fallback to venv_old switches back to the old one
        async with venv_swap(force_clean=True, python=python) as py, module_host.packages(py, fresh=True) as site:
            code, _, err = await uv_pip(py, "install", *args, f"mirrordash=={version}")
            if code != 0:
                raise HTTPException(status_code=500, detail=f"Upgrade failed: {err.strip()}")
            failed = {}
            for mod in modules:
                # ponytail: 10 minutes each, so a module that builds C code on the Pi can't hang the update
                code, err = await site.pip("install", *module_target(mod), timeout=600)
                if code != 0 and mod["type"] == "pypi":
                    # Its newest version may have been made for the new Python already
                    code, err = await site.pip("install", mod["package_name"], timeout=600)
                if code == 0:
                    code, err = await site.check([mod["name"]])
                if code != 0:
                    failed[mod["name"]] = (err.strip().splitlines() or ["unknown error"])[-1]
            if failed:
                incompatible.update(version=version, python=python, modules=failed)
                names = ", ".join(failed)
                raise HTTPException(status_code=409, detail=(
                    f"MirrorDash v{version} needs Python {python}, and {names} doesn't work with it, so nothing "
                    "was changed. Check for updates again to install it anyway with those modules turned off."))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Core upgrade failed: {e}")
    if skip:
        turn_off_modules(skip, f"doesn't work with Python {python}")
    incompatible.clear()
    logger.info(f"mirrordash upgraded to {version} on Python {python}. Restarting server...")
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Core upgraded successfully. Restarting..."}


def turn_off_modules(names: tuple[str, ...], reason: str) -> None:
    """Turn off every instance of these modules (entry point names), keeping their settings."""
    from mirrordash_core.config import save_config
    norm = {n.replace("-", "_") for n in names}
    config = load_config()
    for instance in config.get("modules", {}).values():
        if isinstance(instance, dict) and str(instance.get("module", "")).replace("-", "_") in norm:
            instance.update(enabled=False, disabled_reason=reason)
    save_config(config)


async def update_core(skip: tuple[str, ...] = ()) -> dict:
    """Install the latest MirrorDash into the next A/B venv, then restart into it. skip: modules to
    turn off when the new version needs a newer Python they don't work with."""
    info = await check_core_update()
    if info.get("needs_python") and info["update_available"]:
        return await _update_to_new_python(info["needs_python"], info["latest_version"], skip)
    current_version = get_core_version()
    if os.environ.get("UV_PYTHON_INSTALL_DIR"):  # only on a mirror, never a developer's own uv Pythons
        # The newest patch of this Python (3.14.x): the venvs use uv's minor link, so all of them follow.
        # ponytail: patches come with an app update (the nightly one too); give it its own step in
        # auto_update() if they are ever needed without one.
        code, _, err = await run("uv", "python", "upgrade", running_python(), timeout=900)
        if code != 0:
            logger.warning(f"Python {running_python()} patch upgrade failed, keeping {sys.version.split()[0]}: {err.strip()}")
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


async def rebuild_venv() -> dict:
    """Build a fresh venv with this MirrorDash version. The modules aren't in it (the module site,
    ARCHITECTURE #41) and stay as they are."""
    from mirrordash_core.features.backup.service import venv_distributions
    paths = get_venv_paths()
    if not paths:
        raise HTTPException(status_code=500, detail="A/B updates are not supported on this filesystem layout (missing /storage/mirrordash).")
    current_version = get_core_version()
    if os.environ.get("MIRRORDASH_BOOT_STATUS") == "safe_mode":
        # Safe mode runs base_venv: the version to reinstall is the venv's that didn't start (the
        # link), or the one the launcher set aside before that (venv_failed)
        for venv_dir in (paths[0], paths[0].parent / "venv_failed"):
            if core := next(iter(venv_distributions(venv_dir, name="mirrordash")), None):
                current_version = core.version
                logger.info(f"Safe mode: reinstalling MirrorDash {current_version} of {venv_dir}")
                break
    logger.info(f"Rebuilding venv: installing mirrordash (version: {current_version})")
    try:
        async with venv_swap(force_clean=True) as python:
            code, _, err = await uv_pip(python, "install", "mirrordash" if current_version == "unknown" else f"mirrordash=={current_version}")
            if code != 0:
                logger.error(f"Failed to install mirrordash: {err}")
                raise HTTPException(status_code=500, detail=f"Failed to install core: {err}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {e}")
    logger.info("Fresh venv rebuild completed successfully. Restarting...")
    asyncio.create_task(run_restart())
    return {"status": "success", "message": "Environment rebuilt successfully. Restarting..."}


# --- OS updates (A/B) ---
# The OS (kernel, Pi OS, system setup) updates as a whole: a signed RAUC bundle from a GitHub release
# is written into the half of the card that isn't running, and the mirror restarts into it once on
# trial. It keeps the new version only if the network and the app work; otherwise it comes back on
# the old one by itself. How that works on the mirror: step_ab_updates in scripts/configure_image.sh.
#
# Which build is which: build_image.sh writes its build stamp (UTC, YYYYMMDD-HHMM) to
# /etc/mirrordash-os-build in the image, and ends the bundle's file name with the same stamp,
# e.g. mirrordash-os-v0.7.0-20261018-1915.raucb. A higher stamp is a newer OS.
OS_BUILD_FILE = Path("/etc/mirrordash-os-build")
# The release tag (v0.8.1-os1) of a build CI made for a release, only to show; the stamp decides.
OS_VERSION_FILE = Path("/etc/mirrordash-os-version")
# The download, and the build an update was started for (to tell afterwards whether it stuck).
# /storage/mirrordash belongs to the app's user; the sudoers rule allows installing exactly this file.
OS_BUNDLE_FILE = Path("/storage/mirrordash/os-update.raucb")
OS_PENDING_FILE = Path("/storage/mirrordash/os-update-pending")
# 1 while the firmware runs a new OS version on trial (see rauc-bootloader in configure_image.sh)
TRYBOOT_FLAG = Path("/proc/device-tree/chosen/bootloader/tryboot")
RELEASES_URL = "https://api.github.com/repos/Menturan/MirrorDash/releases?per_page=30"
BUNDLE_NAME = re.compile(r"^mirrordash-os-v.+-(\d{8}-\d{4})\.raucb$")


def format_build(build: str) -> str:
    """A build stamp for people: 20261018-2237 -> 2026-10-18 22:37 UTC (unchanged if it isn't one)."""
    if re.fullmatch(r"\d{8}-\d{4}", build):
        return f"{build[:4]}-{build[4:6]}-{build[6:8]} {build[9:11]}:{build[11:13]} UTC"
    return build


def installed_os_build() -> str | None:
    """The build stamp of the running OS, or None where there are no OS updates (an image from before
    A/B, a Pi set up by hand, a development machine)."""
    try:
        return OS_BUILD_FILE.read_text().strip() or None
    except OSError:
        return None


def installed_os_version() -> str | None:
    """The release tag of the running OS, or None (an image built without one, or before it was written)."""
    try:
        return OS_VERSION_FILE.read_text().strip() or None
    except OSError:
        return None


def newest_os_bundle(releases: list[dict], prerelease: bool) -> dict | None:
    """The newest update bundle among GitHub releases ({"tag", "build", "url", "size"}), or None.
    Pre-releases count only on mirrors with Test versions on; drafts never."""
    newest = None
    for release in releases:
        if release.get("draft") or (release.get("prerelease") and not prerelease):
            continue
        for asset in release.get("assets", []):
            match = BUNDLE_NAME.match(asset.get("name", ""))
            if match and (newest is None or match.group(1) > newest["build"]):
                newest = {"tag": release["tag_name"], "build": match.group(1),
                          "url": asset["browser_download_url"], "size": asset.get("size", 0)}
    return newest


def failed_os_update(installed: str) -> str | None:
    """The build of an OS update that didn't stick (the mirror came back on its old version), once;
    None otherwise. While the new version is still on trial, nothing is decided yet."""
    from mirrordash_core.admin import job_status
    try:
        target = OS_PENDING_FILE.read_text().strip()
    except OSError:
        return None
    if job_status()["state"] == "running":
        return None  # the update is still downloading or installing
    if target == installed:
        try:
            on_trial = TRYBOOT_FLAG.read_bytes() == b"\x00\x00\x00\x01"
        except OSError:
            on_trial = False
        if not on_trial:
            OS_PENDING_FILE.unlink(missing_ok=True)  # it stuck
        return None
    OS_PENDING_FILE.unlink(missing_ok=True)
    from mirrordash_core.config import save_config
    config = load_config()  # the nightly auto-update must skip this build too, whoever saw the failure first
    config.setdefault("system", {})["auto_update_skip_build"] = target
    save_config(config)
    return target


async def check_os_update() -> dict:
    """Whether a newer OS is available for this mirror, and whether the last OS update failed."""
    installed = installed_os_build()
    if installed is None:
        return {"supported": False}

    def _fetch_releases() -> list[dict]:
        request = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json",
                                                                "User-Agent": "MirrorDash"})
        try:
            with urllib.request.urlopen(request, timeout=8) as resp:  # noqa: S310
                return json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(f"GitHub request failed: {exc}") from exc

    try:
        releases = await asyncio.to_thread(_fetch_releases)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    bundle = newest_os_bundle(releases, prerelease_enabled())
    return {
        "supported": True,
        "installed_build": installed,
        "bundle": bundle,
        "update_available": bundle is not None and bundle["build"] > installed,
        "failed_build": failed_os_update(installed),
    }


async def update_os(bundle: dict) -> None:
    """Download the bundle to /storage, install it into the other slot, and restart into it on trial.
    Runs as a background job (admin.start_job); the admin page follows it across the restart."""
    free = shutil.disk_usage(OS_BUNDLE_FILE.parent).free
    if free < bundle["size"] + 200 * 1024 * 1024:
        raise HTTPException(status_code=507, detail=f"Not enough free space for the OS update "
                                                    f"({bundle['size'] // 1048576} MB plus some room).")

    def _download() -> None:
        # GitHub sends the file from another address (a redirect); urllib follows it
        partial = OS_BUNDLE_FILE.with_suffix(".part")
        with urllib.request.urlopen(bundle["url"], timeout=60) as resp, open(partial, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out, length=1024 * 1024)
        partial.replace(OS_BUNDLE_FILE)

    logger.info(f"OS update: downloading {bundle['tag']} (build {bundle['build']})")
    try:
        await asyncio.to_thread(_download)
    except Exception as exc:
        OS_BUNDLE_FILE.with_suffix(".part").unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"Downloading the OS update failed: {exc}") from exc

    # RAUC checks the signature, writes the other slot and arms the trial boot (rauc-bootloader)
    OS_PENDING_FILE.write_text(bundle["build"])
    logger.info("OS update: installing")
    proc = await asyncio.create_subprocess_exec("sudo", "-n", "rauc", "install", str(OS_BUNDLE_FILE),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    OS_BUNDLE_FILE.unlink(missing_ok=True)
    if proc.returncode != 0:
        OS_PENDING_FILE.unlink(missing_ok=True)
        await save_log("os-install", "-u", "rauc", "-b", "-n", "300")  # RAUC's own account of the failure
        error = stderr.decode(errors="replace").strip().splitlines()
        logger.error(f"OS update failed: {stderr.decode(errors='replace')}")
        raise HTTPException(status_code=500, detail=f"Installing the OS update failed: {error[-1] if error else 'unknown error'}")
    logger.info("OS update installed. Restarting into it on trial...")
    asyncio.create_task(reboot_system())


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


async def auto_update() -> str:
    """The nightly automatic update (mirrordash-auto-update.timer asks for it, see routes): the same
    as pressing the buttons on the Updates card, the system first. It never guesses for the owner:
    a system build whose trial boot failed isn't tried again by itself, and a newer Python that some
    modules don't work with waits for someone to choose Install Anyway. Returns what it started."""
    from mirrordash_core.admin import job_status, start_job
    from mirrordash_core.config import save_config
    system = load_config().get("system", {})
    if not system.get("auto_update", True):
        return "off"
    if job_status()["state"] == "running":
        return "busy"
    try:
        os_info = await check_os_update()
    except HTTPException:
        os_info = {}  # GitHub unreachable tonight: MirrorDash can still be up to date
    if os_info.get("failed_build"):  # seen once (failed_os_update): remember it for the nights to come
        config = load_config()
        config.setdefault("system", {})["auto_update_skip_build"] = os_info["failed_build"]
        save_config(config)
        system = config["system"]
    bundle = os_info.get("bundle")
    if os_info.get("update_available") and bundle["build"] != system.get("auto_update_skip_build"):
        start_job(lambda: update_os(bundle))
        return "system"
    app = await check_core_update()
    if not app["update_available"]:
        return "up to date"
    if app.get("needs_python") and incompatible.get("version") == app["latest_version"]:
        return "waiting for a choice"  # some modules don't work with the newer Python
    start_job(update_core)
    return "mirrordash"
