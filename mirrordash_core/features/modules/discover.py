# SPDX-License-Identifier: AGPL-3.0-only
"""The modules an owner can install: every mirrordash-* package on PyPI and repository on GitHub, scanned
when the Modules tab asks, and the two routes of that list."""

import asyncio
import gzip
import json
import logging
import re
import time
import urllib.request
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from mirrordash_core.admin import require_api_key
from mirrordash_core.features.modules.service import (
    OFFICIAL_OWNER,
    github_repo,
    github_screenshot,
    module_title,
)

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")

DISCOVERED_COMMUNITY_MODULES = []  # filled by scan_community_modules_now
LAST_SCAN_TIMESTAMP = None
_LAST_LAZY_SCAN = float("-inf")  # time.monotonic() of the last scan list_community_modules started
NOT_MODULES = {"mirrordash", "mirrordash-core", "mirrordash-sdk", "mirrordash-wpe"}  # mirrordash-* repos and packages that are not modules; the community scan skips them


def github_module(item: dict) -> dict:
    """A discoverable module from one GitHub search result; everything comes from that one response."""
    repo = github_repo(item["html_url"])
    author = (item.get("owner") or {}).get("login")
    return {
        "name": item["name"],
        "install_name": f"git+{item['html_url']}.git",
        "title": module_title(item["name"]),
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
        "title": module_title(name),
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
