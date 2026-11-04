# SPDX-License-Identifier: AGPL-3.0-only
"""A throwaway instance of a module, made from the admin's unsaved settings, for the settings drawer: its
preview (preview.py) and the choices of a list or search setting (choices.py). Never shown on the screen,
and it reaches no GPIO pin, video window, Home Assistant or event bus; its files go to a scratch folder
removed afterwards."""

import asyncio
import contextlib
import site
import sys
import tempfile

from mirrordash_core.features.modules.loader import (
    _inject_module_helpers,
    _settings_for_instance,
)
from mirrordash_core.features.modules.module_files import (
    load_translations,
    module_folder,
)


async def _not_here(*args, **kwargs):
    raise RuntimeError("not in a preview")  # a RuntimeError, as self.gpio gives for a pin it can't have


async def _shown_nowhere(*args, **kwargs) -> None:
    """A throwaway instance's self.expose: the value goes nowhere."""


class AlwaysAwake:
    """The link a throwaway instance's fetch sees: the screen counts as on, so a call made from the admin
    page while the screen is off doesn't wait for it."""

    def __init__(self, link):
        self._link = link
        self.awake = asyncio.Event()
        self.awake.set()

    def __getattr__(self, name):
        return getattr(self._link, name)


@contextlib.contextmanager
def throwaway_instance(ep, pkg_dir, instance_id: str, module_cfg: dict, globals_config: dict, link,
                       fixtures: dict[str, bytes] | None = None):
    """The plugin of the module ep (its package in pkg_dir), made from module_cfg. With fixtures (a preview)
    its fetch answers from them, else from the network."""
    if (folder := module_folder(ep)) and folder not in sys.path:
        site.addsitedir(folder)
    plugin_class = ep.load()
    translations = load_translations(pkg_dir, globals_config.get("language", "en"))
    with tempfile.TemporaryDirectory(prefix="settings-") as scratch:  # on a mirror under its cache folder (TMPDIR)
        settings = _settings_for_instance(instance_id, module_cfg, globals_config, translations, scratch)
        plugin = plugin_class(settings)
        plugin.gpio = plugin.video_window = plugin.video_is_playing = _not_here
        plugin.expose = _shown_nowhere
        _inject_module_helpers(plugin, plugin_class.__module__.split(".")[0], translations, instance_id, settings,
                               AlwaysAwake(link), fixtures)
        yield plugin
