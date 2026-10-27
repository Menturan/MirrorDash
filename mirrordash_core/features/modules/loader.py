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
import site
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

from mirrordash_core.config import get_core_version, get_module_dir
from mirrordash_core.event_bus import event_bus

logger = logging.getLogger("mirrordash.core.modules")


# Restart delay before retrying a crashed module (seconds)
MODULE_RESTART_DELAY = 5
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
    if not (real.is_file() and any(real.is_relative_to(root) for root in roots)):
        return None
    if real.stat().st_size > MODULE_FILE_LIMIT:  # read whole into the core: a huge one would take its memory
        logger.warning(f"{real} is over {MODULE_FILE_LIMIT // 1024} kB, not read")
        return None
    return real


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


def _request_body(json_body: object, form_data: dict | bytes | None) -> bytes | None:
    """What fetch sends: json= as JSON, data= as a form (a dict) or as it is (bytes)."""
    if json_body is not None:
        return json.dumps(json_body).encode()
    return urllib.parse.urlencode(form_data).encode() if isinstance(form_data, dict) else form_data


def _cache_file_for(cache_dir: str | None, method: str, url: str, body: bytes | None) -> str | None:
    """Where the last good answer to this exact request is kept (none without a cache_dir)."""
    if not cache_dir:
        return None
    request_hash = hashlib.sha256(f"{method} {url}\n".encode() + (body or b"")).hexdigest()[:16]
    return os.path.join(cache_dir, f"fetch-{request_hash}")


def _read_cached_answer(cache_file: str | None, parse_answer, max_age: float | None = None):
    """The saved answer, parsed; None when there is none, it doesn't parse, or it is older than max_age."""
    try:
        if max_age is not None and time.time() - os.path.getmtime(cache_file) >= max_age:
            return None
        with open(cache_file, "rb") as f:
            return parse_answer(f.read())
    except (TypeError, OSError, ValueError):  # no cache_dir, nothing saved yet, or a broken file
        return None


def _save_answer(cache_file: str | None, raw_answer: bytes, module_name: str, where: str) -> None:
    """Keep a good answer for when the next fetch fails; written whole or not at all."""
    if not cache_file:
        return
    try:
        with open(cache_file + ".tmp", "wb") as f:
            f.write(raw_answer)
        os.replace(cache_file + ".tmp", cache_file)
    except OSError as e:
        logger.debug(f"{module_name}: could not cache {where}: {e}")


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
        if json is not None and data is not None:
            raise ValueError("fetch: pass json= or data=, not both")
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        body = _request_body(json, data)
        parse_answer = parse or (lambda raw: raw)
        url_parts = urllib.parse.urlsplit(url)
        where = f"{method} {url_parts.netloc}{url_parts.path}"  # never the query, headers or body: they can hold keys
        cache_file = _cache_file_for(cache_dir, method, url, body)

        if max_age and (fresh_answer := _read_cached_answer(cache_file, parse_answer, max_age)) is not None:
            return fresh_answer, None
        if not keep_running:
            await awake.wait()

        request_headers = {"User-Agent": user_agent,
                           **({"Accept": "application/json"} if parse else {}),
                           **({"Content-Type": "application/json"} if json is not None else {}),
                           **(headers or {})}

        def download() -> bytes:
            request = urllib.request.Request(url, data=body, method=method, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()

        try:
            raw_answer = await asyncio.to_thread(download)
            answer = parse_answer(raw_answer)
        except urllib.error.HTTPError as e:
            error = "rejected" if e.code in (401, 403) else f"http {e.code}"
        except (urllib.error.URLError, OSError, http.client.HTTPException):  # timeouts, cut-off answers
            error = "offline"
        except ValueError:  # fetch_json: not JSON, or not UTF-8
            error = "invalid"
        else:
            _save_answer(cache_file, raw_answer, module_name, where)
            return answer, None

        logger.warning(f"{module_name}: fetching {where} failed ({error}); showing the last answer if there is one")
        return _read_cached_answer(cache_file, parse_answer), error

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


def _settings_for_instance(instance_id: str, module_cfg: dict, globals_config: dict, translations: dict) -> dict:
    """What a module's constructor gets: its saved settings, plus its own writable data_dir and cache_dir,
    the event bus, where its routes are served (api_path), the global settings and its translations."""
    instance_settings = module_cfg.copy()
    for kind in ("data", "cache"):
        path = os.path.join(get_module_dir(), kind, instance_id)
        try:
            os.makedirs(path, exist_ok=True)
            instance_settings[f"{kind}_dir"] = path
        except Exception as e:
            logger.warning(f"Could not create writeable {kind} directory '{path}' for '{instance_id}': {e}")
    instance_settings["event_bus"] = event_bus
    instance_settings["api_path"] = f"/api/modules/{instance_id}"
    instance_settings["globals"] = globals_config
    instance_settings["translations"] = translations
    return instance_settings


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
            return
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
