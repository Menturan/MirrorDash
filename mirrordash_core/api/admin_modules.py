# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import find_module_config, load_config, save_config
from mirrordash_core.module_loader import module_loader
from mirrordash_core.system import remount_ro, remount_rw

# Import helper functions from system router to avoid duplication
from mirrordash_core.api.admin_system import (
    commit_venv_next,
    prepare_venv_next,
    revert_venv_next,
    get_disk_usage,
)
from mirrordash_core.api.admin_config import get_module_schema, validate_config

logger = logging.getLogger("mirrordash.core.api.admin_modules")

router = APIRouter()

DISCOVERED_COMMUNITY_MODULES = []  # filled by scan_community_modules_now


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@router.post("/install", dependencies=[Depends(require_api_key)])
async def install_module(package_name: str = Body(..., embed=True)) -> dict:
    # Security validation: strict package naming check (PyPI-safe names or GitHub git+https URLs)
    is_git_url = package_name.startswith("git+https://github.com/") and re.match(r"^git\+https://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.git)?(?:@[a-zA-Z0-9_\-./]+)?$", package_name)
    is_pypi_or_path = re.match(r"^[a-zA-Z0-9\-_.@/]+$", package_name)
    if not (is_pypi_or_path or is_git_url):
        raise HTTPException(status_code=400, detail="Invalid package name or URL")
    # Disallow local path traversal
    if ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name or URL")

    # Enforce that GitHub modules must have an official release
    if package_name.startswith("git+https://github.com/"):
        url_part = package_name.split("github.com/")[-1].split("@")[0]
        parts = url_part.rstrip("/").split("/")
        if len(parts) >= 2:
            owner = parts[0]
            repo = parts[1].replace(".git", "")
            
            def _latest_release_tag():
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                    headers={"User-Agent": "MirrorDash/1.0", "Accept": "application/vnd.github.v3+json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8")).get("tag_name")

            try:
                tag = await asyncio.to_thread(_latest_release_tag)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    tag = None
                else:  # 403/429: the hourly limit of unauthenticated requests
                    raise HTTPException(status_code=503, detail="GitHub isn't answering right now (too many requests). Try again in an hour.")
            except OSError:
                raise HTTPException(status_code=503, detail="Cannot reach GitHub. Check the mirror's internet connection.")
            if not tag:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot install module: The GitHub repository does not have any official releases."
                )
            # Pin the release: updates compare the installed tag with the latest one
            if "@" not in package_name:
                package_name = f"{package_name}@{tag}"

    swap_info = await prepare_venv_next()
    await remount_rw()
    try:
        logger.info(f"Installing package: {package_name}")
        
        # Auto-resolve locally bundled modules
        local_target = package_name
        for base in ("/opt/MirrorDash/modules", "/home/pi/mirrordash/modules"):
            if Path(base, package_name).is_dir():
                local_target = str(Path(base, package_name))
                break

        # Strip full environment to avoid leaking server secrets to subprocess
        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        cmd = ["uv", "pip", "install"]
        if swap_info:
            active_path, next_path = swap_info
            cmd.extend(["--python", str(Path(next_path) / "bin" / "python")])
        cmd.append(local_target)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully installed {package_name}")
            
            # Clean uv cache
            try:
                clean_proc = await asyncio.create_subprocess_exec(
                    "uv", "cache", "clean",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=safe_env
                )
                await clean_proc.wait()
            except Exception as ce:
                logger.warning(f"Failed to clean uv cache after install: {ce}")

            if swap_info:
                await commit_venv_next(*swap_info)
            from mirrordash_core.system import run_restart
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

    # Security validation: strict package naming check (PyPI-safe names or GitHub git+https URLs)
    is_git_url = package_name.startswith("git+https://github.com/") and re.match(r"^git\+https://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.git)?(?:@[a-zA-Z0-9_\-./]+)?$", package_name)
    is_pypi_or_path = re.match(r"^[a-zA-Z0-9\-_.@/]+$", package_name)
    if not (is_pypi_or_path or is_git_url):
        raise HTTPException(status_code=400, detail="Invalid package name or URL")
    # Disallow local path traversal
    if ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name or URL")

    # Try resolving current version for rollback reference
    old_version = None
    # For git installs, the metadata check might be by repo name (e.g. mirrordash-calendar)
    # We resolve the package name from git url if it's a git url:
    clean_package_name = package_name
    if package_name.startswith("git+https://github.com/"):
        clean_package_name = package_name.split("/")[-1].split(".git")[0].split("@")[0]
        
    for name_variant in (clean_package_name, clean_package_name.replace("-", "_"), clean_package_name.replace("_", "-")):
        try:
            old_version = importlib.metadata.version(name_variant)
            break
        except importlib.metadata.PackageNotFoundError:
            continue

    swap_info = await prepare_venv_next()
    await remount_rw()
    try:
        logger.info(f"Upgrading package: {package_name} (current version: {old_version or 'unknown'})")
        
        # Auto-resolve locally bundled modules
        local_target = package_name
        for base in ("/opt/MirrorDash/modules", "/home/pi/mirrordash/modules"):
            if Path(base, package_name).is_dir():
                local_target = str(Path(base, package_name))
                break

        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        cmd = ["uv", "pip", "install", "--upgrade"]
        if swap_info:
            active_path, next_path = swap_info
            cmd.extend(["--python", str(Path(next_path) / "bin" / "python")])
        cmd.append(local_target)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
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

        # Build check command targeting the upgraded virtual environment
        if swap_info:
            active_path, next_path = swap_info
            candidate_bin = Path(next_path) / "bin" / "python"
            python_bin = str(candidate_bin) if candidate_bin.exists() else sys.executable
        else:
            python_bin = sys.executable

        check_cmd = [
            python_bin, "-c",
            f"from importlib.metadata import entry_points; "
            f"import mirrordash_core.app; "
            f"[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in "
            f"('{clean_package_name}', '{clean_package_name.replace('-', '_')}', '{clean_package_name.replace('_', '-')}')]"
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
            
            # Clean uv cache
            try:
                clean_proc = await asyncio.create_subprocess_exec(
                    "uv", "cache", "clean",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=safe_env
                )
                await clean_proc.wait()
            except Exception as ce:
                logger.warning(f"Failed to clean uv cache after update: {ce}")

            if swap_info:
                await commit_venv_next(*swap_info)
            from mirrordash_core.system import run_restart
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
        for key, cfg in list(modules_config.items()):
            if not isinstance(cfg, dict):
                continue
            mod_type = cfg.get("module", key)
            if mod_type == package_name or mod_type.replace('-', '_') == norm_pkg:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del modules_config[key]
            logger.info(f"Removing configuration for module instance '{key}' on uninstall")

        if keys_to_delete:
            save_config(config)

        logger.info(f"Uninstalling package: {package_name}")
        safe_env = {k: v for k, v in os.environ.items() if k in (
            "PATH", "HOME", "USER", "LANG", "LC_ALL", "VIRTUAL_ENV"
        )}
        cmd = ["uv", "pip", "uninstall", "-y"]
        if swap_info:
            active_path, next_path = swap_info
            cmd.extend(["--python", str(Path(next_path) / "bin" / "python")])
        cmd.append(package_name)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info(f"Successfully uninstalled {package_name}")
            
            # Clean uv cache
            try:
                clean_proc = await asyncio.create_subprocess_exec(
                    "uv", "cache", "clean",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=safe_env
                )
                await clean_proc.wait()
            except Exception as ce:
                logger.warning(f"Failed to clean uv cache after uninstall: {ce}")

            if swap_info:
                await commit_venv_next(*swap_info)
            from mirrordash_core.system import run_restart
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


async def list_modules() -> dict:
    """List all discovered entry-point modules and their config status (for the Modules panels)."""
    eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))
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

        # The panels only read title, icon and description; with no description they fall back to the package summary
        if not schema:
            schema = {"title": name.replace("mirrordash-", "").replace("mirrordash_", "").title()}

        instances = []
        for inst_id, inst_cfg in modules_config.items():
            if not isinstance(inst_cfg, dict):
                continue
            mod_type = inst_cfg.get("module", inst_id)
            if mod_type == name or mod_type.replace('-', '_') == name.replace('-', '_'):
                instances.append({
                    "id": inst_id,
                    "enabled": inst_cfg.get("enabled", True),
                    "position": inst_cfg.get("position", "middle_center"),
                    "title": inst_cfg.get("title", inst_id.replace("mirrordash_", "").replace("_", " ").title())
                })

        result[name] = {
            "installed": True,
            "configured": len(instances) > 0,
            "enabled": instances[0]["enabled"] if instances else False,
            "position": instances[0]["position"] if instances else None,
            "package_name": ep.dist.name if ep.dist else name,
            "version": ep.dist.version if ep.dist else "0.0.0",
            "summary": ep.dist.metadata["Summary"] if ep.dist else None,  # fallback when the schema has no description
            "schema": schema,
            "instances": instances
        }
    return {"modules": result}


LAST_SCAN_TIMESTAMP = None

async def scan_community_modules_now():
    """Scan PyPI simple index and GitHub for mirrordash-* packages immediately."""
    global DISCOVERED_COMMUNITY_MODULES, LAST_SCAN_TIMESTAMP
    import gzip
    from datetime import datetime, timezone
    logger.info("Scanning PyPI and GitHub for mirrordash-* community modules...")
    loop = asyncio.get_running_loop()

    scanned_modules = []
    scanned_names = set()

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
                    "install_name": name,
                    "title": info.get("name", name).replace("mirrordash-", "").replace("mirrordash_", "").title(),
                    "description": info.get("summary") or "No description available.",
                    "source": "pypi"
                })
            else:
                scanned_modules.append({
                    "name": name,
                    "install_name": name,
                    "title": name.replace("mirrordash-", "").replace("mirrordash_", "").title(),
                    "description": "No description available.",
                    "source": "pypi"
                })
            scanned_names.add(name)

    # GitHub: one search request. Unauthenticated, GitHub allows 60 requests an hour per IP address,
    # shared by everything on the home network, so the latest release is looked up only when a
    # module is installed (install_module), not here for every repository.
    logger.info("Scanning GitHub for mirrordash-* community modules...")
    def _fetch_github_repos():
        req = urllib.request.Request(
            "https://api.github.com/search/repositories?q=mirrordash-+in:name&per_page=100",
            headers={"User-Agent": "MirrorDash/1.0", "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        github_data = await loop.run_in_executor(None, _fetch_github_repos)
    except Exception as e:
        # Keep the list from the last scan; the admin page says the refresh failed
        logger.warning(f"Could not search GitHub for modules: {e}")
        raise
    for item in github_data.get("items", []):
        repo_name = item.get("name", "")
        if not repo_name.startswith("mirrordash-") or repo_name in scanned_names or repo_name in ("mirrordash-core", "mirrordash-sdk"):
            continue
        scanned_modules.append({
            "name": repo_name,
            "install_name": f"git+{item['html_url']}.git",
            "title": repo_name.replace("mirrordash-", "").replace("-", " ").title(),
            "description": item.get("description") or f"Community module from GitHub ({item.get('owner', {}).get('login')}).",
            "source": "github"
        })
        scanned_names.add(repo_name)

    if scanned_modules:
        DISCOVERED_COMMUNITY_MODULES = scanned_modules
        LAST_SCAN_TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(f"Scan completed. Discovered community modules: {[m['name'] for m in DISCOVERED_COMMUNITY_MODULES]}")


@router.get("/community-modules", dependencies=[Depends(require_api_key)])
async def list_community_modules() -> list[dict]:
    """Return a list of popular discoverable community modules on PyPI."""
    global DISCOVERED_COMMUNITY_MODULES, LAST_SCAN_TIMESTAMP
    if LAST_SCAN_TIMESTAMP is None:
        try:
            await scan_community_modules_now()
        except Exception as e:
            logger.error(f"Lazy scan of community modules failed: {e}")
    return DISCOVERED_COMMUNITY_MODULES

@router.post("/community-modules/scan", dependencies=[Depends(require_api_key)])
async def force_scan_community_modules():
    """Manually trigger a scan of community modules."""
    await scan_community_modules_now()
    return {"status": "success", "message": "Module list refreshed from GitHub and PyPI."}



