# SPDX-License-Identifier: AGPL-3.0-only
"""The oldest MirrorDash a module release needs (ARCHITECTURE #65): "requires_mirrordash": "0.12" at the top
of its config_schema.json, a plain minimum version. Read from the files of the release just installed in the
next module site, before it is switched in, so a release that needs a newer MirrorDash never runs."""

import importlib.metadata
import logging
from pathlib import Path

from mirrordash_core.config import get_core_version
from mirrordash_core.features.modules.schema import get_module_schema

logger = logging.getLogger("mirrordash.core.modules")

KEY = "requires_mirrordash"


def requirement_problem(schema: dict | None, name: str, core_version: str | None = None) -> str | None:
    """Why a module with this schema can't be installed on this MirrorDash, or None: no field is fine, so is a
    minimum the running MirrorDash has reached (its release counts: 0.17.0rc1 meets "0.17"). A field that
    isn't a version is refused too: a typo must not let a module through that needs more."""
    from packaging.version import InvalidVersion, Version
    if not isinstance(schema, dict) or KEY not in schema:
        return None
    minimum = schema[KEY]
    try:
        needed = Version(minimum)
    except (InvalidVersion, TypeError):
        logger.warning(f"{name}: {KEY} is {minimum!r}, not a version like 0.12; not installed")
        return f'{name} has a "{KEY}" that is not a version like 0.12, so it can\'t be installed.'
    try:
        running = Version(Version(core_version or get_core_version()).base_version)
    except InvalidVersion:  # a checkout that isn't installed as a package: nothing to compare
        return None
    return f"Needs MirrorDash {minimum} or newer: update MirrorDash first" if running < needed else None


def folder_problem(folder: Path, core_version: str | None = None) -> tuple[str, str] | None:
    """(the package's version, requirement_problem) of the first module in a module's folder (pkgs/<folder>,
    installed in the next site) that can't be installed, or None."""
    for dist in importlib.metadata.distributions(path=[str(folder)]):
        for ep in dist.entry_points.select(group="mirrordash.modules"):
            if problem := requirement_problem(get_module_schema(ep, also_in=folder), ep.name, core_version):
                return dist.version, problem
    return None
