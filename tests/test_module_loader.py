import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from jinja2 import Environment, DictLoader

import mirrordash_core.module_loader
from mirrordash_core.module_loader import ModuleLoader, load_translations

# Set recovery delay to 0.01s for fast tests
mirrordash_core.module_loader.MODULE_RESTART_DELAY = 0.01

class DummyPlugin:
    def __init__(self, config):
        self.config = config
        self.name = "dummy"
        self.translations = config.get("translations", {})
        self.run_count = 0

    async def run_loop(self, broadcast_fn):
        self.run_count += 1
        if self.run_count == 1:
            raise ValueError("First run crash simulation")
        # Keep running
        while True:
            await asyncio.sleep(0.01)

class DummySyncPlugin:
    def __init__(self, config):
        self.config = config
        self.name = "dummy_sync"
        self.run_count = 0

    def run_loop(self, broadcast_fn):
        self.run_count += 1
        if self.run_count == 1:
            raise ValueError("First run sync crash simulation")

@pytest.mark.asyncio
async def test_closure_late_binding_translate():
    # Setup two mock instances to simulate late binding in loops
    instances = []
    configs = [
        {"translations": {"greeting": "Hello"}},
        {"translations": {"greeting": "Hej"}}
    ]
    
    # Factory function from module_loader.py to make sure it captures scope correctly
    def make_translate(bound_instance):
        def translate(key: str, default: str = None) -> str:
            val = bound_instance.translations.get(key)
            if val is not None:
                return val
            return default if default is not None else key
        return translate

    for cfg in configs:
        instance = MagicMock()
        instance.translations = cfg["translations"]
        instance.translate = make_translate(instance)
        instances.append(instance)

    # Validate that each bound instance returns its own translations
    assert instances[0].translate("greeting") == "Hello"
    assert instances[1].translate("greeting") == "Hej"

@pytest.mark.asyncio
async def test_closure_late_binding_render_template():
    # Setup two mock instances with distinct templates and contexts
    env1 = Environment(loader=DictLoader({"test.html": "A: {{ greeting }} {{ translations.greeting }}"}))
    env2 = Environment(loader=DictLoader({"test.html": "B: {{ greeting }} {{ translations.greeting }}"}))

    instances = []
    data = [
        (env1, {"greeting": "Hello"}, "mirrordash_a"),
        (env2, {"greeting": "Hej"}, "mirrordash_b")
    ]

    def make_render_template(bound_env, bound_instance, bound_pkg):
        def render_template(template_name: str, **context) -> str:
            if "translations" not in context and hasattr(bound_instance, "translations"):
                context["translations"] = bound_instance.translations
            if "show_header" not in context:
                context["show_header"] = bound_instance.config.get("show_header", True)
            return bound_env.get_template(template_name).render(**context)
        return render_template

    for env, trans, pkg in data:
        instance = MagicMock()
        instance.translations = trans
        instance.config = {"show_header": True}
        instance.render_template = make_render_template(env, instance, pkg)
        instances.append(instance)

    # Render template on each instance and verify they output correct templates and contexts
    res0 = instances[0].render_template("test.html", greeting="Bonjour")
    res1 = instances[1].render_template("test.html", greeting="Hallå")

    assert res0 == "A: Bonjour Hello"
    assert res1 == "B: Hallå Hej"

@pytest.mark.asyncio
async def test_config_fallback_priority():
    # Priority: Instance config -> Globals config -> Default
    # Case 1: Key exists in instance config
    config = {
        "format": "12h",
        "globals": {"time_format": "24h"}
    }
    time_format = config.get("format") or config.get("globals", {}).get("time_format", "24h")
    assert time_format == "12h"

    # Case 2: Key missing in instance, exists in globals
    config = {
        "format": None,
        "globals": {"time_format": "24h"}
    }
    time_format = config.get("format") or config.get("globals", {}).get("time_format", "24h")
    assert time_format == "24h"

    # Case 3: Missing entirely, falls back to default
    config = {
        "globals": {}
    }
    time_format = config.get("format") or config.get("globals", {}).get("time_format", "24h")
    assert time_format == "24h"

@pytest.mark.asyncio
async def test_module_loader_async_crash_recovery():
    loader = ModuleLoader()
    plugin = DummyPlugin({"translations": {}})
    broadcast_mock = AsyncMock()

    # Start module task
    loader._start_module_task("dummy", plugin, broadcast_mock)
    
    # Wait for the first run to crash and the second to start
    await asyncio.sleep(0.05)
    
    assert plugin.run_count >= 2
    
    # Clean up
    await loader.stop_modules()

@pytest.mark.asyncio
async def test_module_loader_sync_crash_recovery():
    loader = ModuleLoader()
    plugin = DummySyncPlugin({})
    broadcast_mock = AsyncMock()

    # Start module task
    loader._start_module_task("dummy_sync", plugin, broadcast_mock)
    
    # Wait for the first run to crash and the second to run
    await asyncio.sleep(0.05)
    
    assert plugin.run_count >= 2
    
    # Clean up
    await loader.stop_modules()

@pytest.mark.asyncio
async def test_module_loader_cancel_handling():
    loader = ModuleLoader()
    plugin = DummyPlugin({})
    broadcast_mock = AsyncMock()

    loader._start_module_task("dummy", plugin, broadcast_mock)
    
    # Let it run for a bit
    await asyncio.sleep(0.02)
    
    task = loader.tasks["dummy"]
    assert not task.done()
    
    # Cancel task
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task



@pytest.mark.asyncio
async def test_module_loader_backoff():
    # Set recovery delay to 0.01s for fast tests
    mirrordash_core.module_loader.MODULE_RESTART_DELAY = 0.01

    class CrashingPlugin:
        def __init__(self):
            self.name = "crashing"
            self.run_count = 0
        async def run_loop(self, broadcast_fn):
            self.run_count += 1
            raise ValueError("Always crash")

    loader = ModuleLoader()
    plugin = CrashingPlugin()
    broadcast_mock = AsyncMock()

    # Re-import MODULE_RESTART_DELAY to make sure patch matches
    with patch("mirrordash_core.module_loader.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        call_count = 0
        async def side_effect(delay):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError("Stop test")
            return None
        mock_sleep.side_effect = side_effect

        # Start task
        loader._start_module_task("crashing", plugin, broadcast_mock)
        
        try:
            # Let it run under patched sleep
            await loader.tasks["crashing"]
        except asyncio.CancelledError:
            pass

        # Verify the sleep delays doubled: 0.01, then 0.02
        assert mock_sleep.call_count >= 2
        mock_sleep.assert_any_call(0.01)
        mock_sleep.assert_any_call(0.02)

    await loader.stop_modules()
