# SPDX-License-Identifier: AGPL-3.0-only
"""Runs the modules: ModuleLoader starts each instance, gives it its helpers and keeps its run_loop going.

It lives in the module host, in the instance's own process (instance.py), as its own user, never in the
core's (ARCHITECTURE #41). The helpers a module gets (_inject_module_helpers) are self.translate,
self.fetch and self.fetch_json (module_fetch.py), self.render_template, self.video_is_playing,
self.video_window, self.expose and self.gpio. Finding modules and reading their files is module_files.py;
the settings drawer's preview is preview.py."""

import asyncio
import contextlib
import functools
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import logging
import os
import site
import sys
import time

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)

from mirrordash_core.config import get_module_dir
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.modules.module_fetch import _make_fetch
from mirrordash_core.features.modules.module_files import (
    SAFE_NAME,
    find_entry_point,
    load_translations,
    module_entry_points,
    module_folder,
    package_dir,
)

logger = logging.getLogger("mirrordash.core.modules")


# Restart delay before retrying a crashed module (seconds)
MODULE_RESTART_DELAY = 5


def _inject_module_helpers(plugin_instance, package_name: str, translations: dict, module_name: str, config: dict, link,
                           fixtures: dict[str, bytes] | None = None) -> None:
    """Inject translation, data fetching and template rendering helpers into the plugin instance if missing.
    link is the way to the core (instance.py's HostLink): the screen's state, video playing and its window."""
    plugin_instance.translations = translations

    if not hasattr(plugin_instance, "translate"):
        def translate(key: str, default: str | None = None) -> str:
            val = plugin_instance.translations.get(key)
            if val is not None:
                return val
            return default if default is not None else key
        plugin_instance.translate = translate

    fetch, fetch_json = _make_fetch(config.get("cache_dir"), module_name, link.awake, getattr(plugin_instance, "keep_running", False),
                                    lambda: getattr(plugin_instance, "interval", None), fixtures)
    if not hasattr(plugin_instance, "fetch"):
        plugin_instance.fetch = fetch
    if not hasattr(plugin_instance, "fetch_json"):
        plugin_instance.fetch_json = fetch_json

    if not hasattr(plugin_instance, "changed"):
        plugin_instance.changed = asyncio.Event()  # set: the default run_loop shows render() again at once

    if not hasattr(plugin_instance, "video_is_playing"):
        # `async with self.video_is_playing():` around playback: meanwhile the other modules update once a
        # minute (ws.py, ARCHITECTURE #33). ponytail: async only; a sync run_loop can't hold it.
        @contextlib.asynccontextmanager
        async def video_is_playing():
            await link.video_playing(module_name, True)
            try:
                yield
            finally:
                await link.video_playing(module_name, False)
        plugin_instance.video_is_playing = video_is_playing

    if not hasattr(plugin_instance, "video_window"):
        # `async with self.video_window(fullscreen=False, hz=None) as window:` around the module's own
        # player, whose window has window.app_id: it lands over the card's data-video-window element
        plugin_instance.video_window = functools.partial(link.video_window, module_name)

    if not hasattr(plugin_instance, "expose"):
        # `await self.expose("now_playing", title)`: a value of what config_schema.json's "exposes" declares,
        # in Home Assistant if the owner shares it (MQTT, ARCHITECTURE #42). Commands come to on_control.
        plugin_instance.expose = lambda name, value: link.state(module_name, name, value)

    if not hasattr(plugin_instance, "gpio"):
        # `lines = await self.gpio([17], output=True)`, then lines.set(17, 1): free header pins only, given by
        # the core (features/hardware/gpio.py); options: output, initial, pull, edge, active_low, debounce_ms
        plugin_instance.gpio = functools.partial(link.gpio, module_name)

    if not hasattr(plugin_instance, "render_template"):
        try:
            if render_template := _make_render_template(plugin_instance, package_name, config):
                plugin_instance.render_template = render_template
                logger.debug(f"Auto-injected render_template helper for module '{module_name}'")
        except Exception as e:
            logger.warning(f"Could not auto-inject render_template helper for '{module_name}': {e}")


def _make_render_template(plugin_instance, package_name: str, config: dict):
    """`self.render_template("card.html", **context)` over the module's templates/ folder, with its
    translations and show_header filled in; None when the module has no templates."""
    loaders = []
    with contextlib.suppress(Exception):
        loaders.append(PackageLoader(package_name, "templates"))
    with contextlib.suppress(Exception):
        spec = importlib.util.find_spec(package_name)
        templates_dir = os.path.join(os.path.dirname(spec.origin), "templates") if spec and spec.origin else ""
        if os.path.isdir(templates_dir):
            loaders.append(FileSystemLoader(templates_dir))
    if not loaders:
        return None
    environment = Environment(loader=ChoiceLoader(loaders), autoescape=select_autoescape(["html", "xml"]))

    def render_template(template_name: str, **context) -> str:
        if "translations" not in context and hasattr(plugin_instance, "translations"):
            context["translations"] = plugin_instance.translations
        context.setdefault("show_header", config.get("show_header", True))
        try:
            return environment.get_template(template_name).render(**context)
        except Exception as render_err:
            logger.error(f"Failed to render template {template_name} in {package_name}: {render_err}", exc_info=True)
            raise

    return render_template


class _NoEvents:
    """The event bus of a preview's module: what it publishes or listens for goes nowhere."""
    def subscribe(self, *args, **kwargs) -> None:
        pass
    unsubscribe = publish = subscribe


def _settings_for_instance(instance_id: str, module_cfg: dict, globals_config: dict, translations: dict,
                           scratch: str | None = None) -> dict:
    """What a module's constructor gets: its saved settings, plus its own writable data_dir and cache_dir,
    the event bus, where its routes are served (api_path), the global settings and its translations.
    scratch (a preview): data_dir and cache_dir are that throwaway folder, the event bus goes nowhere, and
    preview is True, so a module that needs a login or a pairing shows its sample answers, not how to log in."""
    instance_settings = module_cfg.copy()
    for kind in ("data", "cache"):
        path = scratch or os.path.join(get_module_dir(), kind, instance_id)
        try:
            os.makedirs(path, exist_ok=True)
            instance_settings[f"{kind}_dir"] = path
        except Exception as e:
            logger.warning(f"Could not create writeable {kind} directory '{path}' for '{instance_id}': {e}")
    instance_settings["event_bus"] = _NoEvents() if scratch else event_bus
    instance_settings["preview"] = bool(scratch)
    instance_settings["api_path"] = f"/api/modules/{instance_id}"
    instance_settings["globals"] = globals_config
    instance_settings["translations"] = translations
    return instance_settings


async def _render_loop(instance_id: str, plugin_instance, broadcast_func) -> None:
    """The run_loop of a module with only `async def render()`: show it, wait its interval (600 s without
    one) or until self.changed is set, repeat. An error is logged and render() tried at the next turn."""
    while True:
        try:
            await broadcast_func(instance_id, await plugin_instance.render())
        except Exception as e:
            logger.error(f"{instance_id}: {e}", exc_info=True)
        try:
            await asyncio.wait_for(plugin_instance.changed.wait(), getattr(plugin_instance, "interval", 600))
        except TimeoutError:
            pass
        plugin_instance.changed.clear()


class ModuleLoader:
    """The running module instances, in the module host process. link: the way to the core (HostLink)."""

    def __init__(self, link):
        self.link = link
        self.tasks: dict[str, asyncio.Task] = {}
        self.instances: dict = {}
        self.last_shown: bool | None = None  # what on_shown said last; None: not yet since the start

    async def start_modules(self, modules_config: dict, globals_config: dict) -> None:
        """Start an instance for each enabled entry of modules_config (the core's config["modules"])."""
        self.last_shown = None
        eps = module_entry_points()
        logger.info(f"Discovered entry points: {[ep.name for ep in eps]}")

        for instance_id, module_cfg in modules_config.items():
            if not isinstance(module_cfg, dict) or not SAFE_NAME.match(instance_id):
                continue
            module_name = module_cfg.get("module") or instance_id  # an instance from before "module" existed
            ep = find_entry_point(module_name, eps)
            if ep is None:
                logger.warning(
                    f"Configuration '{instance_id}' references module '{module_name}', which is not installed — skipping."
                )
                continue
            if not module_cfg.get("enabled", True):
                logger.info(f"Instance '{instance_id}' is disabled in config — skipping.")
                continue
            try:
                self._start_instance(instance_id, module_name, ep, module_cfg, globals_config)
            except Exception as e:
                logger.error(f"Failed to load module {module_name} (instance: {instance_id}): {e}", exc_info=True)

    def _start_instance(self, instance_id: str, module_name: str, ep, module_cfg: dict, globals_config: dict) -> None:
        """Import the module, create its instance with its settings and helpers, and start its run_loop."""
        logger.info(f"Loading module: {module_name} (instance: {instance_id})")
        # Only its own folder, after the core's packages: a module's copy of one (uv installs every
        # dependency) never shadows them. addsitedir runs the folder's .pth files, so only here.
        if (folder := module_folder(ep)) and folder not in sys.path:
            site.addsitedir(folder)
        plugin_class = ep.load()
        package_name = plugin_class.__module__.split(".")[0]
        translations = load_translations(package_dir(ep), globals_config.get("language", "en"))
        instance_settings = _settings_for_instance(instance_id, module_cfg, globals_config, translations)

        plugin_instance = plugin_class(instance_settings)
        self.instances[instance_id] = plugin_instance
        _inject_module_helpers(plugin_instance, package_name, translations, instance_id, instance_settings, self.link)

        if not hasattr(plugin_instance, "run_loop"):
            if not inspect.iscoroutinefunction(getattr(plugin_instance, "render", None)):
                return
            plugin_instance.run_loop = functools.partial(_render_loop, instance_id, plugin_instance)
        if not inspect.iscoroutinefunction(plugin_instance.run_loop):
            # A thread can't be stopped on a reload, so it would keep running with old settings
            logger.error(f"Module {module_name} (instance: {instance_id}): run_loop must be `async def`; not started.")
            return
        self._start_module_task(instance_id, plugin_instance, self._make_broadcast_func(instance_id))

    def _make_broadcast_func(self, instance_id: str):
        """The module's broadcast_func: its HTML goes to the core, which puts it in the instance's place."""
        async def broadcast_func(module_html_name: str, html: str) -> None:
            await self.link.html(instance_id, html)
        return broadcast_func

    def _start_module_task(self, instance_id: str, plugin_instance, broadcast_fn) -> None:
        """Create and register an asyncio task for a module, restarted after a crash or a return."""
        async def run_with_recovery():
            backoff = MODULE_RESTART_DELAY
            while True:
                started = time.monotonic()
                try:
                    logger.info(f"Starting run_loop for {instance_id}")
                    await plugin_instance.run_loop(broadcast_fn)
                    logger.warning(f"Module '{instance_id}' run_loop returned. Starting it again in {backoff}s...")
                except asyncio.CancelledError:
                    logger.info(f"Module '{instance_id}' task cancelled.")
                    raise
                except Exception as e:
                    logger.error(
                        f"Module '{instance_id}' run_loop crashed: {e}. "
                        f"Restarting in {backoff}s...",
                        exc_info=True
                    )
                finally:
                    self.link.release_gpio(instance_id)  # free for its next start, which asks again
                # A loop that ran a while starts over at the short delay; one that keeps ending at once
                # waits longer each time (capped at 5 minutes) instead of spinning
                if time.monotonic() - started > 300:
                    backoff = MODULE_RESTART_DELAY
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

        task = asyncio.create_task(run_with_recovery(), name=f"module-{instance_id}")
        self.tasks[instance_id] = task

    async def control(self, name: str, value) -> None:
        """A command from Home Assistant for what the instance exposes, already checked by the core against
        its declaration and the owner's choice: the module's `on_control(name, value)`, sync or async."""
        for instance_id, plugin_instance in self.instances.items():
            handler = getattr(plugin_instance, "on_control", None)
            if not callable(handler):
                logger.warning(f"{instance_id} exposes {name} for control but has no on_control")
                continue
            try:
                result = handler(name, value)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.error(f"{instance_id}: on_control({name!r}) failed: {e}", exc_info=True)

    async def shown(self, on: bool) -> None:
        """Whether the instance's card is on the screen (the screen on, and neither its schedule nor privacy mode
        hiding it): the module's `on_shown(on)`, sync or async, at the start and on every change."""
        if on == self.last_shown:
            return
        self.last_shown = on
        for instance_id, plugin_instance in self.instances.items():
            handler = getattr(plugin_instance, "on_shown", None)
            if not callable(handler):
                continue
            try:
                result = handler(on)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                logger.error(f"{instance_id}: on_shown({on}) failed: {e}", exc_info=True)

    async def stop_modules(self) -> None:
        logger.info("Stopping all module tasks...")
        for name, task in self.tasks.items():
            task.cancel()
            logger.info(f"Cancelled task for module '{name}'")
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()
        self.instances.clear()
        # Subscribers are module instances; a reload creates new ones, so the old callbacks
        # must go or every reload would add another copy of each subscription.
        event_bus.clear()
        logger.info("All module tasks stopped.")
