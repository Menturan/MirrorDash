# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import UTC
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from mirrordash_core.admin import require_api_key
from mirrordash_core.config import load_config, save_config, version_key
from mirrordash_core.features.modules.loader import find_entry_point
from mirrordash_core.features.settings.schema import get_module_schema
from mirrordash_core.fetch import REMOTE_JSON_TTL, answered_at, fetch_json_cached
from mirrordash_core.host import run_restart
from mirrordash_core.venv import clean_uv_cache, run, uv_pip, venv_swap

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")


DISCOVERED_COMMUNITY_MODULES = []  # filled by scan_community_modules_now


GIT_URL = re.compile(r"^git\+https://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.git)?(?:@[a-zA-Z0-9_\-./]+)?$")


SAFE_NAME = re.compile(r"^[a-zA-Z0-9\-_.@/]+$")  # PyPI names and local paths


LOCAL_MODULE_DIRS = ("/opt/MirrorDash/modules", "/home/pi/mirrordash/modules")


NOT_MODULES = {"mirrordash", "mirrordash-core", "mirrordash-sdk"}  # mirrordash-* packages the community scan skips
# Modules from this GitHub account are marked Official. Only GitHub's own owner field counts:
# a PyPI package can name any repository in its metadata.
OFFICIAL_OWNER = "menturan"


GITHUB_REPO = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def github_repo(url: str | None) -> str | None:
    """https://github.com/owner/repo for a git, html or Project-URL pointing at a repository, else None."""
    m = GITHUB_REPO.search(url or "")
    return f"https://github.com/{m[1]}/{m[2]}" if m else None


def github_screenshot(repo: str | None) -> str | None:
    """The screenshot.png every module keeps at its repository root (the SDK's validator asks for it).
    Built, not fetched: raw.githubusercontent.com isn't the rate-limited API, and the admin's browser loads it."""
    return repo and repo.replace("https://github.com/", "https://raw.githubusercontent.com/", 1) + "/HEAD/screenshot.png"


def _installed_repo(dist) -> str | None:
    """The GitHub repository an installed module came from: its git URL (PEP 610) or a Project-URL."""
    try:
        urls = [json.loads(dist.read_text("direct_url.json") or "{}").get("url", "")]
    except (OSError, ValueError):
        urls = []
    urls += [u.split(",", 1)[-1].strip() for u in dist.metadata.get_all("Project-URL") or []]
    return next(filter(None, map(github_repo, urls)), None)


def github_module(item: dict) -> dict:
    """A discoverable module from one GitHub search result; everything comes from that one response."""
    repo = github_repo(item["html_url"])
    author = (item.get("owner") or {}).get("login")
    return {
        "name": item["name"],
        "install_name": f"git+{item['html_url']}.git",
        "title": item["name"].replace("mirrordash-", "").replace("-", " ").title(),
        "description": item.get("description") or "No description available.",
        "source": "github",
        "official": (author or "").lower() == OFFICIAL_OWNER,
        "author": author,
        "stars": item.get("stargazers_count", 0),
        "license": spdx if (spdx := (item.get("license") or {}).get("spdx_id")) != "NOASSERTION" else None,
        "updated": (item.get("pushed_at") or "")[:10] or None,
        "repo": repo,
        "screenshot": github_screenshot(repo),
    }


def module_icon(name: str, schema: dict | None) -> str:
    """The module's Font Awesome class (or inline <svg>) from its schema, else a guess from its name."""
    if schema and schema.get("icon"):
        return schema["icon"]
    # ponytail: the bundled modules declare no icon; any other module gets the puzzle piece
    guesses = {"clock": "fa-clock", "calendar": "fa-calendar-days", "weather": "fa-cloud-sun", "news": "fa-newspaper",
               "homeassistant": "fa-house", "namnsdag": "fa-calendar-check", "krisinformation": "fa-triangle-exclamation"}
    return next((icon for key, icon in guesses.items() if key in name), "fa-puzzle-piece")


def screen_layout() -> dict[str, list[dict]]:
    """Which instances sit at each screen position. Read from the config, so turned-off instances show
    too, and so does an instance whose module is no longer installed (module is None)."""
    eps = list(importlib.metadata.entry_points(group="mirrordash.modules"))
    layout = {}
    for inst_id, cfg in load_config().get("modules", {}).items():
        if not isinstance(cfg, dict):
            continue
        ep = find_entry_point(cfg.get("module", inst_id), eps)
        schema = None
        try:
            schema = get_module_schema(ep.load()) if ep else None
        except Exception as e:
            logger.warning(f"Could not load schema for '{inst_id}': {e}")
        base = re.sub(r"-\d+$", "", inst_id)
        name = (schema or {}).get("title") or re.sub(r"^mirrordash[-_]", "", base).replace("_", " ").replace("-", " ").title()
        if base != inst_id:
            name += " " + inst_id.rsplit("-", 1)[1]  # the second clock is "Clock 2"
        layout.setdefault(cfg.get("position", "middle_center"), []).append({
            "id": inst_id,
            "name": name,
            "icon": module_icon(ep.name if ep else inst_id, schema),
            "enabled": cfg.get("enabled", True),
            "module": ep.name if ep else None,
            "package": ep.dist.name if ep and ep.dist else None,
            "disabled_reason": cfg.get("disabled_reason"),  # e.g. turned off by an update to a newer Python
        })
    return layout


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


async def update_modules(*package_names: str) -> dict:
    """Upgrade one or more modules in one venv swap, so MirrorDash restarts once (Settings → Updates → Update All)."""
    variants = []
    for package_name in package_names:
        check_package_name(package_name)
        # A git URL's package is named after the repository (mirrordash-calendar)
        name = package_name.split("/")[-1].split(".git")[0].split("@")[0] if package_name.startswith("git+https://github.com/") else package_name
        variants += [name, name.replace("-", "_"), name.replace("_", "-")]

    async def loads_in_new_venv(python):
        """The upgraded modules must still import, or the swap is undone."""
        code, _, err = await run(python, "-c",
            "from importlib.metadata import entry_points; import mirrordash_core.app; "
            f"[ep.load() for ep in entry_points(group='mirrordash.modules') if ep.name in {tuple(variants)!r}]")
        if code != 0:
            logger.warning(f"Upgrade check failed for {', '.join(package_names)}: {err}. Initiating rollback...")
            raise HTTPException(status_code=500, detail=f"Verification failed. Rolled back successfully. Error: {err}")

    logger.info(f"Upgrading packages: {', '.join(package_names)}")
    await _change_packages("Upgrade", ["install", "--upgrade", *map(_local_target, package_names)], verify=loads_in_new_venv)
    return {"status": "success", "message": f"Upgraded {', '.join(package_names)}. Restarting..."}


async def check_module_update(module_name: str, max_age: float = REMOTE_JSON_TTL) -> dict | None:
    """An installed module (entry point name) against its latest release: GitHub's when it was installed from
    there (PEP 610 direct_url.json), else PyPI's. None when it isn't installed, else {"module_name", "package_name",
    "current_version", "latest_version" (None when up to date or nobody answered), "target" (what update_modules installs),
    "notes" (PyPI has release notes), "checked_at" (when the answer was fetched, seconds since 1970)}."""
    ep = find_entry_point(module_name)
    if not ep:
        return None
    package_name = ep.dist.name if ep.dist else module_name
    current_version = ep.dist.version if ep.dist else "0.0.0"
    result = {"module_name": module_name, "package_name": package_name, "current_version": current_version,
              "latest_version": None, "target": package_name, "notes": True, "checked_at": None}

    direct_url_info = None
    if ep.dist:
        try:
            url_data = ep.dist.read_text('direct_url.json')
            if url_data:
                direct_url_info = json.loads(url_data)
        except Exception:
            pass

    if direct_url_info and "vcs_info" in direct_url_info:
        vcs_info = direct_url_info["vcs_info"]
        url = direct_url_info.get("url", "")
        if vcs_info.get("vcs", "") == "git" and "github.com" in url:
            parts = url.rstrip("/").split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                api_url = f"https://api.github.com/repos/{parts[0]}/{parts[1].replace('.git', '')}/releases/latest"
                release_data = await fetch_json_cached(api_url, headers={"Accept": "application/vnd.github.v3+json"},
                                                       max_age=max_age)
                result["checked_at"] = answered_at(api_url)
                if release_data and release_data.get("tag_name"):
                    tag_name = release_data["tag_name"]
                    latest_version = tag_name.lstrip("v")
                    # Installed from exactly this release (upgrades install git+url@tag): up to date,
                    # even when the package's own version doesn't match the tag (v1.0.0 shipped 0.1.0).
                    on_latest = vcs_info.get("requested_revision") == tag_name
                    if not on_latest and version_key(latest_version) > version_key(current_version):
                        result.update(latest_version=latest_version, target=f"git+{url}@{tag_name}", notes=False)
        return result

    pypi_url = f"https://pypi.org/pypi/{package_name}/json"
    pypi_data = await fetch_json_cached(pypi_url, max_age=max_age)
    result["checked_at"] = answered_at(pypi_url)
    if pypi_data:
        latest_version = pypi_data.get("info", {}).get("version", current_version)
        if version_key(latest_version) > version_key(current_version):
            result["latest_version"] = latest_version
    return result


async def module_updates(max_age: float = REMOTE_JSON_TTL) -> tuple[list[dict], float | None]:
    """Every installed module with a newer release (check_module_update's dicts), and when the oldest
    answer was fetched: the honest "checked at" for all of them."""
    names = sorted({ep.name for ep in importlib.metadata.entry_points(group='mirrordash.modules')})
    checked = [c for c in await asyncio.gather(*(check_module_update(n, max_age) for n in names)) if c]
    times = [c["checked_at"] for c in checked if c["checked_at"]]
    return [c for c in checked if c["latest_version"]], min(times, default=None)


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
            "repo": (repo := _installed_repo(ep.dist) if ep.dist else None),
            "screenshot": github_screenshot(repo),
            "schema": schema,
            "icon": module_icon(name, schema),
            "instances": instances
        }
    return {"modules": result}


LAST_SCAN_TIMESTAMP = None


async def scan_community_modules_now():
    """Scan PyPI simple index and GitHub for mirrordash-* packages immediately."""
    global DISCOVERED_COMMUNITY_MODULES, LAST_SCAN_TIMESTAMP
    import gzip
    from datetime import datetime
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
        names = re.findall(r'<a href=\"/simple/(mirrordash-[^\"]+)/\">', html)
        names = sorted(set(names) - NOT_MODULES)

        def _fetch_meta(name: str) -> dict | None:
            url = f"https://pypi.org/pypi/{name}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "MirrorDash/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:  # one package's metadata failing must not stop the scan
                return None

        # Fetch metadata for each discovered package
        for name in names:
            meta = await loop.run_in_executor(None, _fetch_meta, name) or {}
            info = meta.get("info") or {}
            repo = next(filter(None, map(github_repo, (info.get("project_urls") or {}).values())), None)
            scanned_modules.append({
                "name": name,
                "install_name": name,
                "title": name.replace("mirrordash-", "").replace("mirrordash_", "").replace("-", " ").title(),
                "description": info.get("summary") or "No description available.",
                "source": "pypi",
                "official": False,
                "author": info.get("author") or None,
                "license": info.get("license_expression") or None,  # `license` is often the whole licence text
                "updated": ((meta.get("urls") or [{}])[0].get("upload_time") or "")[:10] or None,
                "repo": repo,
                "screenshot": github_screenshot(repo),
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
        if not repo_name.startswith("mirrordash-") or repo_name in NOT_MODULES:
            continue
        module = github_module(item)
        if repo_name in scanned_names:
            if not module["official"]:
                continue
            # A PyPI package can't take an official module's name: the official one is listed and installed
            scanned_modules = [m for m in scanned_modules if m["name"] != repo_name]
        scanned_modules.append(module)
        scanned_names.add(repo_name)

    if scanned_modules:
        DISCOVERED_COMMUNITY_MODULES = scanned_modules
        LAST_SCAN_TIMESTAMP = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
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
