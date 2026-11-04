# SPDX-License-Identifier: AGPL-3.0-only
"""The settings drawer's preview (ARCHITECTURE #43): the card the unsaved form would show.

A module may ship sample answers in its preview/fixtures.json. render_preview starts a throwaway instance
with the form's settings, lets every self.fetch answer from those samples (with their dates moved to
today), and renders the card once. Nothing it does reaches the screen, Home Assistant, the GPIO pins, the
event bus or the network through fetch.
"""

import asyncio
import inspect
import json
import re
import site
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from mirrordash_core.features.modules.loader import (
    _inject_module_helpers,
    _settings_for_instance,
)
from mirrordash_core.features.modules.module_files import (
    find_entry_point,
    load_translations,
    module_file,
    module_folder,
    package_dir,
)

PREVIEW_TIMEOUT = 10  # seconds a preview's render() may take
SAFE_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$")  # a file in a module's preview folder: no path

ISO_DATE = re.compile(r"(?<![\d.])(\d{4})-(\d{2})-(\d{2})(?![\d.])|(?<!\d)(\d{4})(\d{2})(\d{2})(?=T\d{4})")


def _shift_dates(raw: bytes, days: int) -> bytes:
    """raw with every date in it days later: 2026-11-02 (JSON, RSS) and 20261102T0800 (iCalendar).
    ponytail: whole days only, and a date-like number in other text moves too."""
    if not days:
        return raw
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw

    def later(match: re.Match) -> str:
        year, month, day = (int(part) for part in (match.group(1, 2, 3) if match.group(1) else match.group(4, 5, 6)))
        try:
            moved = date(year, month, day) + timedelta(days=days)
        except ValueError:  # not a date after all (2026-13-45)
            return match.group(0)
        return moved.isoformat() if match.group(1) else moved.strftime("%Y%m%d")
    return ISO_DATE.sub(later, text).encode("utf-8")


def preview_fixtures(pkg_dir: Path | None) -> dict[str, bytes] | None:
    """A module's sample answers for the admin's preview (ARCHITECTURE #43), from its preview/fixtures.json:
    {"captured": "2026-11-02", "answers": {"*/api/states": "states.json"}}: fnmatch patterns on host + path,
    each to a file beside it. The dates in them move as many days as have passed since captured, so a
    forecast or a calendar stays current. None when the module has no fixtures.json (the admin shows the
    saved card instead); {} for one that fetches nothing."""
    try:
        raw = module_file(pkg_dir, "preview", "fixtures.json")
        fixtures = json.loads(raw.decode("utf-8")) if raw is not None else None
        days = (date.today() - date.fromisoformat(fixtures["captured"])).days if "captured" in fixtures else 0
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not isinstance(fixtures, dict) or not isinstance(patterns := fixtures.get("answers"), dict):
        return None
    answers = {}
    for pattern, file_name in patterns.items():
        if (isinstance(pattern, str) and isinstance(file_name, str) and SAFE_FILE.match(file_name)
                and (answer := module_file(pkg_dir, "preview", file_name)) is not None):
            answers[pattern] = _shift_dates(answer, days)
    return answers



async def _not_in_a_preview(*args, **kwargs):
    raise RuntimeError("not in a preview")  # a RuntimeError, as self.gpio gives for a pin it can't have


async def _shown_nowhere(*args, **kwargs) -> None:
    """A preview's self.expose: the value goes nowhere, and the card is drawn as usual."""


async def render_preview(instance_id: str, module_cfg: dict, globals_config: dict, link) -> str | None:
    """The card module_cfg (the admin's unsaved form) would show, rendered once by a throwaway instance from
    the module's sample answers (preview_fixtures, ARCHITECTURE #43); None when the module has none or no
    render(). Nothing it does reaches the screen, Home Assistant, the GPIO pins, the event bus or the network
    through fetch. ponytail: a module with its own HTTP client still reaches out from a preview."""
    ep = find_entry_point(module_cfg.get("module") or instance_id)
    if ep is None or (fixtures := preview_fixtures(pkg_dir := package_dir(ep))) is None:
        return None
    if (folder := module_folder(ep)) and folder not in sys.path:
        site.addsitedir(folder)
    plugin_class = ep.load()
    translations = load_translations(pkg_dir, globals_config.get("language", "en"))
    with tempfile.TemporaryDirectory(prefix="preview-") as scratch:  # on a mirror under its cache folder (TMPDIR)
        settings = _settings_for_instance(instance_id, module_cfg, globals_config, translations, scratch)
        plugin = plugin_class(settings)
        plugin.gpio = plugin.video_window = plugin.video_is_playing = _not_in_a_preview
        plugin.expose = _shown_nowhere
        _inject_module_helpers(plugin, plugin_class.__module__.split(".")[0], translations, instance_id, settings, link, fixtures)
        render = getattr(plugin, "render", None)
        if not inspect.iscoroutinefunction(render):
            return None
        return await asyncio.wait_for(render(), PREVIEW_TIMEOUT)


