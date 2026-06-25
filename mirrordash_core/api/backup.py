# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import importlib.metadata
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, File, UploadFile
from fastapi.responses import FileResponse

from mirrordash_core.config import load_config, save_config, get_base_dir
from mirrordash_core.system import remount_ro, remount_rw, run_restart
from mirrordash_core.module_loader import module_loader
from mirrordash_core.api.admin import require_api_key

logger = logging.getLogger("mirrordash.core.api.backup")

router = APIRouter(prefix="/admin/backup")

# Paths
ROOT_DIR = Path(__file__).parent.parent.parent.resolve()
BASE_DIR = get_base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")

os.makedirs(BACKUPS_DIR, exist_ok=True)

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

@router.get("/list", dependencies=[Depends(require_api_key)])
async def list_backups() -> dict:
    """List all available backup .mirror files."""
    backups = []
    try:
        for entry in os.scandir(BACKUPS_DIR):
            if entry.is_file() and entry.name.endswith(".mirror"):
                stat = entry.stat()
                # Check encryption status
                is_encrypted = False
                try:
                    with zipfile.ZipFile(entry.path) as zf:
                        # Try to read manifest without password. If it raises a RuntimeError
                        # due to encryption, we catch it.
                        zf.read("backup_manifest.json")
                except RuntimeError as e:
                    if "encrypted" in str(e).lower():
                        is_encrypted = True
                except Exception:
                    pass

                backups.append({
                    "filename": entry.name,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "size_bytes": stat.st_size,
                    "encrypted": is_encrypted
                })
        # Sort by mtime descending
        backups.sort(key=lambda x: x["created_at"], reverse=True)
    except Exception as e:
        logger.error(f"Error listing backups: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list backups: {e}")

    return {"backups": backups}

@router.post("/create", dependencies=[Depends(require_api_key)])
async def create_backup(payload: dict = Body(default={})) -> dict:
    """Generate a backup .mirror archive (ZIP format), optionally password protected."""
    password = payload.get("password")

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
                # Copy entire data directory (ignoring backup folder to prevent nesting)
                shutil.copytree(DATA_DIR, temp_data_dir, ignore=shutil.ignore_patterns('*.tmp', '*.lock', '*-journal', '*-wal', '*-shm', 'backups'))
            except Exception as e:
                logger.error(f"Failed to copy data dir: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to package data files: {e}")
        else:
            temp_data_dir.mkdir(parents=True, exist_ok=True)

        # 3. Discover and process modules
        modules_list = []
        try:
            eps_dict = {}
            for ep in importlib.metadata.entry_points(group='mymm.modules'):
                eps_dict[ep.name] = ep
            for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
                eps_dict[ep.name] = ep
            eps = list(eps_dict.values())

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
                    # Module is PyPI
                    modules_list.append({
                        "name": name,
                        "package_name": package_name,
                        "version": version,
                        "type": "pypi"
                    })
        except Exception as e:
            logger.error(f"Failed to package modules metadata: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package modules: {e}")

        # Resolve the currently installed version
        core_version = "0.2.1"
        for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
            try:
                core_version = importlib.metadata.version(pkg_name)
                break
            except importlib.metadata.PackageNotFoundError:
                continue

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
            await remount_rw()
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
        finally:
            await remount_ro()

    return {"status": "success", "filename": backup_filename}

@router.get("/download/{filename}", dependencies=[Depends(require_api_key)])
async def download_backup(filename: str):
    """Download a backup file."""
    # Prevent path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    file_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")

@router.delete("/delete/{filename}", dependencies=[Depends(require_api_key)])
async def delete_backup(filename: str):
    """Delete a backup file."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    file_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    await remount_rw()
    try:
        os.remove(file_path)
        logger.info(f"Backup deleted: {filename}")
        return {"status": "success", "message": f"Deleted {filename}"}
    except Exception as e:
        logger.error(f"Failed to delete backup: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete backup: {e}")
    finally:
        await remount_ro()

@router.post("/upload", dependencies=[Depends(require_api_key)])
async def upload_backup(file: UploadFile = File(...)) -> dict:
    """Upload a backup file and inspect its manifest. Return password requirement info."""
    if not file.filename.endswith(".mirror"):
        raise HTTPException(status_code=400, detail="Invalid file type. File must have .mirror extension.")

    # Write to a temp upload file
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    await remount_rw()
    try:
        with open(temp_upload_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Verify if encrypted
        is_encrypted = False
        try:
            with zipfile.ZipFile(temp_upload_path) as zf:
                # Try to read manifest. If encrypted, raises RuntimeError
                zf.read("backup_manifest.json")
        except RuntimeError as e:
            if "encrypted" in str(e).lower():
                is_encrypted = True
            else:
                raise
        except Exception as e:
            logger.error(f"Failed to read uploaded file: {e}")
            raise HTTPException(status_code=400, detail="Invalid or corrupt backup archive.")

        if is_encrypted:
            return {"status": "needs_password", "filename": file.filename}

        # If not encrypted, return manifest info
        with zipfile.ZipFile(temp_upload_path) as zf:
            manifest_bytes = zf.read("backup_manifest.json")
            import json
            manifest = json.loads(manifest_bytes.decode('utf-8'))

        return {
            "status": "ready",
            "filename": file.filename,
            "manifest": manifest
        }
    finally:
        await remount_ro()

@router.post("/validate-password", dependencies=[Depends(require_api_key)])
async def validate_password(filename: str = Body(...), password: str = Body(...)) -> dict:
    """Validate password for the uploaded backup file."""
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    if not os.path.exists(temp_upload_path):
        raise HTTPException(status_code=400, detail="No uploaded backup found to validate.")

    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.setpassword(password.encode('utf-8'))
            manifest_bytes = zf.read("backup_manifest.json")
            import json
            manifest = json.loads(manifest_bytes.decode('utf-8'))

        return {
            "status": "ready",
            "filename": filename,
            "manifest": manifest
        }
    except RuntimeError:
        raise HTTPException(status_code=401, detail="Invalid backup password.")
    except Exception as e:
        logger.error(f"Error validating password: {e}")
        raise HTTPException(status_code=400, detail="Corrupt backup file.")

@router.post("/validate-local", dependencies=[Depends(require_api_key)])
async def validate_local(filename: str = Body(..., embed=True), password: str | None = Body(default=None)) -> dict:
    """Validate a local backup file's password and parse its manifest."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid backup filename")

    file_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Backup file not found")

    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    await remount_rw()
    try:
        shutil.copy(file_path, temp_upload_path)
    finally:
        await remount_ro()

    is_encrypted = False
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            if password:
                zf.setpassword(password.encode('utf-8'))
            zf.read("backup_manifest.json")
    except RuntimeError as e:
        if "encrypted" in str(e).lower():
            is_encrypted = True
        else:
            raise

    if is_encrypted and not password:
        return {"status": "needs_password", "filename": filename}

    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            if password:
                zf.setpassword(password.encode('utf-8'))
            manifest_bytes = zf.read("backup_manifest.json")
            import json
            manifest = json.loads(manifest_bytes.decode('utf-8'))

        return {
            "status": "ready",
            "filename": filename,
            "manifest": manifest
        }
    except RuntimeError:
        raise HTTPException(status_code=401, detail="Invalid backup password.")
    except Exception as e:
        logger.error(f"Error validating local file: {e}")
        raise HTTPException(status_code=400, detail="Corrupt backup file.")

@router.post("/restore", dependencies=[Depends(require_api_key)])
async def restore_backup(password: str | None = Body(default=None)) -> dict:
    """Execute the restore process using the pre-uploaded backup."""
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    if not os.path.exists(temp_upload_path):
        raise HTTPException(status_code=400, detail="No uploaded backup file found. Please upload a file first.")

    logger.info("Starting restoration process...")

    # 1. Read and cache the current system's admin auth config
    current_config = load_config()
    admin_auth = current_config.get("admin_auth")
    if not admin_auth:
        raise HTTPException(status_code=500, detail="Current system admin password is not configured.")

    # We will extract inside a temporary folder
    await remount_rw()
    try:
        with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as extract_dir_path:
            # 2. Extract ZIP
            cmd = ["unzip", "-o", temp_upload_path, "-d", extract_dir_path]
            env = os.environ.copy()
            if password:
                env["UNZIP"] = f"-P {password}"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode().strip()
                logger.error(f"Unzip failed during restore: {err_msg}")
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
                    logger.info(f"Restoring PyPI module: {package_name} (version {version})")
                    # Try installing with strict version, fallback to standard install if fails
                    cmd_install = ["uv", "pip", "install"] + python_target + [f"{package_name}=={version}"]
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
            save_config(restored_config)
            logger.info("Configuration files restored successfully.")

            # 5. Restore data files
            src_data_dir = extract_dir / "data"
            if src_data_dir.exists():
                # Re-create/clean current data dir
                for item in src_data_dir.iterdir():
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

        logger.info("Restoration completed successfully! Restarting mirror server...")
        # Trigger server reboot
        asyncio.create_task(run_restart())

        return {"status": "success", "message": "Restoration completed successfully. System restarting..."}
    except Exception as e:
        logger.error(f"Error during restoration: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Restoration failed: {e}")
    finally:
        await remount_ro()
