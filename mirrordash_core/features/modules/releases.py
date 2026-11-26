# SPDX-License-Identifier: AGPL-3.0-only
"""Which GitHub release of a module an update check offers: the newest stable one, or with the module's
"Test versions" switch on the highest version of all, pre-releases included (ARCHITECTURE #41)."""

from mirrordash_core.config import version_key
from mirrordash_core.fetch import answered_at, fetch_json_cached

API = "https://api.github.com/repos"
HEADERS = {"Accept": "application/vnd.github.v3+json"}


def highest_release(releases: object) -> dict | None:
    """The release with the highest version in GitHub's list (drafts skipped; a tag that isn't a version
    sorts lowest), else None."""
    tagged = [release for release in releases if isinstance(release, dict) and release.get("tag_name")
              and not release.get("draft")] if isinstance(releases, list) else []
    return max(tagged, key=lambda release: version_key(release["tag_name"].lstrip("v")), default=None)


async def candidate_release(owner: str, repo: str, test_versions: bool, max_age: float) -> tuple[dict | None, float | None]:
    """(the release to offer or None, when GitHub's answer was fetched). One request either way, cached like
    every update check: /releases/latest is the newest stable release (GitHub never returns a pre-release
    there); with test_versions the list of the 20 newest releases is asked instead."""
    url = f"{API}/{owner}/{repo}/releases" + ("?per_page=20" if test_versions else "/latest")
    data = await fetch_json_cached(url, headers=HEADERS, max_age=max_age)
    if test_versions:
        release = highest_release(data)
    else:
        release = data if isinstance(data, dict) and data.get("tag_name") else None
    return release, answered_at(url)
