# SPDX-License-Identifier: AGPL-3.0-only
"""Installing, updating and removing modules (into the module host's site, ARCHITECTURE #41), checking
them for updates, and the list of community modules found on PyPI and GitHub."""

import asyncio
import contextlib
import gzip
import json
import logging
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from mirrordash_core.admin import require_api_key
from mirrordash_core.config import load_config, save_config, version_key
from mirrordash_core.features.kiosk import guard
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.module_files import (
    find_entry_point,
    module_entry_points,
    site_name,
)
from mirrordash_core.features.modules.package_names import package_key
from mirrordash_core.features.settings.schema import get_module_schema
from mirrordash_core.fetch import REMOTE_JSON_TTL, answered_at, fetch_json_cached
from mirrordash_core.host import run_in_background, run_restart

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")


DISCOVERED_COMMUNITY_MODULES = []  # filled by scan_community_modules_now
GIT_URL = re.compile(r"^git\+https://github\.com/[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.git)?(?:@[a-zA-Z0-9_\-./]+)?$")


SAFE_NAME = re.compile(r"^[a-zA-Z0-9\-_.@/]+$")  # PyPI names and local paths
# Where the OS image keeps modules bundled with it: one of these is installed from its folder
LOCAL_MODULE_DIRS = ("/opt/MirrorDash/modules", "/home/pi/mirrordash/modules")
NOT_MODULES = {"mirrordash", "mirrordash-core", "mirrordash-sdk"}  # mirrordash-* packages the community scan skips
# Modules from this GitHub account are marked Official. Only GitHub's own owner field counts:
# a PyPI package can name any repository in its metadata.
OFFICIAL_OWNER = "menturan"
GITHUB_REPO = re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:@.*)?$")


def github_owner_repo(url: str | None) -> tuple[str, str] | None:
    """(owner, repo) of a GitHub repository's URL: git+https://github.com/o/r.git@tag, an html or a
    Project-URL; None for anything else."""
    match = GITHUB_REPO.search(url or "")
    return (match[1], match[2]) if match else None


def github_repo(url: str | None) -> str | None:
    """https://github.com/owner/repo for a git, html or Project-URL pointing at a repository, else None."""
    owner_repo = github_owner_repo(url)
    return f"https://github.com/{owner_repo[0]}/{owner_repo[1]}" if owner_repo else None


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
    # A Project-URL is "Label, https://...": the address is after the comma
    urls += [project_url.split(",", 1)[-1].strip() for project_url in dist.metadata.get_all("Project-URL") or []]
    return next(filter(None, map(github_repo, urls)), None)


def github_module(item: dict) -> dict:
    """A discoverable module from one GitHub search result; everything comes from that one response."""
    repo = github_repo(item["html_url"])
    author = (item.get("owner") or {}).get("login")
    return {
        "name": item["name"],
        "install_name": f"git+{item['html_url']}.git",
        "title": _module_title(item["name"]),
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


SVG_ICON = re.compile(r"^data:image/svg\+xml;base64,[A-Za-z0-9+/=]+$")
FA_ICON = re.compile(r"^fa-[a-z0-9-]{1,40}$")


def module_icon(name: str, schema: dict | None) -> str:
    """The module's Font Awesome class, or its icon.svg as a data: URL (get_module_schema), from its schema,
    else a guess from its name. Anything else from a schema is ignored: it lands in the admin page."""
    icon = str((schema or {}).get("icon") or "")
    if SVG_ICON.match(icon) or FA_ICON.match(icon):
        return icon
    # ponytail: the bundled modules declare no icon; any other module gets the puzzle piece
    guesses = {"clock": "fa-clock", "calendar": "fa-calendar-days", "weather": "fa-cloud-sun", "news": "fa-newspaper",
               "homeassistant": "fa-house", "namnsdag": "fa-calendar-check", "krisinformation": "fa-triangle-exclamation"}
    return next((icon for key, icon in guesses.items() if key in name), "fa-puzzle-piece")


def instance_title(instance_id: str, instance_config: dict) -> str:
    """An instance's name in the admin page: its title, else its id made readable (mirrordash-clock-2: Clock-2)."""
    return instance_config.get("title", re.sub(r"^mirrordash[-_]", "", instance_id).replace("_", " ").title())


def screen_layout() -> dict[str, list[dict]]:
    """Which instances sit at each screen position. Read from the config, so turned-off instances show
    too, and so does an instance whose module is no longer installed (module is None)."""
    eps = module_entry_points()
    layout = {}
    for instance_id, instance_config in load_config().get("modules", {}).items():
        if not isinstance(instance_config, dict):
            continue
        ep = find_entry_point(instance_config.get("module", instance_id), eps)
        schema = get_module_schema(ep) if ep else None
        first_instance_id = re.sub(r"-\d+$", "", instance_id)  # "mirrordash-clock-2" -> "mirrordash-clock"
        name = (schema or {}).get("title") or \
            re.sub(r"^mirrordash[-_]", "", first_instance_id).replace("_", " ").replace("-", " ").title()
        if first_instance_id != instance_id:
            name += " " + instance_id.rsplit("-", 1)[1]  # the second clock is "Clock 2"
        layout.setdefault(instance_config.get("position", "middle_center"), []).append({
            "id": instance_id,
            "name": name,
            "icon": module_icon(ep.name if ep else instance_id, schema),
            "enabled": instance_config.get("enabled", True),
            "module": ep.name if ep else None,
            "package": ep.dist.name if ep and ep.dist else None,
            "disabled_reason": instance_config.get("disabled_reason"),  # e.g. turned off by an update to a newer Python
        })
    return layout


def check_package_name(package_name: str, allow_git: bool = True) -> None:
    """Trust boundary: a PyPI-safe name, a local path or a GitHub git+https URL, never `..`, and never
    one starting with "-", which uv would read as an option ("-r/some/file" installs what a file lists)."""
    ok = SAFE_NAME.match(package_name) or (allow_git and GIT_URL.match(package_name))
    if not ok or ".." in package_name or package_name.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid package name or URL" if allow_git else "Invalid package name")


def _local_target(package_name: str) -> str:
    """A module bundled on the device installs from its folder."""
    return next((str(Path(base, package_name)) for base in LOCAL_MODULE_DIRS if Path(base, package_name).is_dir()), package_name)


async def _pinned_to_latest_release(package_name: str) -> str:
    """A GitHub module installs its latest release, pinned (`@tag`): updates compare that tag."""
    if not (owner_repo := github_owner_repo(package_name)):
        return package_name
    owner, repo = owner_repo

    def latest_release_tag():
        request = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
            headers={"User-Agent": "MirrorDash/1.0", "Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8")).get("tag_name")

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


async def _change_packages(what: str, *changes: list[str]) -> None:
    """Run uv pip changes, one module each, in the next module site (module_host.packages, ARCHITECTURE
    #41); keep it only if uv succeeds."""
    try:
        async with module_host.packages() as site:
            for uv_arguments in changes:
                code, err = await site.pip(*uv_arguments)
                if code != 0:
                    logger.error(f"{what} failed: {err}")
                    raise HTTPException(status_code=500, detail=f"{what} failed: {err}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{what} failed: {e}")
    run_in_background(run_restart())


def _set_listed(key: str, name: str | None, listed: bool) -> None:
    """Put the module installed in site/<name> in config's list `key`, or take it out."""
    config = load_config()
    names = set(config.get(key, []))
    if name and (name in names) != listed:
        config[key] = sorted(names | {name} if listed else names - {name})
        save_config(config)


def allow_scripts(name: str | None, allowed: bool) -> None:
    """Whether the module installed in site/<name> runs its card's scripts on the screen (scripts_allowed)."""
    _set_listed("module_scripts", name, allowed)


def allow_auto_update(name: str | None, allowed: bool) -> None:
    """Whether the nightly update may update the module installed in site/<name> (modules_to_auto_update)."""
    _set_listed("module_auto_update_off", name, not allowed)


async def modules_to_auto_update(config: dict) -> list[dict]:
    """What the nightly update may install (module_updates' dicts): every module with a newer release,
    but not the ones the owner turned off in their settings (config's "module_auto_update_off")."""
    held = set(config.get("module_auto_update_off", []))
    updates, _ = await module_updates()
    return [update for update in updates
            if (ep := find_entry_point(update["module_name"])) is None or site_name(ep) not in held]


async def noted(work, *packages: str):
    """An install or update, then each package noted for the crash guard (its step 1: the module changed last)."""
    result = await work
    # only what was really upgraded: update_modules puts back one that didn't work
    for package in result.get("updated", packages) if isinstance(result, dict) else packages:
        guard.note("installed", package)
    return result


async def install_module(package_name: str) -> dict:
    """Install a module (a PyPI name, a bundled one's name or a GitHub URL) and restart."""
    check_package_name(package_name)
    if package_name.startswith("git+https://github.com/"):
        package_name = await _pinned_to_latest_release(package_name)
    logger.info(f"Installing package: {package_name}")
    await _change_packages("Installation", ["install", _local_target(package_name)])
    # Its card's scripts run on the screen if it came from MirrorDash's own GitHub; one of that name from
    # anywhere else loses the yes (ARCHITECTURE #41). Saved before the restart _change_packages scheduled.
    owner_repo = github_owner_repo(package_name) if package_name.startswith("git+https://github.com/") else None
    allow_scripts(package_key(_local_target(package_name)), bool(owner_repo) and owner_repo[0].lower() == OFFICIAL_OWNER)
    return {"status": "success", "message": f"Installed {package_name}. Restarting..."}


def _name_spellings(package_name: str) -> list[str]:
    """A module's name as - and as _ (its entry point may use either); a git URL's package is named after
    the repository (mirrordash-calendar)."""
    owner_repo = github_owner_repo(package_name) if package_name.startswith("git+https://github.com/") else None
    name = owner_repo[1] if owner_repo else package_name
    return [name, name.replace("-", "_"), name.replace("_", "-")]


async def _put_back(site, package_names) -> None:
    """Reinstall these modules in the changed site as they are in the running one (where they came from)."""
    from mirrordash_core.features.backup.service import installed_modules
    from mirrordash_core.features.updates.service import module_target
    running = {package_key(module["package_name"]): module for module in installed_modules()}
    for package_name in package_names:
        module = running.get(package_key(_local_target(package_name)))
        code, err = await site.pip("install", *module_target(module)) if module else (1, "it isn't installed now")
        if code != 0:
            raise HTTPException(status_code=500, detail=f"Putting {package_name} back failed, nothing was changed: {err}")


async def update_modules(*package_names: str) -> dict:
    """Upgrade one or more modules in one change of the module site, so MirrorDash restarts once (Settings →
    Updates → Update All, and the nightly update). Each must install and load on its own, by the module host
    in its sandbox, never as the core's user: one that doesn't is put back as it was and the others still go.
    When none does, nothing is changed. Returns which were upgraded ("updated")."""
    for package_name in package_names:
        check_package_name(package_name)
    logger.info(f"Upgrading packages: {', '.join(package_names)}")
    updated, failed = [], {}
    try:
        async with module_host.packages() as site:
            for package_name in package_names:
                code, err = await site.pip("install", "--upgrade", _local_target(package_name))
                if code == 0:
                    code, err = await site.check(_name_spellings(package_name))
                if code == 0:
                    updated.append(package_name)
                else:
                    logger.warning(f"Upgrade of {package_name} failed, it stays as it was: {err}")
                    failed[package_name] = err.strip()
            if not updated:
                raise HTTPException(status_code=500, detail=f"Verification failed. Rolled back successfully. Error: {'; '.join(failed.values())}")
            await _put_back(site, failed)
            if failed or len(updated) > 1:  # a dependency one of them moved can break another
                code, err = await site.check([name for package_name in updated for name in _name_spellings(package_name)])
                if code != 0:
                    raise HTTPException(status_code=500, detail=f"Verification failed. Rolled back successfully. Error: {err}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upgrade failed: {e}")
    run_in_background(run_restart())
    kept = f" {', '.join(failed)} didn't work and stayed as it was." if failed else ""
    return {"status": "success", "message": f"Upgraded {', '.join(updated)}.{kept} Restarting...", "updated": updated}


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

    # Where pip/uv installed it from (PEP 610): a git URL and commit for a module from GitHub
    direct_url_info = None
    if ep.dist:
        with contextlib.suppress(Exception):
            direct_url_info = json.loads(ep.dist.read_text("direct_url.json") or "null")

    if direct_url_info and "vcs_info" in direct_url_info:
        vcs_info, url = direct_url_info["vcs_info"], direct_url_info.get("url", "")
        owner_repo = github_owner_repo(url) if vcs_info.get("vcs") == "git" else None
        if not owner_repo:
            return result
        api_url = f"https://api.github.com/repos/{owner_repo[0]}/{owner_repo[1]}/releases/latest"
        release_data = await fetch_json_cached(api_url, headers={"Accept": "application/vnd.github.v3+json"}, max_age=max_age)
        result["checked_at"] = answered_at(api_url)
        tag_name = release_data.get("tag_name") if isinstance(release_data, dict) else None
        if not tag_name:
            return result
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
    names = sorted({ep.name for ep in module_entry_points()})
    checked = [update for update in await asyncio.gather(*(check_module_update(name, max_age) for name in names)) if update]
    answer_times = [update["checked_at"] for update in checked if update["checked_at"]]
    return [update for update in checked if update["latest_version"]], min(answer_times, default=None)


async def uninstall_module(package_name: str) -> dict:
    """Uninstall a module, delete its instances' settings, and restart."""
    check_package_name(package_name, allow_git=False)
    logger.info(f"Uninstalling package: {package_name}")
    await _change_packages("Uninstall", ["uninstall", "-y", package_name])
    # Its instances go too, once it is gone (a failed uninstall keeps them); a reinstall starts from defaults.
    # _change_packages only schedules the restart, so this is saved before it.
    config = load_config()
    modules_config = config.get("modules", {})
    instances = [instance_id for instance_id, instance_config in modules_config.items()
                 if isinstance(instance_config, dict)
                 and instance_config.get("module", instance_id).replace("-", "_") == package_name.replace("-", "_")]
    for instance_id in instances:
        logger.info(f"Removing configuration for module instance '{instance_id}' on uninstall")
        del modules_config[instance_id]
    if instances:
        save_config(config)
    allow_scripts(package_key(package_name), False)
    return {"status": "success", "message": f"Uninstalled {package_name}. Restarting..."}


async def list_modules() -> dict:
    """List all discovered entry-point modules and their config status (for the Modules panels)."""
    eps = module_entry_points()
    config = load_config()
    modules_config = config.get("modules", {})
    result = {}
    for ep in eps:
        name = ep.name

        schema = get_module_schema(ep)

        # The panels only read title, icon and description; with no description they fall back to the package summary
        if not schema:
            schema = {"title": name.replace("mirrordash-", "").replace("mirrordash_", "").title()}

        instances = []
        for instance_id, instance_config in modules_config.items():
            if not isinstance(instance_config, dict):
                continue
            instance_module = instance_config.get("module", instance_id)
            if instance_module.replace("-", "_") == name.replace("-", "_"):
                instances.append({
                    "id": instance_id,
                    "enabled": instance_config.get("enabled", True),
                    "position": instance_config.get("position", "middle_center"),
                    "title": instance_title(instance_id, instance_config)
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
_LAST_LAZY_SCAN = float("-inf")  # time.monotonic() of the last scan list_community_modules started


def _module_title(package_name: str) -> str:
    """"mirrordash-weather-radar" -> "Weather Radar"."""
    return package_name.replace("mirrordash-", "").replace("mirrordash_", "").replace("-", " ").title()


def _fetch_pypi_simple_index() -> str:
    """PyPI's list of every package (one link each), or "" when it doesn't answer.
    ponytail: the whole index, megabytes, since PyPI has no search API; scanned at most every 10 minutes.
    HTML scanned by a regex, not the JSON form (PEP 691): json.loads of its 930,000 projects peaked at 325 MB
    against 105 MB here (measured 2026-11-02), too much beside the screen on a Pi 3."""
    request = urllib.request.Request("https://pypi.org/simple/",
                                     headers={"User-Agent": "MirrorDash/1.0", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read()
            if response.info().get("Content-Encoding") == "gzip":
                content = gzip.decompress(content)
            return content.decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to fetch PyPI simple index: {e}")
        return ""


def _fetch_pypi_package_info(name: str) -> dict:
    """A package's PyPI JSON, or {} when it doesn't answer (one failing must not stop the scan)."""
    request = urllib.request.Request(f"https://pypi.org/pypi/{name}/json", headers={"User-Agent": "MirrorDash/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def pypi_module(name: str, package_info: dict) -> dict:
    """A discoverable module from its PyPI JSON."""
    info = package_info.get("info") or {}
    repo = next(filter(None, map(github_repo, (info.get("project_urls") or {}).values())), None)
    return {
        "name": name,
        "install_name": name,
        "title": _module_title(name),
        "description": info.get("summary") or "No description available.",
        "source": "pypi",
        "official": False,
        "author": info.get("author") or None,
        "license": info.get("license_expression") or None,  # `license` is often the whole licence text
        "updated": ((package_info.get("urls") or [{}])[0].get("upload_time") or "")[:10] or None,
        "repo": repo,
        "screenshot": github_screenshot(repo),
    }


async def _scan_pypi() -> list[dict]:
    """Every mirrordash-* package on PyPI that isn't MirrorDash itself."""
    index = await asyncio.to_thread(_fetch_pypi_simple_index)
    names = sorted(set(re.findall(r'<a href=\"/simple/(mirrordash-[^\"]+)/\">', index)) - NOT_MODULES)
    package_infos = await asyncio.gather(*(asyncio.to_thread(_fetch_pypi_package_info, name) for name in names))
    return [pypi_module(name, info) for name, info in zip(names, package_infos, strict=True)]


def _search_github_repositories() -> dict:
    """GitHub's search for repositories named mirrordash-*. One request: unauthenticated, GitHub allows 60
    an hour per IP address, shared by everything on the home network, so the latest release is looked up
    only when a module is installed (install_module), not here for every repository."""
    request = urllib.request.Request(
        "https://api.github.com/search/repositories?q=mirrordash-+in:name&per_page=100",
        headers={"User-Agent": "MirrorDash/1.0", "Accept": "application/vnd.github.v3+json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _add_github_modules(modules: list[dict], search_result: dict) -> list[dict]:
    """The PyPI modules with GitHub's added. A name on both is listed once: PyPI's, unless the GitHub one
    is official, since a PyPI package can't take an official module's name."""
    names = {module["name"] for module in modules}
    for item in search_result.get("items", []):
        repo_name = item.get("name", "")
        if not repo_name.startswith("mirrordash-") or repo_name in NOT_MODULES:
            continue
        module = github_module(item)
        if repo_name in names:
            if not module["official"]:
                continue
            modules = [listed for listed in modules if listed["name"] != repo_name]
        modules.append(module)
        names.add(repo_name)
    return modules


async def scan_community_modules_now():
    """Scan PyPI simple index and GitHub for mirrordash-* packages immediately."""
    global DISCOVERED_COMMUNITY_MODULES, LAST_SCAN_TIMESTAMP
    logger.info("Scanning PyPI and GitHub for mirrordash-* community modules...")
    scanned_modules = await _scan_pypi()
    try:
        search_result = await asyncio.to_thread(_search_github_repositories)
    except Exception as e:
        # Keep the list from the last scan (or, with none yet, what PyPI gave); the admin page says
        # the refresh failed
        logger.warning(f"Could not search GitHub for modules: {e}")
        if not DISCOVERED_COMMUNITY_MODULES:
            DISCOVERED_COMMUNITY_MODULES = scanned_modules
        raise
    scanned_modules = _add_github_modules(scanned_modules, search_result)
    if scanned_modules:
        DISCOVERED_COMMUNITY_MODULES = scanned_modules
        LAST_SCAN_TIMESTAMP = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(f"Scan completed. Discovered community modules: {[module['name'] for module in DISCOVERED_COMMUNITY_MODULES]}")


@router.get("/community-modules", dependencies=[Depends(require_api_key)])
async def list_community_modules() -> list[dict]:
    """Return a list of popular discoverable community modules on PyPI."""
    global _LAST_LAZY_SCAN
    # Not again on every visit after a failed one: the scan downloads PyPI's whole index
    if LAST_SCAN_TIMESTAMP is None and time.monotonic() - _LAST_LAZY_SCAN > 600:
        _LAST_LAZY_SCAN = time.monotonic()
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
