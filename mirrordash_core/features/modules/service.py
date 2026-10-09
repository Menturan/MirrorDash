# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from mirrordash_core.admin import require_api_key
from mirrordash_core.config import load_config, save_config
from mirrordash_core.host import run_restart
from mirrordash_core.venv import clean_uv_cache, run, uv_pip, venv_swap
from mirrordash_core.features.settings.schema import get_module_schema

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")


DISCOVERED_COMMUNITY_MODULES = []  # filled by scan_community_modules_now


GIT_URL = re.compile(r"^git\+https://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.git)?(?:@[a-zA-Z0-9_\-./]+)?$")


SAFE_NAME = re.compile(r"^[a-zA-Z0-9\-_.@/]+$")  # PyPI names and local paths


LOCAL_MODULE_DIRS = ("/opt/MirrorDash/modules", "/home/pi/mirrordash/modules")


def check_package_name(package_name: str, allow_git: bool = True) -> None:
    """Trust boundary: a PyPI-safe name, a local path or a GitHub git+https URL, never `..`."""
    ok = SAFE_NAME.match(package_name) or (allow_git and GIT_URL.match(package_name))
    if not ok or ".." in package_name:
        raise HTTPException(status_code=400, detail="Invalid package name or URL" if allow_git else "Invalid package name")


def _local_target(package_name: str) -> str:
    """A module bundled on the device installs from its folder."""
    return next((str(Path(base, package_name)) for base in LOCAL_MODULE_DIRS if Path(base, package_name).is_dir()), package_name)


async def _pinned_to_latest_release(package_name: str) -> str:
    """A GitHub module installs its latest release, pinned (`@tag`): updates compare that tag."""
    owner_repo = package_name.split("github.com/")[-1].split("@")[0].rstrip("/").split("/")
    if len(owner_repo) < 2:
        return package_name
    owner, repo = owner_repo[0], owner_repo[1].replace(".git", "")

    def latest_release_tag():
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers={"User-Agent": "MirrorDash/1.0", "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8")).get("tag_name")

    try:
        tag = await asyncio.to_thread(latest_release_tag)
    except urllib.error.HTTPError as e:
        if e.code != 404:  # 403/429: the hourly limit of unauthenticated requests
            raise HTTPException(status_code=503, detail="GitHub isn't answering right now (too many requests). Try again in an hour.")
        tag = None
    except OSError:
        raise HTTPException(status_code=503, detail="Cannot reach GitHub. Check the mirror's internet connection.")
    if not tag:
        raise HTTPException(status_code=400, detail="Cannot install module: The GitHub repository does not have any official releases.")
    return package_name if "@" in package_name else f"{package_name}@{tag}"


async def _change_packages(what: str, uv_args: list[str], verify=None) -> None:
    """Run one uv pip change in the next A/B venv; keep it only if uv (and `verify`) succeed."""
    try:
        async with venv_swap() as python:
            code, _, err = await uv_pip(python, *uv_args)
            if code != 0:
                logger.error(f"{what} failed: {err}")
                raise HTTPException(status_code=500, detail=f"{what} failed: {err}")
            if verify:
                await verify(python)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{what} failed: {e}")
    await clean_uv_cache()
    asyncio.create_task(run_restart())


async def install_module(package_name: str) -> dict:
    check_package_name(package_name)
    if package_name.startswith("git+https://github.com/"):
        package_name = await _pinned_to_latest_release(package_name)
    logger.info(f"Installing package: {package_name}")
    await _change_packages("Installation", ["install", _local_target(package_name)])
    return {"status": "success", "message": f"Installed {package_name}. Restarting..."}


async def update_module(package_name: str) -> dict:
    check_package_name(package_name)
    # A git URL's package is named after the repository (mirrordash-calendar)
    name = package_name.split("/")[-1].split(".git")[0].split("@")[0] if package_name.startswith("git+https://github.com/") else package_name
    variants = (name, name.replace("-", "_"), name.replace("_", "-"))

    async def loads_in_new_venv(python):
        """The upgraded module must still import, or the swap is undone."""
        code, _, err = await run(python, "-c",
            "from importlib.metadata import entry_points; import mirrordash_core.app; "
            f"[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in {variants!r}]")
        if code != 0:
            logger.warning(f"Upgrade check failed for {package_name}: {err}. Initiating rollback...")
            raise HTTPException(status_code=500, detail=f"Verification failed. Rolled back successfully. Error: {err}")

    logger.info(f"Upgrading package: {package_name}")
    await _change_packages("Upgrade", ["install", "--upgrade", _local_target(package_name)], verify=loads_in_new_venv)
    return {"status": "success", "message": f"Upgraded {package_name}. Restarting..."}


async def uninstall_module(package_name: str) -> dict:
    check_package_name(package_name, allow_git=False)
    # Its instances go too; a reinstall starts from defaults
    config = load_config()
    modules_config = config.get("modules", {})
    norm = package_name.replace("-", "_")
    instances = [k for k, cfg in modules_config.items()
                 if isinstance(cfg, dict) and cfg.get("module", k).replace("-", "_") == norm]
    for key in instances:
        logger.info(f"Removing configuration for module instance '{key}' on uninstall")
        del modules_config[key]
    if instances:
        save_config(config)
    logger.info(f"Uninstalling package: {package_name}")
    await _change_packages("Uninstall", ["uninstall", "-y", package_name])
    return {"status": "success", "message": f"Uninstalled {package_name}. Restarting..."}


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
