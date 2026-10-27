# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import contextlib
import importlib.metadata
import json
import logging
import os
import shutil
import stat
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
from mirrordash_core.features.hardware.devices import sync_gpio_overlays
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.loader import SAFE_NAME, module_entry_points
from mirrordash_core.features.mqtt.bridge import new_client_id, new_id
from mirrordash_core.features.wifi.network import add_wifi, saved_wifi
from mirrordash_core.host import reboot_system, run_restart

logger = logging.getLogger("mirrordash.core.backup")


# Paths
ROOT_DIR = Path(__file__).parent.parent.parent.resolve()


BASE_DIR = get_base_dir()


DATA_DIR = os.path.join(BASE_DIR, "data")


BACKUPS_DIR = os.path.join(DATA_DIR, "backups")


# Files in the data dir that never go in a backup nor come back from one: the config goes in on its
# own (sanitized unless encrypted), its last good copy and a broken one hold the credentials a plain
# backup leaves out, and a login cookie must not work on another mirror.
NEVER_BACKED_UP = ("config.json", "config.json.bak", "config.json.broken-*", "sessions.json", "backups")


# In a plain backup only: the pi user's password (an encrypted one carries it, and the Wi-Fi)
SECRET_FILES = ("pi_password.hash",)


# openssl enc's header: the backup is encrypted. ponytail: AES-256-CBC with PBKDF2, no MAC; a changed
# file fails to unpack as a zip rather than being detected. Upgrade path: openssl's -aead modes (3.x).
ENCRYPTED_MAGIC = b"Salted__"


# Helper to find or restore a local module directory
def get_modules_dir() -> Path:
    """Get the directory where local modules live or should be restored: on a mirror the module host's
    folder, where it can build them (ARCHITECTURE #41)."""
    dev_modules = ROOT_DIR / "modules"
    if dev_modules.exists() and os.access(dev_modules, os.W_OK):
        return dev_modules
    return get_module_dir() / "local"


def find_local_module_dir(package_name: str) -> Path | None:
    norm_pkg = package_name.lower().replace("_", "-")
    for base_dir in [ROOT_DIR / "modules", get_module_dir() / "local"]:
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir() and child.name.lower().replace("_", "-") == norm_pkg:
                    return child
    return None


def venv_distributions(venv_dir: Path | None = None, **kwargs):
    """The installed packages of this process, or of another venv read from its files: one whose
    Python doesn't run here (the venv Safe mode couldn't start, another Python version)."""
    if venv_dir is not None:
        kwargs["path"] = [str(p) for p in Path(venv_dir).glob("lib/python3.*/site-packages")]
    return importlib.metadata.distributions(**kwargs)


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def copy_out(src: Path, dst: Path, skip) -> None:
    """shutil.copytree(src, dst, ignore=skip) out of a folder the module host writes, through file
    descriptors (os.fwalk, O_NOFOLLOW): no symlink is followed, not even one swapped in while it copies,
    so the host can't carry the core's data into a backup. Links themselves are left out."""
    for root, dirs, files, root_fd in os.fwalk(src, follow_symlinks=False):
        target = dst / os.path.relpath(root, src)
        target.mkdir(parents=True, exist_ok=True)
        ignored = skip(root, dirs + files)
        dirs[:] = [d for d in dirs if d not in ignored]
        for name in files:
            if name in ignored:
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            except OSError:  # a link (ELOOP), or gone meanwhile
                continue
            with open(fd, "rb") as f:
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    with open(target / name, "wb") as out:
                        shutil.copyfileobj(f, out)


def _open_dir(root: Path, parts: tuple[str, ...]) -> int:
    """root/parts... opened as a directory, each part made if missing and none followed if it is a link."""
    fd = os.open(root, DIR_FLAGS)
    try:
        for part in parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=fd)
            fd, parent = os.open(part, DIR_FLAGS, dir_fd=fd), fd
            os.close(parent)
    except OSError:
        os.close(fd)
        raise
    return fd


def _copy_in(src: Path, dir_fd: int) -> None:
    """src's files into the open directory, group-writable for the module host (pi writes them); links
    left out. Nothing at dir_fd is followed: made with O_EXCL and O_NOFOLLOW."""
    for entry in os.scandir(src):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            os.mkdir(entry.name, dir_fd=dir_fd)
            fd = os.open(entry.name, DIR_FLAGS, dir_fd=dir_fd)
            try:
                os.fchmod(fd, 0o2770)
                _copy_in(Path(entry.path), fd)
            finally:
                os.close(fd)
        else:
            fd = os.open(entry.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o660, dir_fd=dir_fd)
            with open(fd, "wb") as out, open(entry.path, "rb") as f:
                os.fchmod(fd, 0o660)
                shutil.copyfileobj(f, out)


def _replace_in_module_folder(src: Path, dest: Path) -> bool:
    """Copy src over dest, in a folder the module host writes, through file descriptors: no symlink there
    is followed, not even one swapped in meanwhile (a link would have pi delete or write wherever it
    points, the core's data dir). False when a folder on the way is a link."""
    host_dir = get_module_dir()
    root, parts = (host_dir, dest.parent.relative_to(host_dir).parts) if dest.is_relative_to(host_dir) \
        else (dest.parent, ())
    try:
        parent = _open_dir(root, parts)
    except OSError as e:
        logger.error(f"{dest.parent} isn't a folder of its own ({e}): {dest.name} was not restored")
        return False
    try:
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISDIR(os.stat(dest.name, dir_fd=parent, follow_symlinks=False).st_mode):
                shutil.rmtree(dest.name, dir_fd=parent)  # fd-based: follows no link either
            else:
                os.unlink(dest.name, dir_fd=parent)
        os.mkdir(dest.name, dir_fd=parent)
        fd = os.open(dest.name, DIR_FLAGS, dir_fd=parent)
        try:
            os.fchmod(fd, 0o2770)
            _copy_in(src, fd)
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return True


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
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    return path


def is_encrypted(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC


async def _openssl(password: str, *args: str) -> bool:
    """`openssl enc` with the backup's cipher; the password goes in the environment, never on the
    command line (where `ps` shows it, and where a space split it with the old zip tool)."""
    proc = await asyncio.create_subprocess_exec(
        "openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", *args, "-pass", "env:MIRRORDASH_BACKUP_PASS",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "MIRRORDASH_BACKUP_PASS": password})
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logger.info(f"openssl enc failed: {err.decode(errors='replace').strip()}")
    return proc.returncode == 0


@contextlib.asynccontextmanager
async def plain_zip(path: str, password: str | None):
    """The backup as a zip: the file itself, or decrypted into a temporary file. None when it's
    encrypted and the password is missing or wrong."""
    if not is_encrypted(path):
        yield path
        return
    if not password:
        yield None
        return
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), suffix=".zip") as tmp:
        # A wrong password fails at the padding, or (about 1 in 256) gives bytes that aren't a zip
        ok = await _openssl(password, "-d", "-in", path, "-out", tmp.name)
        yield tmp.name if ok and zipfile.is_zipfile(tmp.name) else None


async def read_manifest(path: str, password: str | None = None) -> dict | None:
    """A backup's manifest, or None when it's encrypted and the password is missing or wrong.
    Raises zipfile.BadZipFile, KeyError or ValueError for a file that isn't a backup."""
    async with plain_zip(path, password) as zip_path:
        if zip_path is None:
            return None
        with zipfile.ZipFile(zip_path) as zf:
            if zf.getinfo("backup_manifest.json").flag_bits & 0x1:
                raise ValueError("This backup was made by an older MirrorDash version and can't be read.")
            return json.loads(zf.read("backup_manifest.json").decode("utf-8"))


async def backup_wifi(path: str, password: str | None) -> list[dict]:
    """The Wi-Fi networks an encrypted backup carries ([] for a plain one or a wrong password)."""
    async with plain_zip(path, password) as zip_path:
        if zip_path is None:
            return []
        with zipfile.ZipFile(zip_path) as zf:
            return json.loads(zf.read("wifi.json")) if "wifi.json" in zf.namelist() else []


async def list_backups() -> dict:
    """The saved .mirror files, newest first."""
    backups = []
    for entry in os.scandir(BACKUPS_DIR) if os.path.isdir(BACKUPS_DIR) else []:
        if entry.is_file() and entry.name.endswith(".mirror"):
            stat = entry.stat()
            backups.append({"filename": entry.name, "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "size_bytes": stat.st_size, "encrypted": is_encrypted(entry.path)})
    backups.sort(key=lambda b: b["created_at"], reverse=True)
    return {"backups": backups}


async def create_backup(password: str | None = None) -> dict:
    """Write a backup .mirror archive (a zip). With a password the whole file is encrypted and also
    carries what logs in: the admin password, the pi user's password and the saved Wi-Fi networks."""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"mirrordash_backup_{timestamp}.mirror"
    backup_path = os.path.join(BACKUPS_DIR, backup_filename)

    logger.info(f"Creating backup: {backup_filename} (encrypted: {bool(password)})")

    # We will build the archive inside a secure temporary directory
    os.makedirs(BACKUPS_DIR, exist_ok=True)  # a new mirror's storage has no backups folder yet
    with tempfile.TemporaryDirectory(dir=BACKUPS_DIR) as temp_dir_path:
        temp_dir = Path(temp_dir_path)
        content = temp_dir / "content"
        content.mkdir()

        # 1. The config; a plain backup leaves out the admin credentials and the MQTT broker's password
        try:
            config = load_config().copy()
            if not password:
                config.pop("admin_auth", None)
                if isinstance(config.get("mqtt"), dict):
                    config["mqtt"] = {k: v for k, v in config["mqtt"].items() if k != "password"}
            with open(content / "config.json", "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
            if password:
                with open(content / "wifi.json", "w", encoding="utf-8") as f:
                    json.dump(await saved_wifi(), f)
        except Exception as e:
            logger.error(f"Failed to copy config: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to package configuration: {e}")

        # 2. Copy data_dir files (excluding cache_dir)
        temp_data_dir = content / "data"
        skip = shutil.ignore_patterns('*.tmp', '*.lock', '*-journal', '*-wal', '*-shm')
        if os.path.exists(DATA_DIR):
            try:
                top = shutil.ignore_patterns(*NEVER_BACKED_UP, *(() if password else SECRET_FILES))
                def ignore(directory, names):
                    ignored = skip(directory, names)
                    return ignored | top(directory, names) if Path(directory) == Path(DATA_DIR) else ignored
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
                await asyncio.to_thread(copy_out, module_data, content / "module_data",
                                        shutil.ignore_patterns('*.tmp', '*.lock', '*-journal', '*-wal', '*-shm'))
            except Exception as e:
                logger.error(f"Failed to copy the modules' data: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to package the modules' data: {e}")

        # 3. Discover and process modules; a local module's source code goes in the archive
        try:
            modules_list = installed_modules()
            for mod in modules_list:
                if mod["type"] == "local":
                    await asyncio.to_thread(
                        copy_out,
                        find_local_module_dir(mod["package_name"]),
                        content / "local_modules" / mod["folder_name"],
                        shutil.ignore_patterns('.git', '__pycache__', '.venv', 'dist', 'build', '*.pyc', '.idea')
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
    os.remove(backup_file(filename))
    logger.info(f"Backup deleted: {filename}")


async def _restore_module(mod: dict, extract_dir: Path, site) -> None:
    """Install one module from the manifest into the module site being changed (module_host.packages):
    a GitHub module at its commit, a PyPI module at its version (or the latest if that fails), a local
    module from the copy in the backup."""
    package_name, version = mod.get("package_name"), mod.get("version")
    if mod.get("type", "pypi") == "pypi":
        source = mod.get("source") or ""
        target = source if source.startswith("git+https://github.com/") else f"{package_name}=={version}"
        logger.info(f"Restoring module: {package_name} from {target}")
        if (await site.pip("install", target))[0] != 0:
            logger.warning(f"Failed to install package {package_name}=={version}. Retrying general install...")
            if (await site.pip("install", package_name))[0] != 0:
                logger.error(f"Could not restore module {package_name}: install failed")
        return
    folder = mod.get("folder_name")
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
    """The backup's config: with its admin password when it carries one (an encrypted backup),
    otherwise this mirror's, read now so a change made during the restore isn't undone. True when
    the GPIO overlays changed and the mirror must reboot."""
    restored = json.loads((extract_dir / "config.json").read_text(encoding="utf-8"))
    # GPIO overlays live in the boot config, not in the backup: write them for this card
    system_cfg = restored.setdefault("system", {})
    system_cfg.pop("gpio_overlays", None)
    reboot_needed, gpio_error = await sync_gpio_overlays(system_cfg)
    if gpio_error:
        logger.error(f"Could not restore GPIO settings: {gpio_error}")
    current = load_config()
    restored["admin_auth"] = restored.get("admin_auth") or current.get("admin_auth")
    if not restored["admin_auth"]:
        del restored["admin_auth"]  # a mirror still being set up: the admin page asks for one
    if isinstance(restored.get("mqtt"), dict):
        # This mirror's own MQTT ids, never the backup's: two mirrors under one id would share their commands,
        # and under one client ID throw each other off the broker
        restored["mqtt"]["id"] = current.get("mqtt", {}).get("id") or new_id()
        restored["mqtt"]["client_id"] = current.get("mqtt", {}).get("client_id") or new_client_id()
    save_config(restored)
    logger.info("Configuration files restored successfully.")
    return reboot_needed


def _restore_data(src_data_dir: Path) -> None:
    """Module data back into the data dir, replacing what's there."""
    never = shutil.ignore_patterns(*NEVER_BACKED_UP)
    items = list(src_data_dir.iterdir()) if src_data_dir.exists() else []
    skipped = never(str(src_data_dir), [item.name for item in items])
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
                    raise Exception("Failed to decrypt the backup: wrong password.")
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
                for mod in manifest.get("modules", []):
                    await _restore_module(mod, extract_dir, site)
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
    asyncio.create_task(reboot_system() if reboot_needed else run_restart())
    return {"status": "success", "message": "Restoration completed successfully. System restarting..."}
