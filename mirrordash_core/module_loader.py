# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import importlib.metadata
import importlib.util
import os
import json
from mirrordash_core.config import load_config, find_module_config, get_base_dir
from mirrordash_core.ws_manager import manager
from mirrordash_core.event_bus import event_bus
from jinja2 import Environment, PackageLoader, FileSystemLoader, ChoiceLoader, select_autoescape

logger = logging.getLogger("mirrordash.core.module_loader")

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
                from mirrordash_core.system import apply_system_settings
                asyncio.create_task(apply_system_settings(
                    system_cfg.get("rotation", "normal"),
                    system_cfg.get("resolution", "auto"),
                    system_cfg.get("brightness", 100),
                    system_cfg.get("volume", 80)
                ))

            # Apply timezone
            timezone = global_cfg.get("timezone")
            if timezone:
                from mirrordash_core.system import apply_system_timezone
                asyncio.create_task(apply_system_timezone(timezone))

            # Apply SSH status
            ssh_enabled = system_cfg.get("ssh")
            if ssh_enabled is not None:
                from mirrordash_core.system import set_ssh_status
                asyncio.create_task(set_ssh_status(ssh_enabled))

            # Apply persistent system password hash if present
            hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
            if os.path.exists(hash_path):
                try:
                    with open(hash_path, "r", encoding="utf-8") as f:
                        pwd_hash = f.read().strip()
                    if pwd_hash:
                        from mirrordash_core.system import apply_system_password_hash
                        asyncio.create_task(apply_system_password_hash(pwd_hash))
                except Exception as pwd_err:
                    logger.warning(f"Failed to read/apply saved password hash: {pwd_err}")

        except Exception as e:
            logger.warning(f"Failed to apply system settings on startup: {e}")

        modules_config = config.get("modules", {})

        # Discover modules via entry points
        eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))

        logger.info(f"Discovered entry points: {[ep.name for ep in eps]}")

        for ep in eps:
            module_name = ep.name
            config_key, module_cfg = find_module_config(modules_config, module_name)
            if config_key is None:
                logger.warning(
                    f"Module '{module_name}' is installed but not in config.json — skipping. "
                    "Add it to config.json to enable it."
                )
                continue
            if not module_cfg.get("enabled", True):
                logger.info(f"Module '{module_name}' is disabled in config — skipping.")
                continue
            try:
                logger.info(f"Loading module: {module_name}")
                plugin_class = ep.load()

                # Pre-create and inject writeable data and cache directory paths
                module_cfg_copy = module_cfg.copy()
                base_dir = get_base_dir()
                data_dir = os.path.join(base_dir, "data", module_name)
                cache_dir = os.path.join(base_dir, "cache", module_name)
                try:
                    os.makedirs(data_dir, exist_ok=True)
                    module_cfg_copy["data_dir"] = data_dir
                except Exception as e:
                    logger.warning(f"Could not create writeable data directory '{data_dir}' for '{module_name}': {e}")
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    module_cfg_copy["cache_dir"] = cache_dir
                except Exception as e:
                    logger.warning(f"Could not create writeable cache directory '{cache_dir}' for '{module_name}': {e}")

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
                self.instances[module_name] = plugin_instance

                # Attach translations and helper to the instance
                plugin_instance.translations = translations
                if not hasattr(plugin_instance, "translate"):
                    def make_translate(bound_instance):
                        def translate(key: str, default: str = None) -> str:
                            val = bound_instance.translations.get(key)
                            if val is not None:
                                return val
                            return default if default is not None else key
                        return translate
                    plugin_instance.translate = make_translate(plugin_instance)

                # Auto-inject render_template helper if templates/ folder exists in the module's package
                if not hasattr(plugin_instance, "render_template"):
                    try:
                        from jinja2 import Environment, PackageLoader, FileSystemLoader, ChoiceLoader, select_autoescape

                        loaders = []

                        # 1. Try standard PackageLoader
                        try:
                            loaders.append(PackageLoader(package_name, "templates"))
                        except Exception:
                            pass

                        # 2. Try resolving physical path for FileSystemLoader fallback (especially for editable installs)
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
                            # Expected if the package has no templates directory
                            raise ValueError(f"No templates directory found for package '{package_name}'")

                        env = Environment(
                            loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
                            autoescape=select_autoescape(["html", "xml"])
                        )
                        logger.info(f"Auto-injected templates loaders for {package_name}: {loaders}")
                        try:
                            # Test if widget.html can be listed or found by the loader
                            templates_list = env.loader.list_templates()
                            logger.info(f"Available templates for {package_name}: {templates_list}")
                        except Exception as list_err:
                            logger.warning(f"Failed to list templates for {package_name}: {list_err}")

                        # Define a factory to avoid closure late-binding issue in loops
                        def make_render_template(bound_env, bound_instance, bound_pkg):
                            def render_template(template_name: str, **context) -> str:
                                if "translations" not in context and hasattr(bound_instance, "translations"):
                                    context["translations"] = bound_instance.translations
                                if "show_header" not in context:
                                    context["show_header"] = bound_instance.config.get("show_header", True)
                                try:
                                    return bound_env.get_template(template_name).render(**context)
                                except Exception as render_err:
                                    logger.error(f"Failed to render template {template_name} in {bound_pkg}: {render_err}", exc_info=True)
                                    raise
                            return render_template

                        plugin_instance.render_template = make_render_template(env, plugin_instance, package_name)
                        logger.debug(f"Auto-injected render_template helper for module '{module_name}'")
                    except ValueError:
                        # Expected if the package has no templates directory
                        pass
                    except Exception as e:
                        logger.warning(f"Could not auto-inject render_template helper for '{module_name}': {e}")

                if hasattr(plugin_instance, "run_loop"):
                    broadcast_fn = self._make_broadcast_func(module_name)
                    self._start_module_task(module_name, plugin_instance, broadcast_fn)
            except Exception as e:
                logger.error(f"Failed to load module {module_name}: {e}", exc_info=True)

    def _make_broadcast_func(self, name: str):
        """Return a broadcast function that reads position from the cached config."""
        async def broadcast_func(module_html_name: str, html: str) -> None:
            current_config = load_config()  # Returns from cache — no disk I/O
            modules_cfg = current_config.get("modules", {})
            _, module_cfg = find_module_config(modules_cfg, name)
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
                "module": name,
                "carousel_group": carousel_group,
                "carousel_interval": carousel_interval,
                "max_width": max_width,
                "max_height": max_height,
                "z_index": z_index,
                "opacity": opacity,
            })
        return broadcast_func

    def _start_module_task(self, module_name: str, plugin_instance, broadcast_fn) -> None:
        """Create and register an asyncio task for a module, with auto-restart on crash."""
        import inspect
        loop = asyncio.get_running_loop()
        is_async = inspect.iscoroutinefunction(plugin_instance.run_loop)

        async def run_with_recovery():
            backoff = MODULE_RESTART_DELAY
            while True:
                try:
                    logger.info(f"Starting {'async' if is_async else 'sync'} run_loop for {module_name}")
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
                    logger.info(f"Module '{module_name}' task cancelled.")
                    raise
                except Exception as e:
                    logger.error(
                        f"Module '{module_name}' run_loop crashed: {e}. "
                        f"Restarting in {backoff}s...",
                        exc_info=True
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 300)  # Double delay, capped at 5 minutes

        task = asyncio.create_task(run_with_recovery(), name=f"module-{module_name}")
        self.tasks[module_name] = task

    async def stop_modules(self) -> None:
        logger.info("Stopping all module tasks...")
        for name, task in self.tasks.items():
            task.cancel()
            logger.info(f"Cancelled task for module '{name}'")
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()
        self.instances.clear()
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
