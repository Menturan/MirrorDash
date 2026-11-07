# SPDX-License-Identifier: AGPL-3.0-only
"""Backups: a .mirror file is a zip of the settings, the data dir, the modules' data and the source of
local modules, with a manifest of the installed modules to reinstall. With a password the whole zip is
encrypted with openssl and also carries what logs in (the admin password, the pi user's, the Wi-Fi).

This file makes, lists, deletes and restores backups. Beside it:
  archive.py          the .mirror file: zip, encryption, reading its manifest
  secret_settings.py  the secrets a plain backup leaves out, and keeping this mirror's own on a restore
  safe_copy.py        copying the modules' data out of and back into the module host's folder
  routes.py           the admin page's Backup panel
"""

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from mirrordash_core.config import (
    get_base_dir,
    get_core_version,
    get_module_dir,
    load_config,
    save_config,
)
from mirrordash_core.features.backup.archive import _openssl, is_encrypted, plain_zip
from mirrordash_core.features.backup.safe_copy import (
    _replace_in_module_folder,
    copy_out_of_module_folder,
)
from mirrordash_core.features.backup.secret_settings import (
    _keep_this_mirrors_secrets,
    without_secrets,
)
from mirrordash_core.features.hardware.devices import sync_gpio_overlays
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.module_files import SAFE_NAME, module_entry_points
from mirrordash_core.features.mqtt.bridge import new_client_id, new_id
from mirrordash_core.features.wifi.network import add_wifi, saved_wifi
from mirrordash_core.host import reboot_system, run_in_background, run_restart

logger = logging.getLogger("mirrordash.core.backup")


# The source checkout's root, where a developer's local modules sit in modules/
ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
BASE_DIR = get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

# Files in the data dir that never go in a backup nor come back from one: the config goes in on its
# own (sanitized unless encrypted), its last good copy and a broken one hold the credentials a plain
# backup leaves out, a login cookie must not work on another mirror, and the notifications are this mirror's history.
NEVER_BACKED_UP = ("config.json", "config.json.bak", "config.json.broken-*", "sessions.json", "backups", "notifications.json")
# Files a database or an editor keeps only while it works: never worth restoring
TEMPORARY_FILES = ("*.tmp", "*.lock", "*-journal", "*-wal", "*-shm")
# What a local module's folder holds that isn't its source
NOT_MODULE_SOURCE = (".git", "__pycache__", ".venv", "dist", "build", "*.pyc", ".idea")

# In a plain backup only: the pi user's password (an encrypted one carries it, and the Wi-Fi)
SECRET_FILES = ("pi_password.hash",)


def get_modules_dir() -> Path:
    """Get the directory where local modules live or should be restored: on a mirror the module host's
    folder, where it can build them (ARCHITECTURE #41)."""
    dev_modules = ROOT_DIR / "modules"
    if dev_modules.exists() and os.access(dev_modules, os.W_OK):
        return dev_modules
    return get_module_dir() / "local"


def find_local_module_dir(package_name: str) -> Path | None:
    """The folder of a module installed from a local folder (not from GitHub or PyPI), or None."""
    def same_name(name: str) -> bool:
        return name.lower().replace("_", "-") == package_name.lower().replace("_", "-")
    for base_dir in [ROOT_DIR / "modules", get_module_dir() / "local"]:
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir() and same_name(child.name):
                    return child
    return None


def venv_distributions(venv_dir: Path | None = None, **kwargs):
    """The installed packages of this process, or of another venv read from its files: one whose
    Python doesn't run here (the venv Safe mode couldn't start, another Python version)."""
    if venv_dir is not None:
        kwargs["path"] = [str(p) for p in Path(venv_dir).glob("lib/python3.*/site-packages")]
    return importlib.metadata.distributions(**kwargs)


def installed_modules() -> list[dict]:
    """Every installed module and where it reinstalls from: its local folder, its GitHub commit
    (PEP 610 records it) or its PyPI version. The backup manifest and a new Python's module site use it."""
    modules = []
    for ep in module_entry_points():
        package_name = ep.dist.name if ep.dist else ep.name
        entry = {"name": ep.name, "package_name": package_name,
                 "version": ep.dist.version if ep.dist else "0.0.0", "type": "pypi"}
        local_dir = find_local_module_dir(package_name)
        if local_dir:
            entry.update(type="local", folder_name=local_dir.name)
        else:
            direct_url = json.loads((ep.dist.read_text("direct_url.json") if ep.dist else None) or "{}")
            vcs = direct_url.get("vcs_info")
            if vcs and direct_url.get("url"):
                entry["source"] = f"git+{direct_url['url']}@{vcs['commit_id']}"
        modules.append(entry)
    return modules


def temp_upload() -> str:
    """Where the backup being restored waits (uploaded, or copied from the saved ones)."""
    os.makedirs(BACKUPS_DIR, exist_ok=True)  # a new mirror's storage has no backups folder yet
    return os.path.join(BACKUPS_DIR, "tmp_upload.mirror")


def backup_file(filename: str) -> str:
    """The path of a saved backup. Trust boundary: a bare file name, never a path."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.isfile(path):  # "" or "." is the folder itself
        raise HTTPException(status_code=404, detail="Backup file not found")
    return path


async def list_backups() -> dict:
    """The saved .mirror files, newest first."""
    backups = []
    for entry in os.scandir(BACKUPS_DIR) if os.path.isdir(BACKUPS_DIR) else []:
        if entry.is_file() and entry.name.endswith(".mirror"):
            file_info = entry.stat()
            backups.append({"filename": entry.name, "created_at": datetime.fromtimestamp(file_info.st_mtime).isoformat(),
                            "size_bytes": file_info.st_size, "encrypted": is_encrypted(entry.path)})
    backups.sort(key=lambda b: b["created_at"], reverse=True)
    return {"backups": backups}


async def create_backup(password: str | None = None) -> dict:
    """Write a backup .mirror archive (a zip). With a password the whole file is encrypted and also
    carries what logs in: the admin password, the pi user's password and the saved Wi-Fi networks."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"mirrordash_backup_{timestamp}.mirror"
    backup_path = os.path.join(BACKUPS_DIR, backup_filename)

    logger.info(f"Creating backup: {backup_filename} (encrypted: {bool(password)})")

    os.makedirs(BACKUPS_DIR, exist_ok=True)  # a new mirror's storage has no backups folder yet
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as temp_dir_path:
        temp_dir = Path(temp_dir_path)
        content = temp_dir / "content"
        content.mkdir()

        # 1. The config; a plain backup carries no password (without_secrets)
        try:
            config = load_config() if password else without_secrets(load_config())
            with open(content / "config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            if password:
                with open(content / "wifi.json", "w", encoding="utf-8") as f:
                    json.dump(await saved_wifi(), f)
        except Exception as e:
            logger.error(f"Failed to copy config: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package configuration: {e}")

        # 2. The core's data dir, without what never goes in a backup (that only at its top level)
        temp_data_dir = content / "data"
        ignore_temporary_files = shutil.ignore_patterns(*TEMPORARY_FILES)
        if os.path.exists(DATA_DIR):
            try:
                ignore_at_top = shutil.ignore_patterns(*NEVER_BACKED_UP, *(() if password else SECRET_FILES))
                def ignore(directory, names):
                    ignored = ignore_temporary_files(directory, names)
                    return ignored | ignore_at_top(directory, names) if Path(directory) == Path(DATA_DIR) else ignored
                await asyncio.to_thread(shutil.copytree, DATA_DIR, temp_data_dir, ignore=ignore)
            except Exception as e:
                logger.error(f"Failed to copy data dir: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to package data files: {e}")
        else:
            temp_data_dir.mkdir(parents=True, exist_ok=True)
        # The modules' own data_dirs, in the module host's folder (ARCHITECTURE #41); never their caches
        module_data = get_module_dir() / "data"
        if module_data.is_dir() and not module_data.is_symlink():
            try:
                await asyncio.to_thread(copy_out_of_module_folder, module_data, content / "module_data",
                                        ignore_temporary_files)
            except Exception as e:
                logger.error(f"Failed to copy the modules' data: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to package the modules' data: {e}")

        # 3. Discover and process modules; a local module's source code goes in the archive
        try:
            modules_list = installed_modules()
            for module in modules_list:
                if module["type"] == "local":
                    await asyncio.to_thread(
                        copy_out_of_module_folder,
                        find_local_module_dir(module["package_name"]),
                        content / "local_modules" / module["folder_name"],
                        shutil.ignore_patterns(*NOT_MODULE_SOURCE)
                    )
        except Exception as e:
            logger.error(f"Failed to package modules metadata: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package modules: {e}")

        # 4. Generate manifest file
        manifest = {
            "backup_version": "2.0",
            "timestamp": datetime.now().isoformat(),
            "encrypted": bool(password),
            "system": {
                "core_version": get_core_version()
            },
            "modules": modules_list
        }
        with open(content / "backup_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 5. Zip it, and encrypt the zip with a password
        try:
            zip_path = await asyncio.to_thread(shutil.make_archive, str(temp_dir / "backup"), "zip", content)
            if not password:
                os.replace(zip_path, backup_path)
            elif not await _openssl(password, "-salt", "-in", zip_path, "-out", backup_path):
                raise RuntimeError("encryption failed")
            logger.info(f"Backup created successfully: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to write backup: {e}", exc_info=True)
            with contextlib.suppress(OSError):
                os.remove(backup_path)
            raise HTTPException(status_code=500, detail=f"Failed to write backup archive: {e}")

    return {"status": "success", "filename": backup_filename}


async def delete_backup(filename: str) -> None:
    """Delete a saved backup."""
    os.remove(backup_file(filename))
    logger.info(f"Backup deleted: {filename}")


async def _restore_module(module: dict, extract_dir: Path, site) -> None:
    """Install one module from the manifest into the module site being changed (module_host.packages):
    a GitHub module at its commit, a PyPI module at its version (or the latest if that fails), a local
    module from the copy in the backup."""
    package_name, version = module.get("package_name"), module.get("version")
    if module.get("type", "pypi") == "pypi":
        source = module.get("source") or ""
        target = source if source.startswith("git+https://github.com/") else f"{package_name}=={version}"
        logger.info(f"Restoring module: {package_name} from {target}")
        if (await site.pip("install", target))[0] != 0:
            logger.warning(f"Failed to install package {package_name}=={version}. Retrying general install...")
            if (await site.pip("install", package_name))[0] != 0:
                logger.error(f"Could not restore module {package_name}: install failed")
        return
    folder = module.get("folder_name")
    # Trust boundary: the manifest names a folder; a path ("/home/pi", "../..") would be emptied below
    if not isinstance(folder, str) or Path(folder).name != folder or folder in ("", ".", ".."):
        logger.error(f"Local module {package_name}: invalid folder name {folder!r} in the backup, skipped")
        return
    src_dir = extract_dir / "local_modules" / folder
    if not src_dir.exists():
        logger.error(f"Local module source code folder {folder} missing from backup!")
        return
    logger.info(f"Restoring Local module: {package_name} from folder {folder}")
    dest_dir = get_modules_dir() / folder
    if await asyncio.to_thread(_replace_in_module_folder, src_dir, dest_dir):
        await site.pip("install", "-e", str(dest_dir))


async def _restore_config(extract_dir: Path) -> bool:
    """The backup's config: with its secrets when it carries them (an encrypted backup), otherwise this
    mirror's own, read now so a change made during the restore isn't undone. True when the GPIO overlays
    changed and the mirror must reboot."""
    restored = json.loads((extract_dir / "config.json").read_text(encoding="utf-8"))
    # GPIO overlays live in the boot config, not in the backup: write them for this card
    system_cfg = restored.setdefault("system", {})
    system_cfg.pop("gpio_overlays", None)
    reboot_needed, gpio_error = await sync_gpio_overlays(system_cfg)
    if gpio_error:
        logger.error(f"Could not restore GPIO settings: {gpio_error}")
    current = load_config()
    # With no admin password in either, the mirror is still being set up: the admin page asks for one
    _keep_this_mirrors_secrets(restored, current)
    if isinstance(restored.get("mqtt"), dict):
        current_mqtt = current.get("mqtt", {})
        # This mirror's own MQTT ids, never the backup's: two mirrors under one id would share their commands,
        # and under one client ID throw each other off the broker
        restored["mqtt"]["id"] = current_mqtt.get("id") or new_id()
        restored["mqtt"]["client_id"] = current_mqtt.get("client_id") or new_client_id()
    save_config(restored)
    logger.info("Configuration files restored successfully.")
    return reboot_needed


def _restore_data(src_data_dir: Path) -> None:
    """Module data back into the data dir, replacing what's there."""
    never_restored = shutil.ignore_patterns(*NEVER_BACKED_UP)
    items = list(src_data_dir.iterdir()) if src_data_dir.exists() else []
    skipped = never_restored(str(src_data_dir), [item.name for item in items])
    for item in items:
        if item.name in skipped:
            continue
        dest = Path(DATA_DIR) / item.name
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            os.remove(dest)
        (shutil.copytree if item.is_dir() else shutil.copy)(item, dest)
    logger.info("Module persistent data files restored successfully.")


def _restore_module_data(src: Path) -> None:
    """Each instance's data_dir back into the module host's folder, replacing what's there."""
    dest_root = get_module_dir() / "data"
    for item in (src.iterdir() if src.is_dir() else []):
        if not item.is_dir() or item.is_symlink() or not SAFE_NAME.match(item.name):
            continue
        if not _replace_in_module_folder(item, dest_root / item.name):
            return


async def restore_backup(password: str | None = None) -> dict:
    """Restore the backup waiting in temp_upload(): modules (into a copy of the module site), settings,
    saved Wi-Fi networks and module data, then restart (or reboot, for new GPIO overlays)."""
    upload = temp_upload()
    if not os.path.exists(upload):
        raise HTTPException(status_code=400, detail="No uploaded backup file found. Please upload a file first.")
    logger.info("Starting restoration process...")

    try:
        with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as extract_dir:
            async with plain_zip(upload, password) as zip_path:
                if zip_path is None:
                    raise HTTPException(status_code=400, detail="Failed to decrypt the backup: wrong password.")
                def extract():
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(extract_dir)
                try:
                    await asyncio.to_thread(extract)
                except Exception as e:
                    logger.error(f"Unzip failed during restore: {e}")
                    raise Exception("Failed to extract backup file.")
            extract_dir = Path(extract_dir)
            manifest = json.loads((extract_dir / "backup_manifest.json").read_text(encoding="utf-8"))
            async with module_host.packages() as site:
                for module in manifest.get("modules", []):
                    await _restore_module(module, extract_dir, site)
            reboot_needed = await _restore_config(extract_dir)
            await asyncio.to_thread(_restore_data, extract_dir / "data")
            await asyncio.to_thread(_restore_module_data, extract_dir / "module_data")
            if (extract_dir / "wifi.json").exists():
                await add_wifi(json.loads((extract_dir / "wifi.json").read_text(encoding="utf-8")))
        os.remove(upload)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during restoration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Restoration failed: {e}")

    logger.info(f"Restoration completed successfully! {'Rebooting to load the GPIO overlays' if reboot_needed else 'Restarting mirror server'}...")
    run_in_background(reboot_system() if reboot_needed else run_restart())
    return {"status": "success", "message": "Restoration completed successfully. System restarting..."}
