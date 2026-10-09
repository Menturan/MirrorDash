# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import functools
import hashlib
import logging
import importlib.metadata
import importlib.util
import os
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from mirrordash_core.config import load_config, get_base_dir, get_core_version
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.event_bus import event_bus
from jinja2 import Environment, PackageLoader, FileSystemLoader, ChoiceLoader, select_autoescape

logger = logging.getLogger("mirrordash.core.modules")


# Restart delay before retrying a crashed module (seconds)
MODULE_RESTART_DELAY = 5


def load_translations(package_name: str, lang: str) -> dict:
    import json
    from importlib.resources import files

    translations = {}

    # 1. Try loading base 'en.json'
    try:
        en_path = files(package_name) / "translations" / "en.json"
        if en_path.exists():
            with en_path.open("r", encoding="utf-8") as f:
                translations.update(json.load(f))
    except Exception as e:
        logger.debug(f"Could not load base English translations for {package_name}: {e}")

    # 2. Try loading configured language (if not English)
    if lang and lang != "en":
        try:
            lang_path = files(package_name) / "translations" / f"{lang}.json"
            if lang_path.exists():
                with lang_path.open("r", encoding="utf-8") as f:
                    translations.update(json.load(f))
        except Exception as e:
            logger.debug(f"Could not load translations for {package_name} in language '{lang}': {e}")

    return translations


def _make_fetch(cache_dir: str | None, module_name: str, keep_running: bool = False):
    """A module's `self.fetch` and `self.fetch_json` -> (answer, error).

    `await self.fetch(url, method="GET", headers=..., params=..., json=..., data=..., timeout=10)` gives the
    raw bytes (RSS, ICS, XML); `self.fetch_json(...)` takes the same arguments and gives the parsed JSON.
    params go in the query; json= is sent as a JSON body, data= as a form (dict) or as is (bytes).

    While the screen is off it waits until the screen is back on (unless keep_running), so a sleeping
    mirror calls no APIs and a fetch that fell due in the dark happens once, on waking.
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
            from mirrordash_core.features.power.display_power import display_power_manager
            await display_power_manager.awake.wait()

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
        except (urllib.error.URLError, OSError):  # includes timeouts
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


def _inject_module_helpers(plugin_instance, package_name: str, translations: dict, module_name: str, config: dict) -> None:
    """Inject translation, data fetching and template rendering helpers into the plugin instance if missing."""
    plugin_instance.translations = translations

    if not hasattr(plugin_instance, "translate"):
        def translate(key: str, default: str = None) -> str:
            val = plugin_instance.translations.get(key)
            if val is not None:
                return val
            return default if default is not None else key
        plugin_instance.translate = translate

    fetch, fetch_json = _make_fetch(config.get("cache_dir"), module_name, getattr(plugin_instance, "keep_running", False))
    if not hasattr(plugin_instance, "fetch"):
        plugin_instance.fetch = fetch
    if not hasattr(plugin_instance, "fetch_json"):
        plugin_instance.fetch_json = fetch_json

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


def find_entry_point(name: str, eps=None):
    """Find a module's entry point by name, treating '-' and '_' as equal (config ids use either)."""
    norm_name = name.replace('-', '_')
    eps = importlib.metadata.entry_points(group='mirrordash.modules') if eps is None else eps
    return next((ep for ep in eps if ep.name.replace('-', '_') == norm_name), None)


class ModuleLoader:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.instances: dict = {}

    async def start_modules(self) -> None:
        config = load_config()

        # Apply system settings on startup (brightness, rotation, resolution, volume)
        try:
            system_cfg = config.get("system", {})
            global_cfg = config.get("globals", {})

            # Apply display and audio settings
            if system_cfg:
                from mirrordash_core.features.hardware.display import apply_system_settings
                asyncio.create_task(apply_system_settings(
                    system_cfg.get("rotation", "normal"),
                    system_cfg.get("resolution", "auto"),
                    system_cfg.get("brightness", 100),
                    system_cfg.get("volume", 80)
                ))

            # Apply timezone
            timezone = global_cfg.get("timezone")
            if timezone:
                from mirrordash_core.host import apply_system_timezone
                asyncio.create_task(apply_system_timezone(timezone))

            # Apply SSH status
            ssh_enabled = system_cfg.get("ssh")
            if ssh_enabled is not None:
                from mirrordash_core.features.settings.ssh import set_ssh_status
                asyncio.create_task(set_ssh_status(ssh_enabled))

            # Apply persistent system password hash if present
            hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
            if os.path.exists(hash_path):
                try:
                    with open(hash_path, "r", encoding="utf-8") as f:
                        pwd_hash = f.read().strip()
                    if pwd_hash:
                        from mirrordash_core.host import apply_system_password_hash
                        asyncio.create_task(apply_system_password_hash(pwd_hash))
                except Exception as pwd_err:
                    logger.warning(f"Failed to read/apply saved password hash: {pwd_err}")

        except Exception as e:
            logger.warning(f"Failed to apply system settings on startup: {e}")

        modules_config = config.get("modules", {})

        # Discover modules via entry points
        eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))

        logger.info(f"Discovered entry points: {[ep.name for ep in eps]}")

        for instance_id, module_cfg in modules_config.items():
            if not isinstance(module_cfg, dict):
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
                base_dir = get_base_dir()
                data_dir = os.path.join(base_dir, "data", instance_id)
                cache_dir = os.path.join(base_dir, "cache", instance_id)
                try:
                    os.makedirs(data_dir, exist_ok=True)
                    module_cfg_copy["data_dir"] = data_dir
                except Exception as e:
                    logger.warning(f"Could not create writeable data directory '{data_dir}' for '{instance_id}': {e}")
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    module_cfg_copy["cache_dir"] = cache_dir
                except Exception as e:
                    logger.warning(f"Could not create writeable cache directory '{cache_dir}' for '{instance_id}': {e}")

                # Inject event bus for inter-module communication
                module_cfg_copy["event_bus"] = event_bus

                # Inject global configurations
                module_cfg_copy["globals"] = config.get("globals", {})

                # Load translations
                package_name = plugin_class.__module__.split('.')[0]
                lang = config.get("globals", {}).get("language", "en")
                translations = load_translations(package_name, lang)
                module_cfg_copy["translations"] = translations

                plugin_instance = plugin_class(module_cfg_copy)
                self.instances[instance_id] = plugin_instance

                _inject_module_helpers(plugin_instance, package_name, translations, instance_id, module_cfg_copy)

                if hasattr(plugin_instance, "run_loop"):
                    broadcast_fn = self._make_broadcast_func(instance_id, module_name)
                    self._start_module_task(instance_id, plugin_instance, broadcast_fn)
            except Exception as e:
                logger.error(f"Failed to load module {module_name} (instance: {instance_id}): {e}", exc_info=True)

    def _make_broadcast_func(self, instance_id: str, module_name: str):
        """Return a broadcast function that reads position from the cached config."""
        async def broadcast_func(module_html_name: str, html: str) -> None:
            current_config = load_config()  # Returns from cache — no disk I/O
            modules_cfg = current_config.get("modules", {})
            module_cfg = modules_cfg.get(instance_id)
            pos = "middle_center"
            carousel_group = None
            carousel_interval = 15
            max_width = None
            max_height = None
            z_index = None
            opacity = None
            if module_cfg:
                pos = module_cfg.get("position", "middle_center")
                carousel_group = module_cfg.get("carousel_group")
                carousel_interval = module_cfg.get("carousel_interval", 15)
                max_width = module_cfg.get("max_width")
                max_height = module_cfg.get("max_height")
                z_index = module_cfg.get("z_index")
                opacity = module_cfg.get("opacity")
            await manager.broadcast({
                "position": pos,
                "html": html,
                "module": instance_id,
                "carousel_group": carousel_group,
                "carousel_interval": carousel_interval,
                "max_width": max_width,
                "max_height": max_height,
                "z_index": z_index,
                "opacity": opacity,
            })
        return broadcast_func

    def _start_module_task(self, instance_id: str, plugin_instance, broadcast_fn) -> None:
        """Create and register an asyncio task for a module, with auto-restart on crash."""
        import inspect
        loop = asyncio.get_running_loop()
        is_async = inspect.iscoroutinefunction(plugin_instance.run_loop)

        async def run_with_recovery():
            backoff = MODULE_RESTART_DELAY
            while True:
                try:
                    logger.info(f"Starting {'async' if is_async else 'sync'} run_loop for {instance_id}")
                    if is_async:
                        await plugin_instance.run_loop(broadcast_fn)
                    else:
                        def sync_broadcast(module_html_name: str, html: str) -> None:
                            asyncio.run_coroutine_threadsafe(
                                broadcast_fn(module_html_name, html),
                                loop
                            )
                        await loop.run_in_executor(None, plugin_instance.run_loop, sync_broadcast)
                    # Reset backoff on successful execution
                    backoff = MODULE_RESTART_DELAY
                except asyncio.CancelledError:
                    logger.info(f"Module '{instance_id}' task cancelled.")
                    raise
                except Exception as e:
                    logger.error(
                        f"Module '{instance_id}' run_loop crashed: {e}. "
                        f"Restarting in {backoff}s...",
                        exc_info=True
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)  # Double delay, capped at 5 minutes

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
        manager.clear_cache()
        logger.info("All module tasks stopped.")

    async def reload_modules(self) -> None:
        """Stop all modules, tell clients to reload, and restart modules."""
        logger.info("Reloading all modules...")
        await self.stop_modules()
        await manager.broadcast({"action": "reload"})
        await self.start_modules()


# Singleton instance
module_loader = ModuleLoader()
