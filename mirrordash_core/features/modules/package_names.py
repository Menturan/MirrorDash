# SPDX-License-Identifier: AGPL-3.0-only
"""The names a module's package goes by in the module host, made from what uv is told to install:
package_key, the name of its folder and its link in the module site, and package_user, the user it is
installed as (forkserver.PACKAGE_UIDS)."""

import hashlib
import re
from pathlib import Path


def package_key(target: str) -> str | None:
    """The name a module's folder and user go by, from what uv installs: a GitHub repository's name, a
    local folder's or a PyPI requirement's, lowercase with '-' between words. None for anything else."""
    # here, not at the top: the forkserver imports this file, and every instance it forks would carry packaging
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    if target.startswith("git+"):
        name = target.split("@")[0].rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    elif "/" in target:
        name = Path(target).name
    else:
        try:
            name = Requirement(target).name
        except InvalidRequirement:
            return None
    key = canonicalize_name(name)
    return key if re.fullmatch(r"[a-z0-9-]{1,56}", key) else None


def package_user(target: str) -> str | None:
    """The key of the user a module installs as (package_uids.json): its name and where it comes from (a
    GitHub repository, a folder, PyPI), so a module of an installed one's name from elsewhere never builds
    as the user whose files the installed module's code is. None for what package_key refuses."""
    if not (name := package_key(target)):
        return None
    if target.startswith("git+"):
        source = target.split("@")[0].rstrip("/").removesuffix(".git").lower()  # the repository, any version
    elif "/" in target:
        source = target  # a local folder
    else:
        source = ""  # PyPI
    short_source_hash = hashlib.sha256(source.encode()).hexdigest()[:7]
    return f"{name}-{short_source_hash}"
