# SPDX-License-Identifier: AGPL-3.0-only
"""The modules that are installed, and where each one reinstalls from (a backup, an update, a put-back)."""

import json
from pathlib import Path

from mirrordash_core.config import ROOT_DIR, get_module_dir
from mirrordash_core.features.modules.module_files import module_entry_points


def find_local_module_dir(package_name: str) -> Path | None:
    """The folder of a module installed from a local folder (not from GitHub or PyPI), or None."""
    def same_name(name: str) -> bool:
        return name.lower().replace("_", "-") == package_name.lower().replace("_", "-")
    for base_dir in [ROOT_DIR / "modules", get_module_dir() / "local"]:
        if base_dir.exists():
            for child in base_dir.iterdir():
                if child.is_dir() and same_name(child.name):
                    return child
    return None


def installed_modules() -> list[dict]:
    """Every installed module and where it reinstalls from: its local folder, its GitHub commit
    (PEP 610 records it) or its PyPI version. The backup manifest and a new Python's module site use it."""
    modules = []
    for ep in module_entry_points():
        package_name = ep.dist.name if ep.dist else ep.name
        entry = {"name": ep.name, "package_name": package_name,
                 "version": ep.dist.version if ep.dist else "0.0.0", "type": "pypi"}
        local_dir = find_local_module_dir(package_name)
        if local_dir:
            entry.update(type="local", folder_name=local_dir.name)
        else:
            direct_url = json.loads((ep.dist.read_text("direct_url.json") if ep.dist else None) or "{}")
            vcs = direct_url.get("vcs_info")
            if vcs and direct_url.get("url"):
                entry["source"] = f"git+{direct_url['url']}@{vcs['commit_id']}"
        modules.append(entry)
    return modules


def module_target(module: dict) -> list[str]:
    """What `uv pip install` takes to reinstall a module from where it came (installed_modules)."""
    local_dir = find_local_module_dir(module["package_name"]) if module["type"] == "local" else None
    return ["-e", str(local_dir)] if local_dir else [module.get("source") or f"{module['package_name']}=={module['version']}"]
