# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import os
import re
import secrets
import hashlib
import binascii
import urllib.request
from typing import Annotated
from fastapi import APIRouter, Body, Depends, HTTPException, Header, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from mirrordash_core.config import load_config, save_config, find_module_config
from mirrordash_core.system import remount_ro, remount_rw, run_restart, get_available_resolutions, apply_system_settings, set_screen_power, is_wifi_hotspot_active
from mirrordash_core.module_loader import module_loader

PACKAGE_DIR = Path(__file__).parent.parent.resolve()
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


logger = logging.getLogger("mirrordash.core.api.admin")

router = APIRouter(prefix="/admin")

# ---------------------------------------------------------------------------
# Virtual Environment A/B Swapping Helpers
# ---------------------------------------------------------------------------

def get_venv_paths():
    """Get venv paths: (venv_link, active_path, next_path).
    Returns None if not running on a system with /storage/mirrordash.
    """
    from pathlib import Path
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
    from pathlib import Path
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
# Authentication
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str) -> str:
    """Hash a password using pbkdf2_hmac and sha256."""
    hash_bytes = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    )
    return binascii.hexlify(hash_bytes).decode('ascii')

async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """FastAPI dependency that validates the X-API-Key header against stored password."""
    config = load_config()
    auth = config.get("admin_auth")

    if not auth:
        raise HTTPException(status_code=403, detail="Admin password not set. Please complete setup.")

    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing password in X-API-Key header")

    expected_hash = auth.get("hash")
    salt = auth.get("salt")

    if not expected_hash or not salt:
        raise HTTPException(status_code=500, detail="Invalid admin auth config")

    provided_hash = hash_password(x_api_key, salt)
    if not secrets.compare_digest(provided_hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

@router.get("/auth/status")
async def get_auth_status() -> dict:
    config = load_config()
    setup_required = "admin_auth" not in config
    hotspot_active = await is_wifi_hotspot_active()
    return {
        "setup_required": setup_required,
        "wifi_hotspot_active": hotspot_active
    }

@router.post("/auth/setup")
async def setup_auth(body: dict = Body(...)) -> dict:
    password = body.get("password")
    if not password or len(password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")

    config = load_config()
    if "admin_auth" in config:
        raise HTTPException(status_code=400, detail="Password is already set")

    salt = secrets.token_hex(16)
    hashed_pw = hash_password(password, salt)

    config["admin_auth"] = {
        "hash": hashed_pw,
        "salt": salt
    }

    await remount_rw()
    try:
        save_config(config)
        return {"status": "success", "message": "Admin password set successfully"}
    finally:
        await remount_ro()

# ---------------------------------------------------------------------------
# Config schema validation helpers
# ---------------------------------------------------------------------------
def get_module_schema(plugin_class) -> dict | None:
    """Resolve module config schema from the plugin class variable or a standalone json file next to it."""
    # 1. Check for class variable config_schema
    schema = getattr(plugin_class, "config_schema", None)
    if callable(schema):
        schema = schema()
    if schema and isinstance(schema, dict):
        return schema

    # 2. Check for standalone config_schema.json or schema.json next to the module file
    import sys
    import json
    try:
        module_name = plugin_class.__module__
        module_obj = sys.modules.get(module_name)
        if module_obj and getattr(module_obj, "__file__", None):
            plugin_dir = os.path.dirname(os.path.abspath(module_obj.__file__))
            for filename in ("config_schema.json", "schema.json"):
                filepath = os.path.join(plugin_dir, filename)
                if os.path.isfile(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
    except Exception as e:
        logger.warning(f"Error loading standalone schema for {plugin_class}: {e}")
    return None

VALID_POSITIONS = {
    "top_left", "top_center", "top_right",
    "middle_left", "middle_center", "middle_right",
    "bottom_left", "bottom_center", "bottom_right"
}

def validate_config(config: dict) -> None:
    """Basic structural validation of the config dict. Raises ValueError on bad data."""
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object.")
    modules = config.get("modules")
    if modules is not None and not isinstance(modules, dict):
        raise ValueError("'modules' must be a JSON object.")

    if isinstance(modules, dict):
        # Discover entry point classes to validate against their config_schema
        import importlib.metadata
        eps_dict = {}
        for ep in importlib.metadata.entry_points(group='mymm.modules'):
            eps_dict[ep.name] = ep
        for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
            eps_dict[ep.name] = ep
        eps = list(eps_dict.values())

        schemas = {}
        for ep in eps:
            try:
                plugin_class = ep.load()
                schema = get_module_schema(plugin_class)
                if schema and "properties" in schema:
                    schemas[ep.name] = schema
            except Exception:
                pass

        for name, cfg in modules.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"Module '{name}' config must be a JSON object.")

            pos = cfg.get("position")
            if pos is not None and pos not in VALID_POSITIONS:
                raise ValueError(
                    f"Module '{name}' has invalid position '{pos}'. "
                    f"Valid positions: {sorted(VALID_POSITIONS)}"
                )

            # Perform schema-based property type and enum validation with normalized matching
            schema = None
            norm_name = name.replace('-', '_')
            for s_name, s_val in schemas.items():
                if s_name.replace('-', '_') == norm_name:
                    schema = s_val
                    break
            if schema and "properties" in schema:
                properties = schema["properties"]
                for key, val in cfg.items():
                    if key == "position":
                        continue
                    prop_schema = properties.get(key)
                    if not prop_schema:
                        continue

                    expected_type = prop_schema.get("type")
                    title = prop_schema.get("title", key)

                    if expected_type == "boolean":
                        if not isinstance(val, bool):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a boolean.")
                    elif expected_type == "integer":
                        if isinstance(val, bool) or not isinstance(val, int):
                            raise ValueError(f"Module '{name}' setting '{title}' must be an integer.")
                    elif expected_type == "number":
                        if isinstance(val, bool) or not isinstance(val, (int, float)):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a number.")
                    elif expected_type == "string":
                        if not isinstance(val, str):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a string.")

                    enum_list = prop_schema.get("enum")
                    if enum_list is not None:
                        if val not in enum_list:
                            raise ValueError(f"Module '{name}' setting '{title}' must be one of: {enum_list}")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/config", dependencies=[Depends(require_api_key)])
async def get_config() -> dict:
    return load_config()

@router.post("/config", dependencies=[Depends(require_api_key)])
async def update_config(config: dict = Body(...)) -> dict:
    logger.info("Admin requested configuration update.")
    try:
        validate_config(config)
    except ValueError as e:
        logger.warning(f"Configuration validation failed: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    await remount_rw()
    try:
        save_config(config)
        logger.info("Configuration saved successfully. Reloading modules.")

        # Apply system timezone if configured
        globals_cfg = config.get("globals", {})
        timezone = globals_cfg.get("timezone")
        if timezone:
            from mirrordash_core.system import apply_system_timezone
            asyncio.create_task(apply_system_timezone(timezone))

        asyncio.create_task(module_loader.reload_modules())
        return {"status": "success", "message": "Configuration updated"}
    finally:
        await remount_ro()

@router.post("/install", dependencies=[Depends(require_api_key)])
async def install_module(package_name: str = Body(..., embed=True)) -> dict:
    # Security validation: strict package naming check (PyPI-safe names only)
    if not re.match(r"^[a-zA-Z0-9\-_.@/]+$", package_name):
        raise HTTPException(status_code=400, detail="Invalid package name")
    # Disallow local path traversal
    if ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name")

    swap_info = await prepare_venv_next()
    await remount_rw()
    try:
        logger.info(f"Installing package: {package_name}")
        # Strip full environment to avoid leaking server secrets to subprocess
        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully installed {package_name}")
            if swap_info:
                await commit_venv_next(*swap_info)
            asyncio.create_task(run_restart())
            return {"status": "success", "message": f"Installed {package_name}. Restarting..."}
        else:
            err_msg = stderr.decode(errors="replace")
            logger.error(f"Failed to install {package_name}: {err_msg}")
            if swap_info:
                await revert_venv_next(*swap_info)
            raise HTTPException(status_code=500, detail=f"Installation failed: {err_msg}")
    except Exception as e:
        if swap_info:
            await revert_venv_next(*swap_info)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Installation failed: {e}")
    finally:
        await remount_ro()

@router.post("/update", dependencies=[Depends(require_api_key)])
async def update_module(package_name: str = Body(..., embed=True)) -> dict:
    import sys
    import importlib.metadata

    # Security validation: strict package naming check (PyPI-safe names only)
    if not re.match(r"^[a-zA-Z0-9\-_.@/]+$", package_name):
        raise HTTPException(status_code=400, detail="Invalid package name")
    # Disallow local path traversal
    if ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name")

    # Try resolving current version for rollback reference
    old_version = None
    for name_variant in (package_name, package_name.replace("-", "_"), package_name.replace("_", "-")):
        try:
            old_version = importlib.metadata.version(name_variant)
            break
        except importlib.metadata.PackageNotFoundError:
            continue

    swap_info = await prepare_venv_next()
    await remount_rw()
    try:
        logger.info(f"Upgrading package: {package_name} (current version: {old_version or 'unknown'})")
        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", "--upgrade", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")
            logger.error(f"Failed to upgrade {package_name}: {err_msg}")
            if swap_info:
                await revert_venv_next(*swap_info)
            raise HTTPException(status_code=500, detail=f"Upgrade failed: {err_msg}")

        logger.info(f"Successfully upgraded {package_name}. Verifying installation compatibility...")

        # Build check command
        python_bin = sys.executable
        check_cmd = [
            python_bin, "-c",
            f"from importlib.metadata import entry_points; "
            f"import mirrordash_core.app; "
            f"[ep.load() for g in ('mirrordash.modules', 'mymm.modules') for ep in entry_points(group=g) if ep.name in "
            f"('{package_name}', '{package_name.replace('-', '_')}', '{package_name.replace('_', '-')}')]"
        ]

        check_proc = await asyncio.create_subprocess_exec(
            *check_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        check_stdout, check_stderr = await check_proc.communicate()

        if check_proc.returncode == 0:
            logger.info(f"Upgrade check passed for {package_name}. Restarting server...")
            if swap_info:
                await commit_venv_next(*swap_info)
            asyncio.create_task(run_restart())
            return {"status": "success", "message": f"Upgraded {package_name}. Restarting..."}
        else:
            # Verification failed! Roll back.
            check_err = check_stderr.decode(errors="replace")
            logger.warning(f"Upgrade check failed for {package_name}: {check_err}. Initiating rollback...")
            if swap_info:
                await revert_venv_next(*swap_info)
            raise HTTPException(
                status_code=500,
                detail=f"Verification failed. Rolled back successfully. Error: {check_err}"
            )
    except Exception as e:
        if swap_info:
            await revert_venv_next(*swap_info)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Upgrade failed: {e}")
    finally:
        await remount_ro()

@router.post("/uninstall", dependencies=[Depends(require_api_key)])
async def uninstall_module(package_name: str = Body(..., embed=True)) -> dict:
    # Security validation: strict package naming check (PyPI-safe names only)
    if not re.match(r"^[a-zA-Z0-9\-_.@/]+$", package_name):
        raise HTTPException(status_code=400, detail="Invalid package name")
    # Disallow local path traversal
    if ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name")

    swap_info = await prepare_venv_next()
    await remount_rw()
    try:
        # Load and remove module config from config.json if configured
        config = load_config()
        modules_config = config.get("modules", {})
        norm_pkg = package_name.replace('-', '_')
        keys_to_delete = []
        for key in list(modules_config.keys()):
            if key == package_name or key.replace('-', '_') == norm_pkg:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del modules_config[key]
            logger.info(f"Removing configuration for module '{key}' on uninstall")

        if keys_to_delete:
            save_config(config)

        logger.info(f"Uninstalling package: {package_name}")
        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "uninstall", "-y", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully uninstalled {package_name}")
            if swap_info:
                await commit_venv_next(*swap_info)
            asyncio.create_task(run_restart())
            return {"status": "success", "message": f"Uninstalled {package_name}. Restarting..."}
        else:
            err_msg = stderr.decode()
            logger.error(f"Failed to uninstall {package_name}: {err_msg}")
            if swap_info:
                await revert_venv_next(*swap_info)
            raise HTTPException(status_code=500, detail=f"Uninstall failed: {err_msg}")
    except Exception as e:
        if swap_info:
            await revert_venv_next(*swap_info)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Uninstall failed: {e}")
    finally:
        await remount_ro()

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
        proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", "--upgrade", "mirrordash",
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

        proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", install_target,
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
        from pathlib import Path
        from mirrordash_core.config import get_base_dir
        base_dir = get_base_dir()
        modules_dir = Path(base_dir) / "modules"
        local_module_names = []
        if modules_dir.exists() and modules_dir.is_dir():
            for folder in modules_dir.iterdir():
                if folder.is_dir() and (folder / "pyproject.toml").exists():
                    logger.info(f"Rebuilding venv: installing local module {folder.name} in editable mode")
                    proc_local = await asyncio.create_subprocess_exec(
                        "uv", "pip", "install", "-e", str(folder),
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
                proc_pypi = await asyncio.create_subprocess_exec(
                    "uv", "pip", "install", mod_name,
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
    """Get root partition disk space usage."""
    import shutil
    try:
        total, used, free = shutil.disk_usage("/")
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


@router.get("/modules", dependencies=[Depends(require_api_key)])
async def list_modules() -> dict:
    """List all discovered entry-point modules and their config status."""
    import importlib.metadata
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
    eps = list(eps_dict.values())
    config = load_config()
    modules_config = config.get("modules", {})
    result = {}
    for ep in eps:
        name = ep.name

        # Load the configuration schema if defined in the plugin class or next to it
        schema = None
        try:
            plugin_class = ep.load()
            schema = get_module_schema(plugin_class)
        except Exception as e:
            logger.warning(f"Could not load schema for entry point '{name}': {e}")

        if not schema:
            schema = {
                "title": name.replace("mirrordash-", "").replace("mirrordash_", "").replace("mymm-", "").replace("mymm_", "").title(),
                "description": "No description available.",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "default": True,
                        "title": "Enabled",
                        "description": "Enable or disable this module."
                    },
                    "position": {
                        "type": "string",
                        "default": "middle_center",
                        "enum": ["top_left", "top_right", "middle_center", "bottom_left", "bottom_right"],
                        "title": "Screen Position",
                        "description": "Where to display this module on the mirror screen."
                    }
                }
            }

        # Guarantee that standard properties 'enabled' and 'position' are present
        if "properties" not in schema:
            schema["properties"] = {}
        if "enabled" not in schema["properties"]:
            schema["properties"]["enabled"] = {
                "type": "boolean",
                "default": True,
                "title": "Enabled",
                "description": "Enable or disable this module."
            }
        if "position" not in schema["properties"]:
            schema["properties"]["position"] = {
                "type": "string",
                "default": "middle_center",
                "enum": ["top_left", "top_right", "middle_center", "bottom_left", "bottom_right"],
                "title": "Screen Position",
                "description": "Where to display this module on the mirror screen."
            }

        cfg_key, module_cfg = find_module_config(modules_config, name)
        if module_cfg is None:
            module_cfg = {}
        result[name] = {
            "installed": True,
            "configured": cfg_key is not None,
            "enabled": module_cfg.get("enabled", True),
            "position": module_cfg.get("position", None),
            "package_name": ep.dist.name if ep.dist else name,
            "version": ep.dist.version if ep.dist else "0.0.0",
            "schema": schema
        }
    return {"modules": result}

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


@router.get("/logs", dependencies=[Depends(require_api_key)])
async def get_logs(type: str = "system", lines: int = 100, module: str | None = None) -> dict:
    logger.info(f"Admin requested logs: type={type}, lines={lines}, module={module}")
    if type not in ("system", "modules", "raspberry"):
        raise HTTPException(status_code=400, detail="Invalid log type")

    lines = min(max(1, lines), 1000)

    if type in ("system", "modules"):
        import os
        from mirrordash_core.config import get_base_dir
        log_dir = os.path.join(get_base_dir(), "logs")
        log_file = os.path.join(log_dir, "mirrordash.log")
        if not os.path.exists(log_file):
            log_file = os.path.join(log_dir, "mymagicmirror.log")

        if not os.path.exists(log_file):
            return {"logs": f"Log file not found at {log_file}."}

        try:
            filtered_lines = []
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()

            for line in reversed(all_lines):
                if len(filtered_lines) >= lines:
                    break

                is_module = "mirrordash.modules" in line or "mymm.modules" in line

                if type == "modules" and is_module:
                    if module:
                        norm_target = module.replace('-', '_').lower()
                        norm_line = line.replace('-', '_').lower()
                        short_target = norm_target.replace('mirrordash_', '').replace('mymm_', '')
                        if (f"mirrordash.modules.{norm_target}" in norm_line or
                            f"mirrordash.modules.{short_target}" in norm_line or
                            f"mymm.modules.{norm_target}" in norm_line or
                            f"mymm.modules.{short_target}" in norm_line):
                            filtered_lines.append(line)
                    else:
                        filtered_lines.append(line)
                elif type == "system" and not is_module:
                    filtered_lines.append(line)

            filtered_lines.reverse()
            return {"logs": "".join(filtered_lines)}
        except Exception as e:
            logger.error(f"Failed to read logs: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")

    elif type == "raspberry":
        # Run journalctl to get system logs
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-n", str(lines), "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {"logs": stdout.decode("utf-8", errors="ignore")}
        except Exception:
            pass

        # Fallback to /var/log/syslog
        try:
            syslog_path = "/var/log/syslog"
            if os.path.exists(syslog_path):
                with open(syslog_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                last_lines = all_lines[-lines:]
                return {"logs": "".join(last_lines)}
        except Exception:
            pass

        return {"logs": "System log fetching failed. Systemd journalctl is not available, and /var/log/syslog is unreadable."}


DISCOVERED_COMMUNITY_MODULES = [
    {
        "name": "mirrordash-clock",
        "title": "Clock Widget",
        "description": "Standard clock and date widget with 12h/24h formatting, localizations, and sleek layout sizes."
    }
]
_scan_task = None

async def run_pypi_modules_scan():
    """Periodically scan PyPI simple index for mirrordash-* packages."""
    global DISCOVERED_COMMUNITY_MODULES
    import gzip
    while True:
        try:
            logger.info("Scanning PyPI for mirrordash-* community modules...")
            loop = asyncio.get_running_loop()
            
            def _fetch_simple_index():
                url = "https://pypi.org/simple/"
                req = urllib.request.Request(
                    url, 
                    headers={"User-Agent": "MirrorDash/1.0", "Accept-Encoding": "gzip"}
                )
                try:
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        content = resp.read()
                        if resp.info().get("Content-Encoding") == "gzip":
                            content = gzip.decompress(content)
                        return content.decode("utf-8")
                except Exception as e:
                    logger.error(f"Failed to fetch PyPI simple index: {e}")
                    return ""

            html = await loop.run_in_executor(None, _fetch_simple_index)
            if html:
                # Find all package names starting with mirrordash-
                # Exclude mirrordash-core and mirrordash itself
                names = re.findall(r'<a href=\"/simple/(mirrordash-[^\"]+)/\">', html)
                names = sorted(list(set(n for n in names if n != "mirrordash" and n != "mirrordash-core")))
                
                # Fetch metadata for each discovered package
                scanned_modules = []
                for name in names:
                    def _fetch_meta():
                        url = f"https://pypi.org/pypi/{name}/json"
                        req = urllib.request.Request(url, headers={"User-Agent": "MirrorDash/1.0"})
                        try:
                            with urllib.request.urlopen(req, timeout=5) as resp:
                                return json.loads(resp.read().decode("utf-8"))
                        except Exception:
                            return None
                    
                    meta = await loop.run_in_executor(None, _fetch_meta)
                    if meta:
                        info = meta.get("info", {})
                        scanned_modules.append({
                            "name": name,
                            "title": info.get("name", name).replace("mirrordash-", "").replace("mirrordash_", "").title(),
                            "description": info.get("summary") or "No description available."
                        })
                    else:
                        scanned_modules.append({
                            "name": name,
                            "title": name.replace("mirrordash-", "").replace("mirrordash_", "").title(),
                            "description": "No description available."
                        })
                
                # Ensure clock is always included as fallback/pre-packaged
                scanned_names = {m["name"] for m in scanned_modules}
                if "mirrordash-clock" not in scanned_names:
                    scanned_modules.insert(0, {
                        "name": "mirrordash-clock",
                        "title": "Clock Widget",
                        "description": "Standard clock and date widget with 12h/24h formatting, localizations, and sleek layout sizes."
                    })
                
                DISCOVERED_COMMUNITY_MODULES = scanned_modules
                logger.info(f"PyPI scan completed. Discovered community modules: {[m['name'] for m in DISCOVERED_COMMUNITY_MODULES]}")
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error scanning PyPI for modules: {e}", exc_info=True)
            
        # Run scan every 12 hours
        await asyncio.sleep(43200)

def start_community_modules_scan():
    global _scan_task
    import sys
    if "pytest" in sys.modules:
        return
    if _scan_task is None:
        _scan_task = asyncio.create_task(run_pypi_modules_scan())

def stop_community_modules_scan():
    global _scan_task
    if _scan_task is not None:
        _scan_task.cancel()
        _scan_task = None


@router.get("/community-modules", dependencies=[Depends(require_api_key)])
async def list_community_modules() -> list[dict]:
    """Return a list of popular discoverable community modules on PyPI."""
    global DISCOVERED_COMMUNITY_MODULES
    return DISCOVERED_COMMUNITY_MODULES


@router.get("/globals-schema", dependencies=[Depends(require_api_key)])
async def get_globals_schema() -> dict:
    """Return the JSON schema defining global configuration settings."""
    return {
        "title": "Global Settings",
        "description": "System-wide preferences inherited by all modules.",
        "properties": {
            "language": {
                "type": "string",
                "default": "en",
                "title": "System Language",
                "description": "Language for translations (e.g. en, sv, de, fr, nl)."
            },
            "timezone": {
                "type": "string",
                "default": "Europe/Stockholm",
                "title": "Timezone",
                "description": "System timezone (e.g. Europe/Stockholm, America/New_York)."
            },
            "time_format": {
                "type": "string",
                "default": "24h",
                "enum": ["24h", "12h"],
                "title": "Clock Time Format",
                "description": "Global standard for clocks and times."
            },
            "temperature_unit": {
                "type": "string",
                "default": "C",
                "enum": ["C", "F"],
                "title": "Temperature Unit",
                "description": "Unit for thermometer and weather readouts."
            },
            "distance_unit": {
                "type": "string",
                "default": "km",
                "enum": ["km", "miles"],
                "title": "Distance Unit",
                "description": "Unit for travel, range, and maps."
            },
            "latitude": {
                "type": "number",
                "default": 59.3293,
                "title": "Decimal Latitude",
                "description": "Latitude coordinates for weather/astronomy."
            },
            "longitude": {
                "type": "number",
                "default": 18.0686,
                "title": "Decimal Longitude",
                "description": "Longitude coordinates for weather/astronomy."
            }
        }
    }


# ---------------------------------------------------------------------------
# HTMX Panel Handlers
# ---------------------------------------------------------------------------

def render_validation_summary(filename: str, manifest: dict, password: str = "", is_local: bool = False) -> str:
    modules = manifest.get("modules", [])
    modules_list_items = []
    for mod in modules:
        package_name = mod.get("package_name")
        version = mod.get("version")
        mod_type = mod.get("type", "pypi")
        type_badge = '<span style="color: #66ff66;">[Local Source]</span>' if mod_type == "local" else '<span style="color: #66b3ff;">[PyPI Package]</span>'
        modules_list_items.append(f"<li><strong>{package_name}</strong> (v{version}) - {type_badge}</li>")
        
    modules_list_html = "\n".join(modules_list_items)
    password_input = f'<input type="hidden" name="password" value="{password}">' if password else ''
    
    return f"""
    <section class="card" id="backup-validation-panel">
        <h2><i class="fas fa-clipboard-check"></i> Verify Import Contents</h2>
        <div class="validation-summary">
            <div class="validation-stat">
                <span class="label">Manifest Version:</span>
                <span class="value">{manifest.get('backup_version', '1.0')}</span>
            </div>
            <div class="validation-stat">
                <span class="label">Backup Timestamp:</span>
                <span class="value">{manifest.get('timestamp', '-')}</span>
            </div>
            <div class="validation-stat">
                <span class="label">Modules Included:</span>
                <span class="value">{len(modules)}</span>
            </div>
        </div>
        <div class="validation-modules-list" style="margin-top: 1rem;">
            <h4>Restored Modules Listing:</h4>
            <ul>
                {modules_list_html}
            </ul>
        </div>
        <div class="validation-actions" style="margin-top: 1.5rem; display: flex; gap: 10px;">
            <form hx-post="/admin/panels/backup/restore" 
                  hx-target="#backup-validation-panel" 
                  hx-swap="outerHTML"
                  onclick="this.querySelector('button').disabled = true; this.querySelector('span').innerText = 'Restoring...';">
                <input type="hidden" name="filename" value="{filename}">
                <input type="hidden" name="is_local" value="{"true" if is_local else "false"}">
                {password_input}
                <button type="submit" class="btn primary">
                    <i class="fas fa-exclamation-triangle"></i> <span>Start Restoration</span>
                </button>
            </form>
            <button type="button" class="btn secondary" onclick="document.getElementById('backup-validation-panel').remove()">
                Cancel
            </button>
        </div>
    </section>
    """


def render_password_prompt(filename: str, is_local: bool) -> str:
    action_url = "/admin/panels/backup/validate-password"
    is_local_input = f'<input type="hidden" name="is_local" value="{"true" if is_local else "false"}">'
    return f"""
    <section class="card" id="backup-password-prompt-panel" style="margin-top: 1rem;">
        <h2><i class="fas fa-lock"></i> Encrypted Backup</h2>
        <p>This backup file is encrypted. Please enter the password to decrypt and validate it:</p>
        <form hx-post="{action_url}" hx-target="#backup-upload-target" hx-swap="innerHTML" style="margin-top: 1rem;">
            <input type="hidden" name="filename" value="{filename}">
            {is_local_input}
            <div class="form-group">
                <input type="password" name="password" class="form-control" placeholder="Enter password" required>
            </div>
            <div style="margin-top: 1rem; display: flex; gap: 10px;">
                <button type="submit" class="btn primary">Verify Password</button>
                <button type="button" class="btn secondary" onclick="document.getElementById('backup-password-prompt-panel').remove()">Cancel</button>
            </div>
        </form>
    </section>
    """


@router.get("/panels/config", dependencies=[Depends(require_api_key)])
async def get_panel_config(request: Request):
    config = load_config()
    globals_schema = await get_globals_schema()
    globals_data = config.get("globals", {})
    
    from mirrordash_core.api.form_generator import render_schema_form
    visual_form_html = render_schema_form(globals_schema, globals_data, "globals")
    raw_json_str = json.dumps(globals_data, indent=2)
    
    return templates.TemplateResponse(
        request=request,
        name="admin_config.html",
        context={
            "visual_form_html": visual_form_html,
            "raw_json_str": raw_json_str
        }
    )


@router.post("/panels/config/save-visual", dependencies=[Depends(require_api_key)])
async def save_panel_config_visual(request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v
            
    from mirrordash_core.api.form_generator import parse_flat_form_data, cast_values_by_schema
    parsed = parse_flat_form_data(flat_data)
    
    globals_data = parsed.get("globals", {})
    globals_schema = await get_globals_schema()
    globals_data = cast_values_by_schema(globals_data, globals_schema)
    
    config = load_config()
    config["globals"] = globals_data
    
    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
        
    await module_loader.reload_modules()
    
    raw_json_str = json.dumps(globals_data, indent=2).replace("`", "\\`").replace("${", "\\${")
    
    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Global settings saved successfully.</div>
        <script>
            showGlobal('Global settings saved successfully.', 'success');
            const editor = document.getElementById('config-editor');
            if (editor) editor.value = `{raw_json_str}`;
        </script>
    """)
    return response


@router.post("/panels/config/save-raw", dependencies=[Depends(require_api_key)])
async def save_panel_config_raw(request: Request):
    form_data = await request.form()
    raw_json = form_data.get("raw_json", "").strip()
    
    try:
        globals_data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return HTMLResponse(content=f'<div class="alert alert--error">Invalid JSON: {str(e)}</div>')
        
    config = load_config()
    config["globals"] = globals_data
    
    try:
        validate_config(config)
    except ValueError as e:
        return HTMLResponse(content=f'<div class="alert alert--error">Validation failed: {str(e)}</div>')
        
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
        
    await module_loader.reload_modules()
    
    globals_schema = await get_globals_schema()
    from mirrordash_core.api.form_generator import render_schema_form
    visual_form_html = render_schema_form(globals_schema, globals_data, "globals")
    
    escaped_html = visual_form_html.replace("`", "\\`").replace("${", "\\${")
    
    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Global settings saved successfully.</div>
        <script>
            showGlobal('Global settings saved successfully.', 'success');
            const container = document.getElementById('visual-form-container');
            if (container) container.innerHTML = `{escaped_html}`;
            triggerLucide();
        </script>
    """)
    return response


@router.get("/panels/config/add-array-item", dependencies=[Depends(require_api_key)])
async def add_array_item_route(
    name_prefix: str,
    array_key: str,
    index: int,
    item_title: str
):
    sub_properties = {}
    if name_prefix == "globals":
        schema = await get_globals_schema()
        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
    elif name_prefix.startswith("modules["):
        match = re.match(r"^modules\[([^\]]+)\]", name_prefix)
        if match:
            module_name = match.group(1)
            import importlib.metadata
            eps_dict = {}
            for ep in importlib.metadata.entry_points(group='mymm.modules'):
                eps_dict[ep.name] = ep
            for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
                eps_dict[ep.name] = ep
            ep = eps_dict.get(module_name) or eps_dict.get(module_name.replace("-", "_"))
            if ep:
                try:
                    plugin_class = ep.load()
                    schema = get_module_schema(plugin_class)
                    if schema:
                        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
                except Exception:
                    pass
                    
    from mirrordash_core.api.form_generator import render_array_item
    html = render_array_item(
        name_prefix=name_prefix,
        array_key=array_key,
        sub_properties=sub_properties,
        index=index,
        item_val={},
        item_title=item_title
    )
    return HTMLResponse(content=html)


@router.get("/panels/modules", dependencies=[Depends(require_api_key)])
async def get_panel_modules(request: Request):
    installed = await list_modules()
    installed_modules = installed.get("modules", {})
    
    query = request.query_params.get("query", "").strip().lower()
    if query:
        filtered_installed = {}
        for name, meta in installed_modules.items():
            title = meta.get("schema", {}).get("title", name).lower()
            if query in name.lower() or query in title:
                filtered_installed[name] = meta
        installed_modules = filtered_installed
        
    community = await list_community_modules()
    
    discoverable = []
    for m in community:
        name = m.get("name")
        if name not in installed_modules:
            title = m.get("title", "")
            description = m.get("description", "")
            if not query or (query in name.lower() or query in title.lower() or query in description.lower()):
                discoverable.append(m)
                
    disk_usage = await get_disk_usage()
    
    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "installed_modules": installed_modules,
            "discoverable_modules": discoverable,
            "disk_usage": disk_usage,
            "query": query
        }
    )


@router.get("/panels/modules/config/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_config_form(module_name: str):
    import importlib.metadata
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
        
    ep = eps_dict.get(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")
        
    schema = None
    try:
        plugin_class = ep.load()
        schema = get_module_schema(plugin_class)
    except Exception as e:
        logger.warning(f"Could not load schema for '{module_name}': {e}")
        
    if not schema:
        schema = {
            "title": module_name.replace("mirrordash-", "").replace("mirrordash_", "").title(),
            "properties": {
                "enabled": {"type": "boolean", "default": True, "title": "Enabled"},
                "position": {
                    "type": "string",
                    "default": "middle_center",
                    "enum": ["top_left", "top_right", "middle_center", "bottom_left", "bottom_right"],
                    "title": "Screen Position"
                }
            }
        }
        
    if "properties" not in schema:
        schema["properties"] = {}
    if "enabled" not in schema["properties"]:
        schema["properties"]["enabled"] = {"type": "boolean", "default": True, "title": "Enabled", "description": "Enable or disable this module."}
    if "position" not in schema["properties"]:
        schema["properties"]["position"] = {
            "type": "string",
            "default": "middle_center",
            "enum": [
                "top_bar", "top_left", "top_center", "top_right", "upper_third", 
                "middle_left", "middle_center", "middle_right", "lower_third", 
                "bottom_left", "bottom_center", "bottom_right", "bottom_bar"
            ],
            "title": "Screen Position"
        }
        
    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key, module_cfg = find_module_config(modules_config, module_name)
    if module_cfg is None:
        module_cfg = {}
        
    from mirrordash_core.api.form_generator import render_schema_form
    name_prefix = f"modules[{cfg_key or module_name.replace('_', '-')}]"
    form_html = render_schema_form(schema, module_cfg, name_prefix)
    
    save_url = f"/admin/panels/modules/config/{module_name}/save"
    remove_url = f"/admin/panels/modules/config/{module_name}/remove"
    
    return HTMLResponse(content=f"""
        <form hx-post="{save_url}" hx-target="#global-status" hx-swap="innerHTML" style="background: rgba(255,255,255,0.02); padding: 1.25rem; border-radius: 6px; border: 1px solid #27272a;">
            <h4 style="margin: 0 0 15px 0; color: white; font-size: 1rem;"><i class="fas fa-sliders-h" style="margin-right: 6px; color: var(--accent-color);"></i>Configuration Parameters</h4>
            {form_html}
            
            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
                <button type="button" class="btn danger btn-sm"
                        hx-post="{remove_url}"
                        hx-target="#global-status"
                        hx-confirm="Are you sure you want to deactivate and remove this module from the mirror screen?">
                    <i class="fas fa-times"></i> Remove from Mirror
                </button>
                <button type="submit" class="btn primary btn-sm">
                    <i class="fas fa-save"></i> Save Configuration
                </button>
            </div>
        </form>
        <script>triggerLucide();</script>
    """)


@router.post("/panels/modules/config/{module_name}/save", dependencies=[Depends(require_api_key)])
async def save_module_config_route(module_name: str, request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v
            
    from mirrordash_core.api.form_generator import parse_flat_form_data, cast_values_by_schema
    parsed = parse_flat_form_data(flat_data)
    
    modules_dict = parsed.get("modules", {})
    if not modules_dict:
        raise HTTPException(status_code=400, detail="Invalid form data structure")
        
    cfg_key = list(modules_dict.keys())[0]
    module_cfg = modules_dict[cfg_key]
    
    import importlib.metadata
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
    ep = eps_dict.get(module_name)
    schema = None
    if ep:
        try:
            plugin_class = ep.load()
            schema = get_module_schema(plugin_class)
        except Exception:
            pass
            
    if schema:
        module_cfg = cast_values_by_schema(module_cfg, schema)
        
    config = load_config()
    if "modules" not in config:
        config["modules"] = {}
        
    config["modules"][cfg_key] = module_cfg
    
    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
        
    await module_loader.reload_modules()
    
    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Module configuration saved successfully.</div>
        <script>
            showGlobal('Module configuration saved successfully.', 'success');
            htmx.trigger("#installed-modules-container", "refreshModules");
        </script>
    """)
    return response


@router.post("/panels/modules/config/{module_name}/remove", dependencies=[Depends(require_api_key)])
async def remove_module_config_route(module_name: str):
    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key, _ = find_module_config(modules_config, module_name)
    
    if cfg_key in modules_config:
        del modules_config[cfg_key]
        
    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()
        
    await module_loader.reload_modules()
    
    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Module removed from mirror display.</div>
        <script>
            showGlobal('Module removed from mirror display.', 'success');
            htmx.trigger("#installed-modules-container", "refreshModules");
        </script>
    """)
    return response


@router.get("/panels/modules/check-update/{module_name}", dependencies=[Depends(require_api_key)])
async def check_module_update_route(module_name: str):
    import importlib.metadata
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
        
    ep = eps_dict.get(module_name)
    if not ep:
        return HTMLResponse(content="")
        
    package_name = ep.dist.name if ep.dist else module_name
    current_version = ep.dist.version if ep.dist else "0.0.0"
    
    def _fetch_pypi_info() -> dict | None:
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
            
    pypi_data = await asyncio.to_thread(_fetch_pypi_info)
    if not pypi_data:
        return HTMLResponse(content="")
        
    latest_version = pypi_data.get("info", {}).get("version", current_version)
    
    def _parse_version(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)
            
    is_newer = _parse_version(latest_version) > _parse_version(current_version)
    
    if is_newer:
        return HTMLResponse(content=f"""
            <div id="update-badge-{module_name}" hx-swap-oob="true">
                <span class="status-badge update-avail" style="margin-left: 8px;">Update Available (v{latest_version})</span>
            </div>
            <div id="update-actions-{module_name}" hx-swap-oob="true" style="display: flex; gap: 8px; align-items: center;">
                <button class="btn secondary btn-sm"
                        hx-get="/admin/panels/modules/notes/{module_name}"
                        hx-target="#notes-modal-content-container"
                        onclick="document.getElementById('notes-modal').style.display='flex'; document.getElementById('notes-modal').classList.add('open');">
                    <i class="fas fa-file-alt"></i> Notes
                </button>
                <button class="btn primary btn-sm"
                        hx-post="/admin/panels/modules/upgrade"
                        hx-vals='{{"package_name": "{package_name}"}}'
                        hx-target="#global-status"
                        hx-confirm="Are you sure you want to upgrade {package_name} to v{latest_version}?">
                    <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
                </button>
            </div>
        """)
    else:
        return HTMLResponse(content="")


@router.get("/panels/modules/notes/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_notes(module_name: str):
    import importlib.metadata
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
        
    ep = eps_dict.get(module_name)
    if not ep:
        return HTMLResponse(content="Module not found.")
        
    package_name = ep.dist.name if ep.dist else module_name
    
    def _fetch_pypi_info() -> dict | None:
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
            
    pypi_data = await asyncio.to_thread(_fetch_pypi_info)
    if not pypi_data:
        return HTMLResponse(content="Failed to fetch release notes from PyPI.")
        
    info = pypi_data.get("info", {})
    description = info.get("description", "No release notes available.")
    latest_version = info.get("version", "0.0.0")
    
    return HTMLResponse(content=f"""
        <header class="modal-header">
            <div>
                <h2 id="modal-title"><i class="fas fa-file-alt"></i> {info.get('summary', module_name)} Release Notes</h2>
                <span id="modal-subtitle" class="modal-subtitle">{package_name} v{latest_version}</span>
            </div>
            <button id="modal-close-btn" class="modal-close-btn" aria-label="Close modal" onclick="closeReleaseNotesModal()">
                <i class="fas fa-times"></i>
            </button>
        </header>
        <div id="modal-body" class="modal-body">
            <textarea id="notes-markdown-source" style="display:none;">{description}</textarea>
            <div id="notes-rendered-content">Rendering...</div>
        </div>
        <footer class="modal-footer">
            <button id="modal-update-btn" class="btn primary"
                    hx-post="/admin/panels/modules/upgrade"
                    hx-vals='{{"package_name": "{package_name}"}}'
                    hx-target="#global-status"
                    hx-confirm="Are you sure you want to upgrade {package_name} to v{latest_version}?"
                    onclick="closeReleaseNotesModal()">
                <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
            </button>
            <button class="btn secondary" onclick="closeReleaseNotesModal()">Close</button>
        </footer>
        <script>
            renderNotesMarkdown();
        </script>
    """)


@router.post("/panels/modules/install", dependencies=[Depends(require_api_key)])
async def install_panel_module(package_name: str = Form(...)):
    try:
        res = await install_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully installed {package_name}! System is restarting...</div>
            <script>
                showGlobal('Successfully installed {package_name}. Restarting...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Installation failed: {err_detail}</div>')


@router.post("/panels/modules/uninstall", dependencies=[Depends(require_api_key)])
async def uninstall_panel_module(package_name: str = Form(...)):
    try:
        res = await uninstall_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully uninstalled {package_name}! System is restarting...</div>
            <script>
                showGlobal('Successfully uninstalled {package_name}. Restarting...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Uninstall failed: {err_detail}</div>')


@router.post("/panels/modules/upgrade", dependencies=[Depends(require_api_key)])
async def upgrade_panel_module(package_name: str = Form(...)):
    try:
        res = await update_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully upgraded {package_name}! System is restarting...</div>
            <script>
                showGlobal('Successfully upgraded {package_name}. Restarting...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Upgrade failed: {err_detail}</div>')


@router.get("/panels/logs", dependencies=[Depends(require_api_key)])
async def get_panel_logs(request: Request):
    log_data = await get_logs(type="system", lines=100)
    logs_content = log_data.get("logs", "No logs found.")
    
    modules_list = []
    for name in module_loader.instances.keys():
        modules_list.append(name)
        
    return templates.TemplateResponse(
        request=request,
        name="admin_logs.html",
        context={
            "logs_content": logs_content,
            "modules_list": modules_list
        }
    )


@router.get("/panels/logs/viewer", dependencies=[Depends(require_api_key)])
async def get_logs_viewer(type: str = "system", lines: int = 100, module: str | None = None):
    log_data = await get_logs(type=type, lines=lines, module=module)
    logs_content = log_data.get("logs", "No logs found.")
    return HTMLResponse(content=logs_content)


@router.get("/panels/system", dependencies=[Depends(require_api_key)])
async def get_panel_system(request: Request):
    config = load_config()
    globals_cfg = config.get("globals", {})
    time_format = globals_cfg.get("time_format", "24h")
    
    settings_data = await get_system_settings()
    settings = settings_data.get("settings", {})
    resolutions = settings_data.get("resolutions", [])
    
    # Parse current active times
    display_control = settings.get("display_control", {})
    interval = display_control.get("interval", {"start": "07:00", "end": "22:00"})
    start_time_str = interval.get("start", "07:00")
    end_time_str = interval.get("end", "22:00")
    
    # Helper to parse 24h string to (hour, minute, ampm)
    def parse_time_to_format(time_str: str, fmt: str):
        try:
            h_str, m_str = time_str.split(":")
            h = int(h_str)
            m = int(m_str)
        except Exception:
            h, m = 7, 0
            
        if fmt == "12h":
            ampm = "PM" if h >= 12 else "AM"
            h_12 = h % 12
            if h_12 == 0:
                h_12 = 12
            return h_12, m, ampm
        else:
            return h, m, None
            
    start_h, start_m, start_ampm = parse_time_to_format(start_time_str, time_format)
    end_h, end_m, end_ampm = parse_time_to_format(end_time_str, time_format)
    
    # Hours list
    if time_format == "12h":
        hours_list = list(range(1, 13))
    else:
        hours_list = list(range(0, 24))
        
    minutes_list = list(range(0, 60))
    
    current_version = "unknown"
    for pkg_name in ("mirrordash", "mirrordash-core", "mirrordash_core"):
        try:
            current_version = importlib.metadata.version(pkg_name)
            break
        except importlib.metadata.PackageNotFoundError:
            continue
            
    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context={
            "settings": settings,
            "resolutions": resolutions,
            "current_version": current_version,
            "time_format": time_format,
            "start_h": start_h,
            "start_m": start_m,
            "start_ampm": start_ampm,
            "end_h": end_h,
            "end_m": end_m,
            "end_ampm": end_ampm,
            "hours_list": hours_list,
            "minutes_list": minutes_list
        }
    )


@router.post("/panels/system/save", dependencies=[Depends(require_api_key)])
async def save_system_settings_route(request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v
            
    from mirrordash_core.api.form_generator import parse_flat_form_data
    parsed = parse_flat_form_data(flat_data)
    
    # Format times back to HH:MM strings expected by update_system_settings
    display_control = parsed.get("display_control", {})
    interval = display_control.get("interval", {})
    if "start_h" in interval and "start_m" in interval:
        h = int(interval["start_h"])
        m = interval["start_m"]
        ampm = interval.get("start_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        display_control["interval"] = {
            "start": f"{h:02d}:{m}"
        }
    if "end_h" in interval and "end_m" in interval:
        h = int(interval["end_h"])
        m = interval["end_m"]
        ampm = interval.get("end_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        if "interval" not in display_control:
            display_control["interval"] = {}
        display_control["interval"]["end"] = f"{h:02d}:{m}"
        
    try:
        parsed["brightness"] = int(parsed.get("brightness", 100))
        parsed["volume"] = int(parsed.get("volume", 80))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Brightness and volume must be integers")
        
    if "pir" in display_control:
        pir = display_control["pir"]
        try:
            pir["pin"] = int(pir.get("pin", 18))
            pir["timeout_minutes"] = int(pir.get("timeout_minutes", 5))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="PIR pin and timeout must be integers")
    if "button" in display_control:
        btn = display_control["button"]
        try:
            btn["pin"] = int(btn.get("pin", 23))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Button pin must be an integer")
            
    res = await update_system_settings(settings=parsed)
    
    return HTMLResponse(content=f"""
        <div class="alert alert--success">System settings applied successfully.</div>
        <script>
            showGlobal('System settings applied successfully.', 'success');
        </script>
    """)


@router.post("/panels/system/screen", dependencies=[Depends(require_api_key)])
async def post_panel_screen(request: Request):
    form_data = await request.form()
    state = form_data.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state")
        
    from mirrordash_core.display_power import display_power_manager
    asyncio.create_task(display_power_manager.set_state(state == "on"))
    
    return HTMLResponse(content=f"""
        <div class="alert alert--success">Screen turned {state.upper()} successfully.</div>
        <script>
            showGlobal('Screen turned {state.upper()} successfully.', 'success');
        </script>
    """)


@router.get("/panels/system/update-check", dependencies=[Depends(require_api_key)])
async def get_system_update_check():
    try:
        data = await check_core_update()
    except Exception as e:
        return HTMLResponse(content=f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for updates: {str(e)}</div>')
        
    current = data.get("current_version", "—")
    latest = data.get("latest_version", "—")
    avail = data.get("update_available", False)
    
    if avail:
        return HTMLResponse(content=f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #10b981;"><strong>Update available!</strong> New version v{latest} is available (currently installed: v{current}).</p>
                <button type="button" 
                        class="btn primary btn-sm" 
                        style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Are you sure you want to upgrade MirrorDash Core to v{latest}? The system will reboot afterwards."
                        onclick="this.disabled=true; this.innerHTML='<i class=&quot;fas fa-spinner fa-spin&quot;></i> Upgrading...';">
                    Upgrade to v{latest} Now
                </button>
            </div>
        """)
    else:
        return HTMLResponse(content=f'<div style="margin-top: 10px; color: var(--text-muted);">Your system is up-to-date (v{current}).</div>')


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update():
    try:
        res = await update_core()
        return HTMLResponse(content=f"""
            <div class="alert alert--success" style="margin-top: 10px;">Upgrade initiated successfully. System is restarting. Please wait...</div>
            <script>
                showGlobal('Upgrade initiated. Restarting system...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        return HTMLResponse(content=f'<div class="status-msg error" style="margin-top: 10px;">Upgrade failed: {str(e)}</div>')


@router.get("/panels/backup", dependencies=[Depends(require_api_key)])
async def get_panel_backup(request: Request):
    from mirrordash_core.api.backup import list_backups
    data = await list_backups()
    backups = data.get("backups", [])
    
    # Process backup list for formatting
    processed_backups = []
    for backup in backups:
        from datetime import datetime
        created_at_formatted = backup["created_at"]
        try:
            dt = datetime.fromisoformat(backup["created_at"])
            created_at_formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        
        size_formatted = f"{backup['size_bytes'] / 1024:.1f} KB"
        
        processed_backups.append({
            "filename": backup["filename"],
            "created_at_formatted": created_at_formatted,
            "size_formatted": size_formatted,
            "encrypted": backup["encrypted"]
        })
        
    return templates.TemplateResponse(
        request=request,
        name="admin_backup.html",
        context={
            "backups": processed_backups
        }
    )


@router.get("/panels/backup/list", dependencies=[Depends(require_api_key)])
async def get_panel_backups_list(request: Request):
    from mirrordash_core.api.backup import list_backups
    data = await list_backups()
    backups = data.get("backups", [])
    if not backups:
        return HTMLResponse(content='<tr><td colspan="5" style="text-align: center; color: #999;">No backups saved.</td></tr>')
        
    rows = []
    for backup in backups:
        from datetime import datetime
        created_at_formatted = backup["created_at"]
        try:
            dt = datetime.fromisoformat(backup["created_at"])
            created_at_formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
            
        size_formatted = f"{backup['size_bytes'] / 1024:.1f} KB"
        is_enc_badge = (
            '<span class="status-badge" style="background-color: #ffb300; color: #000; padding: 2px 6px; border-radius: 4px; font-size: 0.8rem;"><i class="fas fa-lock"></i> Yes</span>'
            if backup["encrypted"]
            else '<span style="color: #666;"><i class="fas fa-unlock"></i> No</span>'
        )
        
        rows.append(f"""
            <tr>
                <td><strong>{backup['filename']}</strong></td>
                <td>{created_at_formatted}</td>
                <td>{size_formatted}</td>
                <td>{is_enc_badge}</td>
                <td style="text-align: right;">
                    <a class="btn secondary btn-sm" href="/admin/backup/download/{backup['filename']}" download title="Download file"><i class="fas fa-download"></i></a>
                    <button class="btn primary btn-sm"
                            hx-post="/admin/panels/backup/validate-local"
                            hx-vals='{{"filename": "{backup['filename']}"}}'
                            hx-target="#backup-upload-target"
                            hx-swap="innerHTML"
                            title="Restore from local">
                        <i class="fas fa-undo"></i> Restore
                    </button>
                    <button class="btn btn-sm" style="background-color: #ff3333; color: white;"
                            hx-post="/admin/panels/backup/delete/{backup['filename']}"
                            hx-confirm="Are you sure you want to delete backup {backup['filename']}?"
                            hx-target="closest tr"
                            hx-swap="outerHTML"
                            title="Delete">
                        <i class="fas fa-trash"></i>
                    </button>
                </td>
            </tr>
        """)
    return HTMLResponse(content="\n".join(rows))


@router.post("/panels/backup/delete/{filename}", dependencies=[Depends(require_api_key)])
async def delete_panel_backup_route(filename: str):
    from mirrordash_core.api.backup import delete_backup
    try:
        await delete_backup(filename=filename)
        return HTMLResponse(content="")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/panels/backup/upload", dependencies=[Depends(require_api_key)])
async def upload_panel_backup(request: Request, file: UploadFile = File(...)):
    import shutil
    import zipfile
    import json
    from mirrordash_core.api.backup import BACKUPS_DIR, remount_rw, remount_ro
    
    if not file.filename.endswith(".mirror"):
        return HTMLResponse(content='<div class="alert alert--error">Invalid file type. File must have .mirror extension.</div>')
        
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    await remount_rw()
    try:
        with open(temp_upload_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        is_encrypted = False
        try:
            with zipfile.ZipFile(temp_upload_path) as zf:
                zf.read("backup_manifest.json")
        except RuntimeError as e:
            if "encrypted" in str(e).lower():
                is_encrypted = True
            else:
                raise
        except Exception as e:
            logger.error(f"Failed to read uploaded file: {e}")
            return HTMLResponse(content='<div class="alert alert--error">Invalid or corrupt backup archive.</div>')
            
        if is_encrypted:
            return HTMLResponse(content=render_password_prompt(file.filename, is_local=False))
            
        with zipfile.ZipFile(temp_upload_path) as zf:
            manifest_bytes = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            
        return HTMLResponse(content=render_validation_summary(file.filename, manifest, is_local=False))
    finally:
        await remount_ro()


@router.post("/panels/backup/validate-local", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_local(filename: str = Form(...)):
    import shutil
    import zipfile
    import json
    from mirrordash_core.api.backup import BACKUPS_DIR, remount_rw, remount_ro
    
    if ".." in filename or "/" in filename or "\\" in filename:
        return HTMLResponse(content='<div class="alert alert--error">Invalid filename.</div>')
        
    file_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        return HTMLResponse(content='<div class="alert alert--error">Backup file not found.</div>')
        
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    await remount_rw()
    try:
        shutil.copy(file_path, temp_upload_path)
    finally:
        await remount_ro()
        
    is_encrypted = False
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.read("backup_manifest.json")
    except RuntimeError as e:
        if "encrypted" in str(e).lower():
            is_encrypted = True
        else:
            raise
            
    if is_encrypted:
        return HTMLResponse(content=render_password_prompt(filename, is_local=True))
        
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            manifest_bytes = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_bytes.decode('utf-8'))
        return HTMLResponse(content=render_validation_summary(filename, manifest, is_local=True))
    except Exception as e:
        logger.error(f"Error validating local backup: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Corrupt backup file.</div>')


@router.post("/panels/backup/validate-password", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_password(
    filename: str = Form(...),
    password: str = Form(...),
    is_local: bool = Form(...)
):
    import zipfile
    import json
    from mirrordash_core.api.backup import BACKUPS_DIR
    
    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    if not os.path.exists(temp_upload_path):
        return HTMLResponse(content='<div class="alert alert--error">No uploaded backup found to validate.</div>')
        
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.setpassword(password.encode('utf-8'))
            manifest_bytes = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            
        return HTMLResponse(content=render_validation_summary(filename, manifest, password, is_local))
    except RuntimeError:
        prompt_html = render_password_prompt(filename, is_local)
        error_msg = '<div class="alert alert--error" style="margin-bottom: 1rem;">Invalid backup password.</div>'
        return HTMLResponse(content=error_msg + prompt_html)
    except Exception as e:
        logger.error(f"Error validating password: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Corrupt backup file.</div>')


@router.post("/panels/backup/restore", dependencies=[Depends(require_api_key)])
async def restore_panel_backup(
    filename: str = Form(...),
    password: str | None = Form(default=None),
    is_local: bool = Form(default=False)
):
    from mirrordash_core.api.backup import restore_backup
    
    try:
        res = await restore_backup(password=password)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Backup restored successfully! System is restarting...</div>
            <script>
                showGlobal('Backup restored successfully! Restarting...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        logger.error(f"Restoration failed: {e}")
        return HTMLResponse(content=f'<div class="alert alert--error">Restoration failed: {str(e)}</div>')

@router.post("/panels/backup/create", dependencies=[Depends(require_api_key)])
async def create_panel_backup(request: Request):
    from mirrordash_core.api.backup import create_backup
    form_data = await request.form()
    encrypt = form_data.get("encrypt") == "true"
    password = form_data.get("password")
    
    if encrypt and (not password or len(password) < 4):
        return HTMLResponse(content="""
            <div class="alert alert--error">Password must be at least 4 characters for encryption.</div>
            <script>
                showGlobal('Password must be at least 4 characters for encryption.', 'error');
            </script>
        """)
        
    payload = {}
    if encrypt:
        payload["password"] = password
        
    res = await create_backup(payload=payload)
    filename = res.get("filename")
    
    return HTMLResponse(content=f"""
        <div class="alert alert--success">Backup {filename} generated successfully.</div>
        <script>
            showGlobal('Backup generated successfully.', 'success');
            htmx.trigger("#backups-list-tbody", "refreshBackups");
            const pwdInput = document.getElementById('backup-password');
            if (pwdInput) pwdInput.value = '';
        </script>
    """)


