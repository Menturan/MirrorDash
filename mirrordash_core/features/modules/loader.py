# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Runs the modules. ModuleLoader lives in the module host process (host.py), as its own user, never in
the core's: the core reads a module's files (schema, icon, translations) with the helpers at the top of
this file, which import nothing of the module (ARCHITECTURE #41)."""

import asyncio
import contextlib
import functools
import hashlib
import http.client
import importlib.machinery
import importlib.metadata
import importlib.util
import inspect
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)

from mirrordash_core.config import get_core_version, get_module_dir, load_config
from mirrordash_core.event_bus import event_bus

logger = logging.getLogger("mirrordash.core.modules")


# Restart delay before retrying a crashed module (seconds)
MODULE_RESTART_DELAY = 5
# A module or instance name: it ends up in HTML ids, JS strings, URLs and folder names
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def module_site() -> Path:
    """Where modules are installed: the module host's own folder, a link it switches between site_a and
    site_b (host.py). Never on the core's sys.path: the core reads its files only, so a package's .pth or
    one that shadows the core's own runs nowhere but in the module host (ARCHITECTURE #41)."""
    return get_module_dir() / "site"


def module_search_path() -> list[str]:
    """The module site, then this process's sys.path (a module installed into a developer's venv)."""
    return [str(module_site()), *sys.path]


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
        spec = importlib.machinery.PathFinder.find_spec(top, module_search_path())
    except (AttributeError, TypeError, ImportError, ValueError):  # not a real entry point, or a bad name
        return None
    if not spec:
        return None
    if spec.submodule_search_locations:
        return Path(list(spec.submodule_search_locations)[0], *rest[:-1])
    return Path(spec.origin).parent if spec.origin else None


def module_file(pkg_dir: Path | None, *parts: str) -> Path | None:
    """A file in a module's package, for the core to read, or None. One that really lies outside the
    folders modules are found in isn't read: the module host writes the site, and a link it put there to
    the core's data would have the core show that (a translated title on the screen)."""
    if pkg_dir is None:
        return None
    real = Path(pkg_dir, *parts).resolve()
    roots = [Path(p).resolve() for p in module_search_path() if p]
    return real if real.is_file() and any(real.is_relative_to(root) for root in roots) else None


def load_translations(pkg_dir: Path | None, lang: str) -> dict:
    """The module's translations/en.json, with the configured language's file over it."""
    translations = {}
    for code in dict.fromkeys(["en", lang or "en"]):
        try:
            if path := module_file(pkg_dir, "translations", f"{code}.json"):
                translations.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.debug(f"Could not load translations from {pkg_dir} in language '{code}': {e}")
    return translations


def _make_fetch(cache_dir: str | None, module_name: str, awake: asyncio.Event, keep_running: bool = False):
    """A module's `self.fetch` and `self.fetch_json` -> (answer, error).

    `await self.fetch(url, method="GET", headers=..., params=..., json=..., data=..., timeout=10)` gives the
    raw bytes (RSS, ICS, XML); `self.fetch_json(...)` takes the same arguments and gives the parsed JSON.
    params go in the query; json= is sent as a JSON body, data= as a form (dict) or as is (bytes).

    While the screen is off (awake cleared) it waits until the screen is back on (unless keep_running), so
    a sleeping mirror calls no APIs and a fetch that fell due in the dark happens once, on waking.
    ponytail: only fetches through here sleep; a module with its own HTTP client keeps polling.

    error is None, "rejected" (401/403: usually the API key), "offline" (no answer), "http <code>" or
    "invalid" (fetch_json: not JSON). On an error, answer is the last good one for the same method, URL
    and body (kept in the module's cache_dir), or None.
    ponytail: no retry or backoff; the module's own interval is the retry. Upgrade path: backoff here.

    max_age=seconds: a saved answer younger than that comes back as fresh (error None) without a call, so
    saving a setting or a restart doesn't fetch everything again. ponytail: after a restart the next call
    can come up to two intervals after the last one, once; the cache file's mtime is the clock, so a Pi
    booting on an old time may make one extra call. Upgrade path: return the answer's age.
    """
    user_agent = f"MirrorDash/{get_core_version()}"

    async def fetch(url: str, *, parse=None, method: str = "GET", headers: dict | None = None,
                    params: dict | None = None, json: object = None, data: dict | bytes | None = None,
                    timeout: float = 10, max_age: float | None = None) -> tuple[object, str | None]:
        from json import dumps  # the json= argument hides the module
        if json is not None and data is not None:
            raise ValueError("fetch: pass json= or data=, not both")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = (dumps(json).encode() if json is not None
                else urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data)
        accept = {"Accept": "application/json"} if parse else {}
        parse = parse or (lambda raw: raw)
        parts = urllib.parse.urlsplit(url)
        where = f"{method} {parts.netloc}{parts.path}"  # never the query, headers or body: they can hold keys
        key = hashlib.sha256(f"{method} {url}\n".encode() + (body or b"")).hexdigest()[:16]
        cache_file = os.path.join(cache_dir, f"fetch-{key}") if cache_dir else None

        if max_age and cache_file:
            try:
                if time.time() - os.path.getmtime(cache_file) < max_age:
                    with open(cache_file, "rb") as f:
                        return parse(f.read()), None
            except (OSError, ValueError):  # nothing saved yet, or a broken file: fetch
                pass
        if not keep_running:
            await awake.wait()

        def get() -> bytes:
            request = urllib.request.Request(url, data=body, method=method, headers={
                "User-Agent": user_agent,
                **accept,
                **({"Content-Type": "application/json"} if json is not None else {}),
                **(headers or {})})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()

        try:
            raw = await asyncio.to_thread(get)
            answer = parse(raw)
        except urllib.error.HTTPError as e:
            error = "rejected" if e.code in (401, 403) else f"http {e.code}"
        except (urllib.error.URLError, OSError, http.client.HTTPException):  # timeouts, cut-off answers
            error = "offline"
        except ValueError:  # fetch_json: not JSON, or not UTF-8
            error = "invalid"
        else:
            if cache_file:
                try:
                    with open(cache_file + ".tmp", "wb") as f:
                        f.write(raw)
                    os.replace(cache_file + ".tmp", cache_file)
                except OSError as e:
                    logger.debug(f"{module_name}: could not cache {where}: {e}")
            return answer, None

        logger.warning(f"{module_name}: fetching {where} failed ({error}); showing the last answer if there is one")
        try:
            with open(cache_file, "rb") as f:
                return parse(f.read()), error
        except (TypeError, OSError, ValueError):  # no cache_dir, nothing cached yet, or a broken file
            return None, error

    return (functools.partial(fetch, parse=None),
            functools.partial(fetch, parse=lambda raw: json.loads(raw.decode("utf-8"))))


def _inject_module_helpers(plugin_instance, package_name: str, translations: dict, module_name: str, config: dict, link) -> None:
    """Inject translation, data fetching and template rendering helpers into the plugin instance if missing.
    link is the way to the core (host.py's HostLink): the screen's state, video playing and its window."""
    plugin_instance.translations = translations

    if not hasattr(plugin_instance, "translate"):
        def translate(key: str, default: str = None) -> str:
            val = plugin_instance.translations.get(key)
            if val is not None:
                return val
            return default if default is not None else key
        plugin_instance.translate = translate

    fetch, fetch_json = _make_fetch(config.get("cache_dir"), module_name, link.awake, getattr(plugin_instance, "keep_running", False))
    if not hasattr(plugin_instance, "fetch"):
        plugin_instance.fetch = fetch
    if not hasattr(plugin_instance, "fetch_json"):
        plugin_instance.fetch_json = fetch_json

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

    if not hasattr(plugin_instance, "gpio"):
        # `lines = await self.gpio([17], output=True)`, then lines.set(17, 1): free header pins only, given by
        # the core (features/hardware/gpio.py); options: output, initial, pull, edge, active_low, debounce_ms
        plugin_instance.gpio = functools.partial(link.gpio, module_name)

    if not hasattr(plugin_instance, "render_template"):
        try:
            loaders = []
            try:
                loaders.append(PackageLoader(package_name, "templates"))
            except Exception:
                pass

            try:
                spec = importlib.util.find_spec(package_name)
                if spec and spec.origin:
                    pkg_dir = os.path.dirname(spec.origin)
                    templates_dir = os.path.join(pkg_dir, "templates")
                    if os.path.isdir(templates_dir):
                        loaders.append(FileSystemLoader(templates_dir))
            except Exception:
                pass

            if not loaders:
                return

            env = Environment(
                loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
                autoescape=select_autoescape(["html", "xml"])
            )

            def render_template(template_name: str, **context) -> str:
                if "translations" not in context and hasattr(plugin_instance, "translations"):
                    context["translations"] = plugin_instance.translations
                if "show_header" not in context:
                    context["show_header"] = config.get("show_header", True)
                try:
                    return env.get_template(template_name).render(**context)
                except Exception as render_err:
                    logger.error(f"Failed to render template {template_name} in {package_name}: {render_err}", exc_info=True)
                    raise

            plugin_instance.render_template = render_template
            logger.debug(f"Auto-injected render_template helper for module '{module_name}'")
        except Exception as e:
            logger.warning(f"Could not auto-inject render_template helper for '{module_name}': {e}")


def start_system() -> None:
    """Apply the saved system settings once, at startup (app.py lifespan). A module reload doesn't: each
    setting's own save applies it, and doing all of them again flickered the screen and restarted Matter."""
    config = load_config()
    system_cfg = config.get("system", {})
    try:
        if system_cfg:
            from mirrordash_core.features.hardware.display import apply_system_settings
            asyncio.create_task(apply_system_settings(
                system_cfg.get("rotation", "normal"),
                system_cfg.get("resolution", "auto"),
                system_cfg.get("brightness", 100),
                system_cfg.get("volume", 80)
            ))

        timezone = config.get("globals", {}).get("timezone")
        if timezone:
            from mirrordash_core.host import apply_system_timezone
            asyncio.create_task(apply_system_timezone(timezone))

        if system_cfg.get("ssh") is not None:
            from mirrordash_core.features.settings.ssh import set_ssh_status
            asyncio.create_task(set_ssh_status(system_cfg["ssh"]))

        # Matter: the core starts the node, /etc forgets `systemctl enable` (features/matter)
        if config.get("matter", {}).get("enabled"):
            from mirrordash_core.features.matter.routes import start_matter
            asyncio.create_task(start_matter())

        # The pi password lives on /storage; the read-only root forgets it at every boot
        from mirrordash_core.features.settings.ssh import PASSWORD_HASH_FILE
        if os.path.exists(PASSWORD_HASH_FILE):
            with open(PASSWORD_HASH_FILE, encoding="utf-8") as f:
                pwd_hash = f.read().strip()
            if pwd_hash:
                from mirrordash_core.host import apply_system_password_hash
                asyncio.create_task(apply_system_password_hash(pwd_hash))
    except Exception as e:
        logger.warning(f"Failed to apply system settings on startup: {e}")


class ModuleLoader:
    """The running module instances, in the module host process. link: the way to the core (HostLink)."""

    def __init__(self, link):
        self.link = link
        self.tasks: dict[str, asyncio.Task] = {}
        self.instances: dict = {}

    async def start_modules(self, modules_config: dict, globals_config: dict) -> None:
        """Start an instance for each enabled entry of modules_config (the core's config["modules"])."""
        eps = module_entry_points()
        logger.info(f"Discovered entry points: {[ep.name for ep in eps]}")
        lang = globals_config.get("language", "en")

        for instance_id, module_cfg in modules_config.items():
            if not isinstance(module_cfg, dict) or not SAFE_NAME.match(instance_id):
                continue

            module_name = module_cfg.get("module")
            if not module_name:
                # If no module name is set, fall back to instance_id (e.g. legacy/direct config)
                module_name = instance_id

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
                logger.info(f"Loading module: {module_name} (instance: {instance_id})")
                plugin_class = ep.load()

                # Pre-create and inject writeable data and cache directory paths
                module_cfg_copy = module_cfg.copy()
                for kind in ("data", "cache"):
                    path = os.path.join(get_module_dir(), kind, instance_id)
                    try:
                        os.makedirs(path, exist_ok=True)
                        module_cfg_copy[f"{kind}_dir"] = path
                    except Exception as e:
                        logger.warning(f"Could not create writeable {kind} directory '{path}' for '{instance_id}': {e}")

                # Inject event bus for inter-module communication
                module_cfg_copy["event_bus"] = event_bus

                # Where the module's own routes are served (host.py), so it can tell its users
                module_cfg_copy["api_path"] = f"/api/modules/{instance_id}"

                # Inject global configurations
                module_cfg_copy["globals"] = globals_config

                # Load translations
                package_name = plugin_class.__module__.split('.')[0]
                translations = load_translations(package_dir(ep), lang)
                module_cfg_copy["translations"] = translations

                plugin_instance = plugin_class(module_cfg_copy)
                self.instances[instance_id] = plugin_instance

                _inject_module_helpers(plugin_instance, package_name, translations, instance_id, module_cfg_copy, self.link)

                if hasattr(plugin_instance, "run_loop"):
                    if not inspect.iscoroutinefunction(plugin_instance.run_loop):
                        # A thread can't be stopped on a reload, so it would keep running with old settings
                        logger.error(f"Module {module_name} (instance: {instance_id}): run_loop must be `async def`; not started.")
                        continue
                    self._start_module_task(instance_id, plugin_instance, self._make_broadcast_func(instance_id))
            except Exception as e:
                logger.error(f"Failed to load module {module_name} (instance: {instance_id}): {e}", exc_info=True)

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
