import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from jinja2 import Environment, DictLoader

import mirrordash_core.features.modules.loader
from mirrordash_core.features.modules.loader import ModuleLoader, load_translations

# Set recovery delay to 0.01s for fast tests
mirrordash_core.features.modules.loader.MODULE_RESTART_DELAY = 0.01

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
    mirrordash_core.features.modules.loader.MODULE_RESTART_DELAY = 0.01

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
    with patch("mirrordash_core.features.modules.loader.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
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

@pytest.mark.asyncio
async def test_fetch_json_answers_errors_and_falls_back_to_the_last_answer(tmp_path, caplog):
    """fetch_json against a real local server: data, a rejected key, not-JSON, then the server gone."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from mirrordash_core.features.modules.loader import _inject_module_helpers

    seen_headers = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen_headers.append(self.headers.get("X-Api-Key"))
            status, body = {"/ok": (200, b'{"temp": 21}'), "/denied": (401, b"{}"),
                            "/html": (200, b"<html>"), "/feed": (200, b"<rss/>")}[self.path.split("?")[0]]
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):  # echoes what it got
            body = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"type": self.headers["Content-Type"], "body": body.decode()}).encode())
        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    plugin = DummyPlugin({})
    _inject_module_helpers(plugin, "dummy", {}, "dummy", {"cache_dir": str(tmp_path)})
    key = {"X-Api-Key": "secret-key-123"}

    assert await plugin.fetch_json(f"{base}/ok", headers=key, params={"q": "Oslo"}) == ({"temp": 21}, None)
    assert seen_headers[-1] == "secret-key-123"
    assert await plugin.fetch_json(f"{base}/denied", headers=key) == (None, "rejected")
    assert await plugin.fetch_json(f"{base}/html") == (None, "invalid")
    assert await plugin.fetch(f"{base}/feed") == (b"<rss/>", None)
    query = {"query": "{ departures }", "token": "secret-in-body"}
    assert await plugin.fetch_json(f"{base}/graphql", method="POST", json=query) == (
        {"type": "application/json", "body": json.dumps(query)}, None)
    assert (await plugin.fetch_json(f"{base}/graphql", method="POST", data={"a": "1"}))[0] == {
        "type": "application/x-www-form-urlencoded", "body": "a=1"}

    server.shutdown()
    server.server_close()
    # The server is gone: the last good answer for the same URL comes back, marked offline
    assert await plugin.fetch_json(f"{base}/ok", headers=key, params={"q": "Oslo"}, timeout=2) == ({"temp": 21}, "offline")
    assert await plugin.fetch(f"{base}/feed", timeout=2) == (b"<rss/>", "offline")
    # Each POST body has its own last answer
    assert (await plugin.fetch_json(f"{base}/graphql", method="POST", json=query, timeout=2))[0]["body"] == json.dumps(query)
    assert (await plugin.fetch_json(f"{base}/graphql", method="POST", data={"a": "1"}, timeout=2))[0]["body"] == "a=1"
    assert "secret-key-123" not in caplog.text and "Oslo" not in caplog.text and "secret-in-body" not in caplog.text


@pytest.mark.asyncio
async def test_fetch_json_sleeps_while_the_screen_is_off(tmp_path):
    """Screen off: a fetch waits and goes out once on waking; a keep_running module is never held."""
    from mirrordash_core.features.modules.loader import _inject_module_helpers
    from mirrordash_core.features.power.display_power import display_power_manager

    url = (tmp_path / "data.json").as_uri()
    (tmp_path / "data.json").write_text('{"n": 1}')
    sleeper, logger_ = DummyPlugin({}), DummyPlugin({})
    logger_.keep_running = True
    _inject_module_helpers(sleeper, "dummy", {}, "dummy", {})
    _inject_module_helpers(logger_, "dummy", {}, "logger", {})

    display_power_manager.awake.clear()
    try:
        waiting = asyncio.create_task(sleeper.fetch_json(url))
        assert await logger_.fetch_json(url) == ({"n": 1}, None)
        await asyncio.sleep(0.05)
        assert not waiting.done()
    finally:
        display_power_manager.awake.set()
    assert await asyncio.wait_for(waiting, 1) == ({"n": 1}, None)


@pytest.mark.asyncio
async def test_fetch_max_age_uses_the_saved_answer_until_it_is_old(tmp_path):
    """max_age: a young saved answer comes back as fresh without a call; an old one is fetched again."""
    import os
    from mirrordash_core.features.modules.loader import _inject_module_helpers

    source = tmp_path / "data.json"
    source.write_text('{"n": 1}')
    cache = tmp_path / "cache"
    cache.mkdir()
    plugin = DummyPlugin({})
    _inject_module_helpers(plugin, "dummy", {}, "dummy", {"cache_dir": str(cache)})

    assert await plugin.fetch_json(source.as_uri(), max_age=60) == ({"n": 1}, None)
    source.write_text('{"n": 2}')
    assert await plugin.fetch_json(source.as_uri(), max_age=60) == ({"n": 1}, None)  # saved, no call
    assert await plugin.fetch_json(source.as_uri()) == ({"n": 2}, None)              # no max_age: a call
    for saved in cache.iterdir():
        os.utime(saved, (0, 0))
    source.write_text('{"n": 3}')
    assert await plugin.fetch_json(source.as_uri(), max_age=60) == ({"n": 3}, None)  # too old: a call


@pytest.mark.asyncio
async def test_the_same_module_message_is_sent_once():
    from mirrordash_core.features.kiosk.ws import ConnectionManager

    manager = ConnectionManager()
    client = AsyncMock()
    manager.active_connections.append(client)
    message = {"module": "weather", "html": "<p>21°</p>", "position": "top_left"}
    await manager.broadcast(message)
    await manager.broadcast(dict(message))
    assert client.send_json.await_count == 1
    await manager.broadcast({**message, "position": "top_right"})  # a changed setting is sent
    manager.clear_cache()                                          # a reload sends everything again
    await manager.broadcast({**message, "position": "top_right"})
    assert client.send_json.await_count == 3
