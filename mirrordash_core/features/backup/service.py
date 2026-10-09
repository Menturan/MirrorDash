# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import json
import logging
import os
import shutil
import tempfile
import zipfile
import importlib.metadata
from datetime import datetime
from pathlib import Path
from fastapi import HTTPException
from mirrordash_core.config import load_config, save_config, get_base_dir, get_core_version
from mirrordash_core.host import reboot_system, run_restart
from mirrordash_core.features.hardware.devices import sync_gpio_overlays

logger = logging.getLogger("mirrordash.core.backup")


# Paths
ROOT_DIR = Path(__file__).parent.parent.parent.resolve()


BASE_DIR = get_base_dir()


DATA_DIR = os.path.join(BASE_DIR, "data")


BACKUPS_DIR = os.path.join(DATA_DIR, "backups")


# Helper to find or restore a local module directory
def get_modules_dir() -> Path:
    """Get the directory where local modules live or should be restored."""
    dev_modules = ROOT_DIR / "modules"
    if dev_modules.exists() and os.access(dev_modules, os.W_OK):
        return dev_modules

    home_modules = get_base_dir() / "modules"
    return home_modules


def find_local_module_dir(package_name: str) -> Path | None:
    norm_pkg = package_name.lower().replace("_", "-")
    for base_dir in [ROOT_DIR / "modules", get_base_dir() / "modules"]:
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir():
                    if child.name.lower().replace("_", "-") == norm_pkg:
                        return child
    return None


def temp_upload() -> str:
    """Where the backup being restored waits (uploaded, or copied from the saved ones)."""
    return os.path.join(BACKUPS_DIR, "tmp_upload.mirror")


def backup_file(filename: str) -> str:
    """The path of a saved backup. Trust boundary: a bare file name, never a path."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")
    path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    return path


def read_manifest(path: str, password: str | None = None) -> dict | None:
    """A backup's manifest, or None when it's encrypted and the password is missing or wrong.
    Raises zipfile.BadZipFile, KeyError or ValueError for a file that isn't a backup."""
    with zipfile.ZipFile(path) as zf:
        if password:
            zf.setpassword(password.encode("utf-8"))
        try:
            return json.loads(zf.read("backup_manifest.json").decode("utf-8"))
        except RuntimeError as e:  # "is encrypted, password required" / "Bad password"
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                return None
            raise


async def list_backups() -> dict:
    """The saved .mirror files, newest first."""
    backups = []
    for entry in os.scandir(BACKUPS_DIR) if os.path.isdir(BACKUPS_DIR) else []:
        if entry.is_file() and entry.name.endswith(".mirror"):
            try:
                encrypted = read_manifest(entry.path) is None
            except Exception:
                encrypted = False
            stat = entry.stat()
            backups.append({"filename": entry.name, "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "size_bytes": stat.st_size, "encrypted": encrypted})
    backups.sort(key=lambda b: b["created_at"], reverse=True)
    return {"backups": backups}


async def create_backup(password: str | None = None) -> dict:
    """Write a backup .mirror archive (ZIP format), optionally password protected."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"mirrordash_backup_{timestamp}.mirror"
    backup_path = os.path.join(BACKUPS_DIR, backup_filename)

    logger.info(f"Creating backup: {backup_filename} (password protected: {bool(password)})")

    # We will build the archive inside a secure temporary directory
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as temp_dir_path:
        temp_dir = Path(temp_dir_path)

        # 1. Sanitize config.json (strip admin credentials)
        try:
            config = load_config().copy()
            if "admin_auth" in config:
                del config["admin_auth"]
            import json
            with open(temp_dir / "config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to copy config: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package configuration: {e}")

        # 2. Copy data_dir files (excluding cache_dir)
        temp_data_dir = temp_dir / "data"
        if os.path.exists(DATA_DIR):
            try:
                # Copy entire data directory (ignoring backup folder to prevent nesting). The live
                # config.json lives here too: skip it, the sanitized copy above is the only one.
                skip = shutil.ignore_patterns('*.tmp', '*.lock', '*-journal', '*-wal', '*-shm', 'backups')
                def ignore(directory, names):
                    ignored = skip(directory, names)
                    return ignored | {"config.json"} if Path(directory) == Path(DATA_DIR) else ignored
                shutil.copytree(DATA_DIR, temp_data_dir, ignore=ignore)
            except Exception as e:
                logger.error(f"Failed to copy data dir: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to package data files: {e}")
        else:
            temp_data_dir.mkdir(parents=True, exist_ok=True)

        # 3. Discover and process modules
        modules_list = []
        try:
            eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))

            for ep in eps:
                name = ep.name
                package_name = ep.dist.name if ep.dist else name
                version = ep.dist.version if ep.dist else "0.0.0"

                local_dir = find_local_module_dir(package_name)
                if local_dir:
                    # Module is local
                    modules_list.append({
                        "name": name,
                        "package_name": package_name,
                        "version": version,
                        "type": "local",
                        "folder_name": local_dir.name
                    })
                    # Copy module source code to the temp archive dir
                    temp_local_modules_dir = temp_dir / "local_modules" / local_dir.name
                    shutil.copytree(
                        local_dir,
                        temp_local_modules_dir,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', '.venv', 'dist', 'build', '*.pyc', '.idea')
                    )
                else:
                    # Module is PyPI, or installed from GitHub: PEP 610 records where it came from
                    entry = {
                        "name": name,
                        "package_name": package_name,
                        "version": version,
                        "type": "pypi"
                    }
                    direct_url = json.loads((ep.dist.read_text("direct_url.json") if ep.dist else None) or "{}")
                    vcs = direct_url.get("vcs_info")
                    if vcs and direct_url.get("url"):
                        entry["source"] = f"git+{direct_url['url']}@{vcs['commit_id']}"
                    modules_list.append(entry)
        except Exception as e:
            logger.error(f"Failed to package modules metadata: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package modules: {e}")

        # Resolve the currently installed version
        core_version = get_core_version()

        # 4. Generate manifest file
        manifest = {
            "backup_version": "1.0",
            "timestamp": datetime.now().isoformat(),
            "encrypted": bool(password),
            "system": {
                "core_version": core_version
            },
            "modules": modules_list
        }
        with open(temp_dir / "backup_manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # 5. Compress using system zip tool to support optional encryption
        try:
            cmd = ["zip", "-r", backup_path, "."]
            env = os.environ.copy()
            if password:
                env["ZIPOPT"] = f"-P {password}"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=temp_dir_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                logger.error(f"Zip subprocess failed: {err_msg}")
                raise Exception(err_msg)

            logger.info(f"Backup created successfully: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to compress backup: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to write backup archive: {e}")

    return {"status": "success", "filename": backup_filename}


async def delete_backup(filename: str) -> None:
    os.remove(backup_file(filename))
    logger.info(f"Backup deleted: {filename}")


async def restore_backup(password: str | None = None) -> dict:
    """Execute the restore process using the pre-uploaded backup."""
    temp_upload_path = temp_upload()
    if not os.path.exists(temp_upload_path):
        raise HTTPException(status_code=400, detail="No uploaded backup file found. Please upload a file first.")

    logger.info("Starting restoration process...")

    # 1. Read and cache the current system's admin auth config
    current_config = load_config()
    admin_auth = current_config.get("admin_auth")
    if not admin_auth:
        raise HTTPException(status_code=500, detail="Current system admin password is not configured.")

    # We will extract inside a temporary folder
    try:
        with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as extract_dir_path:
            # 2. Extract ZIP using standard zipfile
            try:
                def _extract():
                    with zipfile.ZipFile(temp_upload_path) as zf:
                        if password:
                            zf.setpassword(password.encode('utf-8'))
                        zf.extractall(extract_dir_path)
                await asyncio.to_thread(_extract)
            except Exception as e:
                logger.error(f"Unzip failed during restore: {e}")
                raise Exception("Failed to decrypt or extract backup file.")

            extract_dir = Path(extract_dir_path)

            # Read manifest
            import json
            with open(extract_dir / "backup_manifest.json", "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 3. Re-install modules
            modules = manifest.get("modules", [])
            for mod in modules:
                mod_type = mod.get("type", "pypi")
                package_name = mod.get("package_name")
                version = mod.get("version")

                python_target = []
                if os.path.exists("/storage/mirrordash/venv/bin/python"):
                    python_target = ["--python", "/storage/mirrordash/venv/bin/python"]

                if mod_type == "pypi":
                    # GitHub modules go back to the exact commit; only GitHub, like install_module
                    source = mod.get("source") or ""
                    target = source if source.startswith("git+https://github.com/") else f"{package_name}=={version}"
                    logger.info(f"Restoring module: {package_name} from {target}")
                    # Try installing with strict version, fallback to standard install if fails
                    cmd_install = ["uv", "pip", "install"] + python_target + [target]
                    proc_inst = await asyncio.create_subprocess_exec(
                        *cmd_install,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    await proc_inst.communicate()
                    if proc_inst.returncode != 0:
                        logger.warning(f"Failed to install package {package_name}=={version}. Retrying general install...")
                        cmd_fallback = ["uv", "pip", "install"] + python_target + [package_name]
                        proc_fallback = await asyncio.create_subprocess_exec(
                            *cmd_fallback,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        await proc_fallback.communicate()
                        if proc_fallback.returncode != 0:
                            logger.error(f"Could not restore module {package_name}: install failed")

                elif mod_type == "local":
                    folder_name = mod.get("folder_name")
                    logger.info(f"Restoring Local module: {package_name} from folder {folder_name}")
                    src_dir = extract_dir / "local_modules" / folder_name
                    dest_modules_dir = get_modules_dir()
                    dest_dir = dest_modules_dir / folder_name

                    if src_dir.exists():
                        # Delete existing module folder if exists
                        if dest_dir.exists():
                            shutil.rmtree(dest_dir)
                        # Ensure parent dir exists
                        dest_modules_dir.mkdir(parents=True, exist_ok=True)
                        # Copy back
                        shutil.copytree(src_dir, dest_dir)
                        # Install editable mode
                        cmd_local = ["uv", "pip", "install"] + python_target + ["-e", str(dest_dir)]
                        proc_local = await asyncio.create_subprocess_exec(
                            *cmd_local,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        await proc_local.communicate()
                    else:
                        logger.error(f"Local module source code folder {folder_name} missing from backup!")

            # 4. Restore configuration & restore current admin credentials
            with open(extract_dir / "config.json", "r", encoding="utf-8") as f:
                restored_config = json.load(f)

            # Overwrite config.json credentials with current admin credentials
            restored_config["admin_auth"] = admin_auth

            # GPIO overlays live in the boot config, not in the backup: write them for this card
            system_cfg = restored_config.setdefault("system", {})
            system_cfg.pop("gpio_overlays", None)
            reboot_needed, gpio_error = await sync_gpio_overlays(system_cfg)
            if gpio_error:
                logger.error(f"Could not restore GPIO settings: {gpio_error}")
            save_config(restored_config)
            logger.info("Configuration files restored successfully.")

            # 5. Restore data files
            src_data_dir = extract_dir / "data"
            if src_data_dir.exists():
                # Re-create/clean current data dir
                for item in src_data_dir.iterdir():
                    if item.name == "config.json":
                        continue  # older backups carry the raw config with admin credentials
                    dest_item = Path(DATA_DIR) / item.name
                    if item.is_dir():
                        if dest_item.exists():
                            shutil.rmtree(dest_item)
                        shutil.copytree(item, dest_item)
                    else:
                        if dest_item.exists():
                            os.remove(dest_item)
                        shutil.copy(item, dest_item)
                logger.info("Module persistent data files restored successfully.")

        # Clean up temp file
        if os.path.exists(temp_upload_path):
            os.remove(temp_upload_path)

        if reboot_needed:
            logger.info("Restoration completed successfully! Rebooting to load the GPIO overlays...")
            asyncio.create_task(reboot_system())
        else:
            logger.info("Restoration completed successfully! Restarting mirror server...")
            asyncio.create_task(run_restart())

        return {"status": "success", "message": "Restoration completed successfully. System restarting..."}
    except Exception as e:
        logger.error(f"Error during restoration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Restoration failed: {e}")
