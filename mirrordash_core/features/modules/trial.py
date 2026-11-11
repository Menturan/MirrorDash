# SPDX-License-Identifier: AGPL-3.0-only
"""A module update is on trial for a while, as an app update (venv.py) and a system update (mark-good) are:
when it fails in that time, the version before is put back instead of the module being paused.

Failing is what already had a rule: its process dying CRASH_LIMIT times in a row (client.py), or the screen
crashing twice soon after (the crash guard, modules/guard.py). The version before is still there: an update
goes into a new folder, and the site that isn't active keeps its link to the old one until the next change
(site_folders.py), so the host only points one link back (host.py Control._site "back")."""

import logging
import time

from mirrordash_core.config import load_config, save_config

logger = logging.getLogger("mirrordash.core.modules")

KEY = "module_trial"
# From the update, the restart that follows included: the crash guard's own window (two crashes in it)
# and a few minutes to get there
WINDOW = 15 * 60


def start(site_names: list[str], now: float | None = None) -> None:
    """These modules (their names in the module site) were updated now. Saved before the restart that follows."""
    if not site_names:
        return
    config = load_config()
    config[KEY] = {"until": (now or time.time()) + WINDOW, "modules": sorted(site_names)}
    save_config(config)


def on_trial(config: dict, now: float | None = None) -> set[str]:
    """The modules (site names) whose update is still on trial."""
    trial = config.get(KEY)
    if not isinstance(trial, dict) or not isinstance(trial.get("until"), int | float):
        return set()
    # a clock that went back past the update (not set yet) ends the trial too: never one that lasts for days
    if not trial["until"] - WINDOW <= (now or time.time()) <= trial["until"]:
        return set()
    return {name for name in trial.get("modules") or [] if isinstance(name, str)}


def site_name_of(instance_id: str, config: dict) -> str | None:
    """The module site's name of an instance's module, or None (not installed, or one in the core's venv)."""
    from mirrordash_core.features.modules.module_files import (
        find_entry_point,
        site_name,
    )
    instance = config.get("modules", {}).get(instance_id)
    ep = find_entry_point(instance.get("module", instance_id)) if isinstance(instance, dict) else None
    return site_name(ep) if ep else None


async def put_back(only: str | None = None) -> list[str]:
    """Put the version before back for every module on trial, or for the one named (a site name). Returns
    the modules put back; the caller restarts the app, as after any change of the modules. The version
    that failed isn't tried by the nightly update again (the buttons still install it)."""
    from mirrordash_core import notifications
    from mirrordash_core.features.backup.service import installed_modules
    from mirrordash_core.features.modules.client import module_host
    from mirrordash_core.features.modules.package_names import package_key
    from mirrordash_core.features.modules.service import module_title
    from mirrordash_core.features.updates.service import UPDATE_TRIES
    names = on_trial(load_config())
    if only is not None:
        names &= {only}
    failed = {package_key(module["package_name"]): module for module in installed_modules()}  # as they run now
    back = []
    for name in sorted(names):
        code, err = await module_host._site("back", 30, name=name)
        if code:
            logger.warning(f"{name} couldn't be put back to the version before: {err}")
            continue
        back.append(name)
        module = failed.get(name) or {"package_name": name, "version": ""}
        logger.warning(f"{name} {module['version']} failed soon after its update: the version before is back")
        notifications.add("error", f"{module_title(module['package_name'])} {module['version']} didn't work and was put "
                          "back to the version before. The nightly update waits for a newer version; Settings → "
                          "Updates can still install this one.")
    if back:
        config = load_config()
        system = config.setdefault("system", {})
        tries = system.get("auto_update_tries")
        system["auto_update_tries"] = {**(tries if isinstance(tries, dict) else {}), **{
            failed[name]["package_name"]: {"version": failed[name]["version"], "times": UPDATE_TRIES}
            for name in back if name in failed}}
        left = on_trial(config) - set(back)
        config[KEY] = {**config[KEY], "modules": sorted(left)} if left else {}
        save_config(config)
    return back
