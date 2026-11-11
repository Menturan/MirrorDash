# SPDX-License-Identifier: AGPL-3.0-only
""""MirrorDash updated to 0.16.0": told when the new version runs, not when it was installed, since an
update that crashes is rolled back (and that has its own saved log, watch.py). So at every start the
versions running now are held against the last start's: MirrorDash's, the OS's and every module's, from
the nightly update or the admin page alike. Only a higher version is news; a module installed for the
first time isn't (the owner did that themselves)."""

from mirrordash_core import notifications
from mirrordash_core.config import get_core_version, version_key


def running_versions() -> dict[str, str]:
    """What runs now: {"MirrorDash": "0.16.0", "System": "20261018-2237", "module:Weather": "1.4.2", ...}."""
    from mirrordash_core.features.modules.installed import installed_modules
    from mirrordash_core.features.modules.service import module_title
    from mirrordash_core.host import installed_os_build
    versions = {"MirrorDash": get_core_version()}
    if build := installed_os_build():
        versions["System"] = build
    for module in installed_modules():
        versions[f"module:{module_title(module['package_name'])}"] = module["version"]
    return versions


def newer(name: str, version: str, before: str) -> bool:
    if name == "System":
        return version > before  # a build stamp, YYYYMMDD-HHMM
    return version_key(version) > version_key(before)


def note_new_versions(now: dict[str, str] | None = None) -> None:
    """A notification for each version that went up since the last start. The first start of this
    version of MirrorDash only remembers them."""
    from mirrordash_core.features.updates.service import format_build
    from mirrordash_core.host import installed_os_version
    now = running_versions() if now is None else now
    before = notifications.last_versions()
    if before is not None:
        for name, version in now.items():
            if name in before and newer(name, version, before[name]):
                # ponytail: an OS on trial counts as updated already; when its trial fails, the os-trial
                # saved log says so right after
                shown = (installed_os_version() or format_build(version)) if name == "System" else version
                notifications.add("info", f"{name.removeprefix('module:')} updated to {shown}")
    if now != before:
        notifications.set_last_versions(now)
