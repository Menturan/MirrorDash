# SPDX-License-Identifier: AGPL-3.0-only
"""The module site's folders (ARCHITECTURE #41): where the installed modules live, in the host's folder.

Each module is installed in a folder of its own, pkgs/<package>_<n>, which belongs to that module's user.
The module site is a folder of links, one per module, each to its folder in pkgs. There are two of them,
site_a and site_b, and the link `site` points at the active one: a change (install, update, remove) is
made in the other one and switched to at once, like the core's A/B venvs, so a change that fails never
touches the modules that run. Only the module host's control process writes here (host.py Control).
"""

import contextlib
import importlib.metadata
import os
import shutil
from pathlib import Path

from mirrordash_core.config import get_module_dir
from mirrordash_core.features.modules.loader import (
    SAFE_NAME,
    folder_entry_points,
    module_site,
)
from mirrordash_core.features.modules.package_names import package_key


def site_paths() -> tuple[Path, Path, Path]:
    """(link, active, next): the module site is a link to site_a or site_b, as the core's venv is."""
    link = module_site()
    site_a, site_b = link.with_name("site_a"), link.with_name("site_b")
    active = site_b if link.is_symlink() and os.readlink(link) == "site_b" else site_a
    return link, active, site_a if active == site_b else site_b


def site_links(site_dir: Path) -> dict[str, str]:
    """A site's modules: {name: the name of its folder in pkgs}."""
    with contextlib.suppress(OSError):
        return {e.name: Path(os.readlink(e.path)).name for e in os.scandir(site_dir)
                if e.is_symlink() and SAFE_NAME.match(e.name)}
    return {}


def link_module(site_dir: Path, name: str, folder: str) -> None:
    """Point site_dir/name at pkgs/folder, at once."""
    tmp = site_dir / f".{name}.tmp"
    tmp.unlink(missing_ok=True)
    os.symlink(f"../pkgs/{folder}", tmp)
    os.replace(tmp, site_dir / name)


def folder_holds_package(folder: Path, package: str) -> bool:
    """Whether a module's folder has the package of that package_key installed."""
    return any(package_key(d.name) == package for d in importlib.metadata.distributions(path=[str(folder)]))


def next_module_folder_name(package: str) -> str:
    """A new folder in pkgs for an install of package: <package>_<n>, n one past the highest there, so an
    install never writes into the folder the running version uses."""
    pkgs = get_module_dir() / "pkgs"
    used_numbers = []
    if pkgs.is_dir():
        for entry in os.scandir(pkgs):
            prefix, _, number = entry.name.rpartition("_")
            if prefix == package and number.isdigit():
                used_numbers.append(int(number))
    return f"{package}_{max(used_numbers, default=0) + 1}"


def module_name_clash(package: str, new_folder: Path, links: dict[str, str]) -> tuple[str, set[str]] | None:
    """(the other package, the module names both have) when the modules in new_folder take a name another
    package has: one in the site (links), or in the core's venv (the image's clock), which the site shadows
    (loader.module_search_path), so only a newer version of that package may take them. None if no clash."""
    claimed_names = folder_entry_points(str(new_folder))
    pkgs = get_module_dir() / "pkgs"
    others = [(other, folder_entry_points(str(pkgs / folder))) for other, folder in links.items()]
    for distribution in importlib.metadata.distributions():
        names = {ep.name.replace("-", "_") for ep in distribution.entry_points.select(group="mirrordash.modules")}
        others.append((package_key(str(distribution.name)), names))
    for other_package, names in others:
        if other_package != package and (shared_names := claimed_names & names):
            return other_package, shared_names
    return None


def empty_folder(folder: Path, keep_what_cannot_be_removed: bool = False) -> None:
    """Everything in folder, not the folder: a module's folder is its user's in pkgs, which it can't write
    (forkserver.give_folder_to_user makes it). keep_what_cannot_be_removed: for a site's folder of links,
    where what an older shared site left and can't be removed stays (module_folders reads the links only)."""
    folder.mkdir(parents=True, exist_ok=True)
    for entry in os.scandir(folder):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        except OSError:
            if not keep_what_cannot_be_removed:
                raise
