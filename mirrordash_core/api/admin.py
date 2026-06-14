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
from fastapi import APIRouter, Body, Depends, HTTPException, Header
from mirrordash_core.config import load_config, save_config, find_module_config
from mirrordash_core.system import remount_ro, remount_rw, run_restart, get_available_resolutions, apply_system_settings, set_screen_power, is_wifi_hotspot_active
from mirrordash_core.module_loader import module_loader

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


@router.get("/community-modules", dependencies=[Depends(require_api_key)])
async def list_community_modules() -> list[dict]:
    """Return a list of popular discoverable community modules on PyPI."""
    return [
        {
            "name": "mirrordash-clock",
            "title": "Clock Widget",
            "description": "Standard clock and date widget with 12h/24h formatting, localizations, and sleek layout sizes."
        },
        {
            "name": "mirrordash-namnsdag",
            "title": "Swedish Namnsdag",
            "description": "Displays daily Swedish name days matching Swedish almanac registries."
        },
        {
            "name": "mirrordash-calendar",
            "title": "Detailed Calendar agenda",
            "description": "Supports standard iCalendar (.ics) subscriptions from Google, iCloud, or Outlook."
        },
        {
            "name": "mirrordash-weather",
            "title": "Weather Forecast",
            "description": "Displays real-time localized weather telemetry and barometric symbols."
        },
        {
            "name": "mirrordash-homeassistant",
            "title": "Home Assistant Integration",
            "description": "Displays real-time smart home sensor telemetry and device state tracking."
        },
        {
            "name": "mirrordash-news",
            "title": "News Feed RSS Reader",
            "description": "Cycles headlines from clean RSS feeds to keep you informed."
        }
    ]


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

