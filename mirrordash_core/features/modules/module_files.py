# SPDX-License-Identifier: AGPL-3.0-only
"""Where the installed modules are, and their files read without running them (ARCHITECTURE #41).

The core never imports a module: it finds a module's entry point (find_entry_point), its folder
(package_dir) and reads its files (module_file: config_schema.json, icon.svg, translations), all without
running any of its code. The module host uses the same functions to find what to start.
"""

import contextlib
import importlib.machinery
import importlib.metadata
import json
import logging
import os
import re
import stat
import sys
from pathlib import Path

from mirrordash_core.config import get_module_dir

logger = logging.getLogger("mirrordash.core.modules")

# A module or instance name: it ends up in HTML ids, JS strings, URLs and folder names
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MODULE_FILE_LIMIT = 1024 * 1024  # a module file the core reads (schema, translations, icon.svg)


def module_site() -> Path:
    """Where modules are installed: the module host's own folder, a link it switches between site_a and
    site_b (host.py), each a link per module to the folder it was installed in alone. Never on the core's
    sys.path: the core reads its files only, so a package's .pth or one that shadows the core's own runs
    nowhere but in the module host, and there only in that module's process (ARCHITECTURE #41)."""
    return get_module_dir() / "site"


def module_folders() -> list[str]:
    """Each installed module's own folder (site/<name>), in name order."""
    with contextlib.suppress(OSError):
        return [str(module_site() / e.name) for e in sorted(os.scandir(module_site()), key=lambda e: e.name)
                if e.is_symlink() and SAFE_NAME.match(e.name)]
    return []


def module_search_path() -> list[str]:
    """The modules' folders, then this process's sys.path (a module installed into a developer's venv)."""
    return [*module_folders(), *sys.path]


def module_folder(ep) -> str | None:
    """The folder ep's package was installed in (one of module_folders, or this process's site-packages)."""
    return str(ep.dist.locate_file("")) if getattr(ep, "dist", None) else None


def site_name(ep) -> str | None:
    """A module's folder's name in the module site (site/<name>), or None for one in the core's venv."""
    folder = module_folder(ep)
    return Path(folder).name if folder and Path(folder).parent == module_site() else None


def scripts_allowed(ep, config: dict) -> bool:
    """Whether a module's card runs its scripts on the screen, where they could read the other cards
    (ARCHITECTURE #41): a module in the core's venv (the image's clock, a developer's), or one in the site
    whose name is in config's "module_scripts" (installed from MirrorDash's own GitHub, or the owner's yes)."""
    return bool(ep) and ((name := site_name(ep)) is None or name in config.get("module_scripts", []))


def folder_entry_points(folder: str) -> set[str]:
    """The module names a folder declares, '-' as '_' (find_entry_point's equality)."""
    return {ep.name.replace("-", "_") for dist in importlib.metadata.distributions(path=[folder])
            for ep in dist.entry_points.select(group="mirrordash.modules")}


def module_entry_points() -> list:
    """The installed modules' entry points, each name once; one whose name isn't SAFE_NAME is left out (a
    package chooses its own entry point names)."""
    eps, seen = [], set()
    for dist in importlib.metadata.distributions(path=module_search_path()):
        for ep in dist.entry_points.select(group="mirrordash.modules"):
            if SAFE_NAME.match(ep.name) and ep.name not in seen:
                seen.add(ep.name)
                eps.append(ep)
    return eps


def find_entry_point(name: str, eps=None):
    """Find a module's entry point by name, treating '-' and '_' as equal (config ids use either)."""
    norm_name = name.replace('-', '_')
    eps = module_entry_points() if eps is None else eps
    return next((ep for ep in eps if ep.name.replace('-', '_') == norm_name), None)


def package_dir(ep) -> Path | None:
    """The folder of a module's plugin file, found without importing anything of it: PathFinder of a
    top-level name runs no package code (a submodule's would run its parent's __init__), nor a .pth."""
    try:
        top, *rest = ep.module.split(".")
        # Its own folder first: another module's package of the same name lies in a folder of its own
        folder = module_folder(ep)
        spec = importlib.machinery.PathFinder.find_spec(top, [folder, *sys.path] if folder else module_search_path())
    except (AttributeError, TypeError, ImportError, ValueError):  # not a real entry point, or a bad name
        return None
    if not spec:
        return None
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations)), *rest[:-1])
    return Path(spec.origin).parent if spec.origin else None


def module_file(pkg_dir: Path | None, *parts: str) -> bytes | None:
    """A file in a module's package, read for the core, or None. One that really lies outside the folders
    modules are found in isn't read: the module host writes the site, and a link it put there to the core's
    data would have the core show that (a translated title on the screen). Where it lies is asked of the
    open file (/proc/self/fd), and it is read from that descriptor, so a link the host swaps in between the
    check and the read isn't followed either."""
    if pkg_dir is None:
        return None
    path = Path(pkg_dir, *parts)
    roots = [Path(p).resolve() for p in module_search_path() if p]
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)  # NONBLOCK: a FIFO there can't hang the core
    except OSError:
        return None
    with open(fd, "rb") as file:
        try:
            real = Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:  # no /proc (a developer's Mac): the path as it resolves now
            real = path.resolve()
        info = os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) and any(real.is_relative_to(root) for root in roots)):
            return None
        if info.st_size > MODULE_FILE_LIMIT:  # read whole into the core: a huge one would take its memory
            logger.warning(f"{real} is over {MODULE_FILE_LIMIT // 1024} kB, not read")
            return None
        return file.read(MODULE_FILE_LIMIT)


def load_translations(pkg_dir: Path | None, lang: str) -> dict:
    """The module's translations/en.json, with the configured language's file over it."""
    translations = {}
    for code in dict.fromkeys(["en", lang or "en"]):
        try:
            if (raw := module_file(pkg_dir, "translations", f"{code}.json")) is not None:
                translations.update(json.loads(raw.decode("utf-8")))
        except Exception as e:
            logger.debug(f"Could not load translations from {pkg_dir} in language '{code}': {e}")
    return translations


