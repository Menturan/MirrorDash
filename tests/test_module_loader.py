import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mirrordash_core.features.modules.loader
from mirrordash_core.features.modules.loader import ModuleLoader

# Set recovery delay to 0.01s for fast tests
mirrordash_core.features.modules.loader.MODULE_RESTART_DELAY = 0.01


class FakeLink:
    """host.py's HostLink without a core: what the modules' helpers call."""
    def __init__(self):
        self.awake = asyncio.Event()
        self.awake.set()
        self.html = AsyncMock()
        self.video_playing = AsyncMock()
        self.video_window = MagicMock()
        self.gpio = AsyncMock()
        self.release_gpio = MagicMock()


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


def test_helpers_give_each_module_its_own_translations_and_templates(tmp_path, monkeypatch):
    """Two modules loaded one after the other keep their own translate() and render_template(): the
    helpers are closures over each instance (a late-binding bug once gave every module the last one's),
    and the configured language's file goes over en.json."""
    from mirrordash_core.features.modules.loader import (
        _inject_module_helpers,
        load_translations,
    )

    for pkg, greeting in (("md_test_a", "Hej"), ("md_test_b", "Hallå")):
        (tmp_path / pkg / "translations").mkdir(parents=True)
        (tmp_path / pkg / "templates").mkdir()
        (tmp_path / pkg / "__init__.py").write_text("")
        (tmp_path / pkg / "translations" / "en.json").write_text('{"greeting": "Hello", "bye": "Bye"}')
        (tmp_path / pkg / "translations" / "sv.json").write_text(f'{{"greeting": "{greeting}"}}')
        (tmp_path / pkg / "templates" / "card.html").write_text(pkg + ": {{ translations.greeting }} {{ show_header }}")
    monkeypatch.syspath_prepend(str(tmp_path))

    instances = []
    for pkg in ("md_test_a", "md_test_b"):
        instance = type("Plugin", (), {})()
        _inject_module_helpers(instance, pkg, load_translations(tmp_path / pkg, "sv"), pkg, {"show_header": False}, FakeLink())
        instances.append(instance)

    a, b = instances
    assert (a.translate("greeting"), b.translate("greeting")) == ("Hej", "Hallå")
    assert a.translate("bye") == "Bye" and a.translate("missing") == "missing" and a.translate("missing", "x") == "x"
    assert a.render_template("card.html") == "md_test_a: Hej False"
    assert b.render_template("card.html") == "md_test_b: Hallå False"
    assert load_translations(tmp_path / "md_test_a", "en") == {"greeting": "Hello", "bye": "Bye"}


def test_the_core_finds_a_modules_files_without_running_its_code(tmp_path, monkeypatch):
    """package_dir, get_module_schema and load_translations read files: a module whose __init__ raises
    (it would, if imported) still has its schema, icon and title, and stays unimported."""
    import sys

    from mirrordash_core.features.modules.loader import load_translations, package_dir
    from mirrordash_core.features.settings.schema import get_module_schema

    pkg = tmp_path / "md_untouched"
    (pkg / "translations").mkdir(parents=True)
    (pkg / "__init__.py").write_text("raise RuntimeError('imported')")
    (pkg / "plugin.py").write_text("raise RuntimeError('imported')")
    (pkg / "config_schema.json").write_text('{"title": "Untouched", "properties": {"url": {"type": "string"}}}')
    (pkg / "icon.svg").write_text('<svg onload="alert(1)"></svg>')
    (pkg / "translations" / "en.json").write_text('{"title": "Untouched"}')
    # A link out of the folders modules are found in (to the core's data, in the module host's site) isn't read
    secret = tmp_path.parent / f"{tmp_path.name}-core-data.json"
    secret.write_text('{"title": "the core\'s secret"}')
    (pkg / "translations" / "sv.json").symlink_to(secret)
    monkeypatch.syspath_prepend(str(tmp_path))
    ep = type("EP", (), {"name": "md_untouched", "module": "md_untouched.plugin"})()

    assert package_dir(ep) == pkg
    schema = get_module_schema(ep)
    assert schema["title"] == "Untouched" and schema["icon"].startswith("data:image/svg+xml;base64,")
    assert load_translations(package_dir(ep), "sv") == {"title": "Untouched"}
    assert "md_untouched" not in sys.modules


def test_admin_forms_escape_what_a_module_schema_says():
    """A module's schema is the module author's text: it lands escaped, and keys that would reach
    onclick/hx-vals JS are left out."""
    from mirrordash_core.features.modules.service import module_icon
    from mirrordash_core.forms import render_array_item, render_schema_form

    evil = '"><img src=x onerror=alert(1)>'
    schema = {"properties": {
        "url": {"type": "string", "title": evil, "description": evil},
        "mode": {"type": "string", "enum": ["a", evil], "enum_titles": [evil, "b"]},
        "list": {"type": "array", "items": {"type": "object", "title": evil, "properties": {"x": {"type": "string"}}}},
        "level": {"type": "integer", "minimum": evil, "maximum": 3},
        "x'onclick='alert(1)": {"type": "string"},
    }}
    html = render_schema_form(schema, {}, "modules[clock]", "mirrordash_clock")
    assert "<img" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "modules[clock][x" not in html and 'name="modules[clock][url]"' in html  # the bad key's field is left out
    assert "<img" not in render_array_item("modules[clock]", "list", {"x": {"type": "string", "title": evil}}, 0, {}, evil)
    with pytest.raises(ValueError):
        render_array_item("modules[clock]\"onmouseover=\"x", "list", {}, 0, {}, "Item")
    assert module_icon("clock", {"icon": '<svg onload="alert(1)">'}) == "fa-clock"
    assert module_icon("x", {"icon": "fa-house"}) == "fa-house"


@pytest.mark.asyncio
async def test_module_loader_async_crash_recovery():
    loader = ModuleLoader(FakeLink())
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
async def test_a_sync_run_loop_is_not_started(monkeypatch):
    """A thread can't be stopped on a reload, so only an async run_loop runs."""
    class SyncPlugin:
        def __init__(self, config):
            pass

        def run_loop(self, broadcast_fn):
            pass

    class EntryPoint:
        name = "sync_mod"
        module = "md_missing.plugin"

        def load(self):
            return SyncPlugin

    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", "/tmp/mirrordash-test-modules")
    monkeypatch.setattr(mirrordash_core.features.modules.loader, "module_entry_points", lambda: [EntryPoint()])
    loader = ModuleLoader(FakeLink())
    await loader.start_modules({"sync_mod": {"module": "sync_mod"}, "../escape": {"module": "sync_mod"}}, {})
    assert list(loader.instances) == ["sync_mod"] and loader.tasks == {}


@pytest.mark.asyncio
async def test_a_run_loop_that_returns_waits_before_it_starts_again():
    class OncePlugin:
        run_count = 0

        async def run_loop(self, broadcast_fn):
            self.run_count += 1

    loader = ModuleLoader(FakeLink())
    plugin = OncePlugin()
    with patch("mirrordash_core.features.modules.loader.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        loader._start_module_task("once", plugin, AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await loader.tasks["once"]
    assert plugin.run_count == 2
    assert [c.args[0] for c in mock_sleep.call_args_list] == [0.01, 0.02]
    await loader.stop_modules()


@pytest.mark.asyncio
async def test_module_loader_cancel_handling():
    loader = ModuleLoader(FakeLink())
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

    loader = ModuleLoader(FakeLink())
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
    _inject_module_helpers(plugin, "dummy", {}, "dummy", {"cache_dir": str(tmp_path)}, FakeLink())
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

    url = (tmp_path / "data.json").as_uri()
    (tmp_path / "data.json").write_text('{"n": 1}')
    sleeper, logger_ = DummyPlugin({}), DummyPlugin({})
    logger_.keep_running = True
    link = FakeLink()
    _inject_module_helpers(sleeper, "dummy", {}, "dummy", {}, link)
    _inject_module_helpers(logger_, "dummy", {}, "logger", {}, link)

    link.awake.clear()
    waiting = asyncio.create_task(sleeper.fetch_json(url))
    assert await logger_.fetch_json(url) == ({"n": 1}, None)
    await asyncio.sleep(0.05)
    assert not waiting.done()
    link.awake.set()
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
    _inject_module_helpers(plugin, "dummy", {}, "dummy", {"cache_dir": str(cache)}, FakeLink())

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


@pytest.mark.asyncio
async def test_a_module_reload_leaves_the_system_settings_alone():
    """Saving a module reloads the modules only: re-applying the system settings flickered the screen
    and restarted the Matter node each time."""
    from mirrordash_core.features.modules.client import ModuleHost
    with patch("mirrordash_core.features.matter.routes.start_matter", new_callable=AsyncMock) as matter, \
         patch("mirrordash_core.features.settings.ssh.set_ssh_status", new_callable=AsyncMock) as ssh, \
         patch("mirrordash_core.features.modules.client.manager") as manager:
        manager.broadcast = AsyncMock()
        await ModuleHost().reload_modules()
        await asyncio.sleep(0)
    assert not matter.called and not ssh.called
    manager.broadcast.assert_awaited_once_with({"action": "reload"})


@pytest.mark.asyncio
async def test_lan_access_is_what_the_owner_typed_on_the_home_network(tmp_path, monkeypatch):
    """Only "format": "lan-url" settings count, only home-network addresses come out of them, and each
    instance's user may reach its own (mirrordash-module-network's nftables rules), not another's."""
    from mirrordash_core.features.modules import client as client_module
    from mirrordash_core.features.modules.client import lan_addresses, lan_hosts

    schemas = {"md_ha": {"properties": {"url": {"type": "string", "format": "lan-url"}, "other": {"type": "string"}}}}
    modules = {"ha": {"module": "md_ha", "url": "http://192.168.1.20:8123", "other": "http://10.0.0.1"},
               "ha-2": {"module": "md_ha", "url": "homeassistant.local", "enabled": False},
               "ha-3": {"module": "md_ha"}, "cam": {"module": "md_ha", "url": "192.168.1.30"}}
    with patch("mirrordash_core.features.settings.schema._installed_module_schemas", return_value=schemas):
        assert lan_hosts(modules) == {"ha": {"192.168.1.20"}, "cam": {"192.168.1.30"}}
        assert await lan_addresses({"192.168.1.20", "8.8.8.8", "127.0.0.1", "fd00::5"}) == {
            "127.0.0.1": [], "192.168.1.20": ["192.168.1.20"], "8.8.8.8": [], "fd00::5": ["fd00::5"]}

        (tmp_path / "helper").write_text("")
        monkeypatch.setattr(client_module, "LAN_HELPER", str(tmp_path / "helper"))
        monkeypatch.setenv("MIRRORDASH_MODULE_SOCKET", str(tmp_path / "host.sock"))
        process = MagicMock(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as run:
            core = client_module.ModuleHost()
            core._uids = {"ha": 70100, "cam": 70101}
            await core._apply_lan(modules)
    assert run.await_args.args == ("sudo", "-n", str(tmp_path / "helper"), "70101=192.168.1.30", "70100=192.168.1.20")

    # The schema's default is a hint in the form, never a value: saving it unchanged opens nothing
    from mirrordash_core.forms import render_schema_form
    form = render_schema_form({"properties": {"url": {"type": "string", "format": "lan-url", "default": "192.168.1.1"}}}, {})
    assert 'value=""' in form and 'placeholder="192.168.1.1"' in form
    assert 'value="http://192.168.1.20:8123"' in render_schema_form(
        {"properties": {"url": {"type": "string", "format": "lan-url", "default": "192.168.1.1"}}},
        {"url": "http://192.168.1.20:8123"})


async def _until(check, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def _call(path: str, method: str = "GET", headers=()) -> tuple[int, dict, bytes]:
    """One HTTP call straight into the core's /api/modules mount."""
    from mirrordash_core.features.modules.client import module_routes
    scope = {"type": "http", "method": method, "path": "/api/modules" + path, "root_path": "/api/modules",
             "query_string": b"", "headers": [(k.encode(), v.encode()) for k, v in headers],
             "client": ("192.168.1.5", 1234)}
    events = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(event):
        events.append(event)

    await module_routes(scope, receive, send)
    start = next(e for e in events if e["type"] == "http.response.start")
    headers_out = {}
    for k, v in start["headers"]:
        headers_out.setdefault(k.decode(), []).append(v.decode())
    return start["status"], headers_out, b"".join(e.get("body", b"") for e in events if e["type"] == "http.response.body")


def test_the_module_host_tells_when_its_sandbox_is_open(tmp_path, monkeypatch):
    """systemd only warns when the kernel can't filter, and a user per module holds only with the right
    modes: the host looks itself, as a module's user, for a port of the mirror, the router, a secret folder,
    a device, another module's data or the site within its reach, and for capabilities left over."""
    import socket

    from mirrordash_core.features.modules import host

    def holes():
        return [what for what, reachable in host.sandbox_checks() if reachable]

    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    # The default route through 127.0.0.1 (01 00 00 7f, little-endian), where nothing listens on 53: refused
    (tmp_path / "route").write_text("Iface\tDestination\tGateway\nwlan0\t00000000\t0100007F\n")
    (tmp_path / "gpiochip0").write_text("")
    (tmp_path / "i2c-1").write_text("")
    modules = tmp_path / "modules"
    (modules / "data" / "calendar").mkdir(parents=True)
    (modules / "site_a").mkdir()
    (modules / "site").symlink_to("site_a")
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(modules))
    try:
        monkeypatch.setattr(host, "SANDBOX_PORTS", (port,))
        monkeypatch.setattr(host, "ROUTES", str(tmp_path / "route"))
        monkeypatch.setattr(host, "SANDBOX_PATHS", {str(tmp_path): "the core's data"})
        monkeypatch.setattr(host, "SANDBOX_DEVICES", (str(tmp_path / "gpiochip*"), str(tmp_path / "i2c-*")))
        monkeypatch.setattr(host, "MODULE_DEVICES", {str(tmp_path / "i2c-1")})
        assert holes() == [f"the mirror's own port {port}", "the router (127.0.0.1)", f"the core's data ({tmp_path})",
                           str(tmp_path / "gpiochip0"), "another module's data (calendar)",
                           f"writing the module site ({modules / 'site'})"]
    finally:
        listener.close()
    monkeypatch.setattr(host, "SANDBOX_PORTS", ())
    monkeypatch.setattr(host, "ROUTES", str(tmp_path / "no-route"))
    monkeypatch.setattr(host, "SANDBOX_PATHS", {str(tmp_path / "out-of-sight"): "the core's data"})
    for path in (tmp_path / "gpiochip0", modules / "data" / "calendar", modules / "site_a"):
        path.chmod(0o500 if path.is_dir() else 0)
    (modules / "data" / "calendar").chmod(0)
    try:
        assert holes() == [] and ("capabilities (none)", False) in host.sandbox_checks()
    finally:
        (modules / "data" / "calendar").chmod(0o700)


FAKE_UV = '''#!{python}
import pathlib, sys
args = sys.argv[1:]
target = pathlib.Path(args[args.index("--target") + 1])
if args[-1] != "md_widget":
    sys.exit("No solution found when resolving: " + args[-1])
(target / "md_widget").mkdir()
(target / "md_widget" / "__init__.py").write_text("")
(target / "md_widget" / "plugin.py").write_text("class Module: pass")
(target / "md_widget-1.0.dist-info").mkdir()
(target / "md_widget-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\\nName: md-widget\\nVersion: 1.0\\n")
(target / "md_widget-1.0.dist-info" / "entry_points.txt").write_text("[mirrordash.modules]\\nmd_widget = md_widget.plugin:Module\\n")
(target / "evil.pth").write_text("import pathlib; pathlib.Path({marker!r}).write_text('ran')")
'''


def test_the_site_is_copied_without_setting_a_setgid_bit(tmp_path, monkeypatch):
    """The modules folder is setgid (2770) and its folders inherit the bit; the host's sandbox
    (RestrictSUIDSGID=) refuses to set it, which copytree's copystat does: every module change after the
    first failed with EPERM on the test mirror."""
    import os
    import shutil
    import stat

    from mirrordash_core.features.modules.host import copy_site

    src = tmp_path / "site_b"
    (src / "pkg" / "templates").mkdir(parents=True)
    (src / "pkg" / "templates" / "widget.html").write_text("<p>hi</p>")
    (src / "bin").mkdir()
    (src / "bin" / "tool").write_text("#!/bin/sh\n")
    (src / "bin" / "tool").chmod(0o750)
    (src / "pkg-link").symlink_to("pkg")
    for folder in (src, src / "pkg", src / "pkg" / "templates", src / "bin"):
        folder.chmod(0o2770)
    real_chmod = os.chmod

    def sandboxed_chmod(path, mode, *args, **kwargs):
        if mode & (stat.S_ISUID | stat.S_ISGID):
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, mode, *args, **kwargs)
    monkeypatch.setattr(os, "chmod", sandboxed_chmod)

    with pytest.raises(OSError):  # what the host did
        shutil.copytree(src, tmp_path / "copytree", symlinks=True)
    copy_site(src, tmp_path / "site_a")
    dst = tmp_path / "site_a"
    assert (dst / "pkg" / "templates" / "widget.html").read_text() == "<p>hi</p>"
    assert os.readlink(dst / "pkg-link") == "pkg" and os.access(dst / "bin" / "tool", os.X_OK)


@pytest.mark.asyncio
async def test_the_running_modules_keep_the_settings_order():
    """The screen lays an area's cards out in the order /api/active-modules lists them; the host reported
    its instances sorted, which put the clock above Home Assistant on the test mirror."""
    from mirrordash_core.features.modules.client import Connection, ModuleHost
    core = ModuleHost()
    core._placement = {"ha": {}, "namnsdag": {}, "clock": {}, "off": {}}
    for instance in ("clock", "ha", "namnsdag", "unknown"):
        conn = core._instances[instance] = Connection(MagicMock(), MagicMock(), None, instance)
        await core._handle({"t": "status", "gen": 1, "running": [instance]}, conn)
    assert core.running == ["ha", "namnsdag", "clock"]


@pytest.mark.asyncio
async def test_a_module_that_keeps_dying_is_paused_alone(monkeypatch):
    """A module that uses up the modules' memory is the one the kernel kills (oom_score_adj), and the core
    starts its process again, forever: after CRASH_LIMIT quick deaths in a row it stays off, the others run
    on, until the Dashboard's button."""
    from mirrordash_core.features.modules import client as client_module
    core = client_module.ModuleHost()
    core._placement = {"leak": {}, "calm": {}}
    core._instances["calm"] = client_module.Connection(MagicMock(), MagicMock(), None, "calm")
    starts = []

    async def sync():
        if "leak" not in core.paused:
            starts.append("leak")
            reader = asyncio.StreamReader()
            reader.feed_eof()  # it dies at once
            conn = core._instances["leak"] = client_module.Connection(reader, MagicMock(), None, "leak")
            asyncio.create_task(core._serve(conn))

    monkeypatch.setattr(core, "_sync", sync)
    monkeypatch.setattr(client_module, "RESTART_DELAY", 0)
    await sync()
    await _until(lambda: "leak" in core.paused)
    assert starts == ["leak"] * 3 and core.paused == {"leak"} and list(core._instances) == ["calm"]
    await core.resume()
    assert not core.paused and not core._crashes


@pytest.mark.asyncio
async def test_modules_are_installed_by_the_module_host_into_its_own_site(tmp_path, monkeypatch, host_here):
    """uv runs in the module host (its site process), into a copy of its site that is switched in only when the change worked
    (ARCHITECTURE #41). The core then finds the module by its files: a package's .pth never runs in it."""
    import os
    import sys

    from mirrordash_core.features.modules import client as client_module
    from mirrordash_core.features.modules import host as host_module
    from mirrordash_core.features.modules.loader import find_entry_point, package_dir

    marker = tmp_path / "pth-ran"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "uv").write_text(FAKE_UV.format(python=sys.executable, marker=str(marker)))
    (tmp_path / "bin" / "uv").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    socket_path = str(tmp_path / "host.sock")
    monkeypatch.setenv("MIRRORDASH_MODULE_SOCKET", socket_path)
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path / "modules"))
    monkeypatch.setattr(host_module.LogForward, "emit", lambda self, record: None)
    monkeypatch.setattr(client_module, "LAN_HELPER", str(tmp_path / "no-helper"))
    monkeypatch.setattr(client_module, "load_config", lambda: {"modules": {}, "globals": {}})
    monkeypatch.setattr(client_module, "manager", MagicMock(broadcast=AsyncMock()))
    core = client_module.ModuleHost()
    server = host_here(socket_path)
    await _until(lambda: (tmp_path / "host.sock").exists())
    await core.start_modules()
    site_link = tmp_path / "modules" / "site"
    try:
        await _until(lambda: core._control is not None)
        reply = await core.diagnose()  # Logs → Diagnostics: the host's own checks, as a module's user
        assert isinstance(reply["checks"], list) and reply["sandboxed"] is False and reply["gpio"] == {}
        async with core.packages(sys.executable) as site:
            assert (await site.pip("install", "md_widget"))[0] == 0
            code, err = await site.check(["md_widget"])
            assert code == 0, err
        assert os.readlink(site_link) == "site_b"
        marker.unlink()  # the check's own Python ran it: that one is the module host's

        ep = find_entry_point("md_widget")
        assert ep and package_dir(ep) == site_link / "md_widget"
        assert not marker.exists() and "md_widget" not in sys.modules

        # A change that fails is dropped: the site stays as it was
        with pytest.raises(RuntimeError):
            async with core.packages(sys.executable) as site:
                code, err = await site.pip("install", "md_missing")
                assert code != 0 and "md_missing" in err
                raise RuntimeError(err)
        assert os.readlink(site_link) == "site_b" and not any((tmp_path / "modules" / "site_a").iterdir())
        assert find_entry_point("md_widget")
    finally:
        await core.stop_modules()
        await asyncio.wait_for(server, 5)


@pytest.mark.asyncio
async def test_modules_run_in_the_module_host_and_reach_the_core_only_through_it(tmp_path, monkeypatch, host_here, caplog):
    """The core's client and a real module host over unix sockets: the module's card lands in its place,
    its routes answer under its instance without the owner's cookie and can't set one, and what its
    connection sends speaks for it alone, whatever instance it names."""
    import json as json_

    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import JSONResponse

    from mirrordash_core.features.modules import client as client_module
    from mirrordash_core.features.modules import host as host_module

    class GaragePlugin:
        def __init__(self, config):
            self.router = APIRouter()

            @self.router.post("/door/{state}")
            async def door(state: str, request: Request):
                if state not in ("open", "closed"):
                    raise HTTPException(status_code=400, detail="open or closed")
                return JSONResponse({"door": state, "cookie": request.headers.get("cookie"),
                                     "api_path": config["api_path"], "client": request.client.host},
                                    headers={"Set-Cookie": "mirrordash_session=evil", "X-Garage": "1",
                                             "Service-Worker-Allowed": "/", "Clear-Site-Data": '"cookies"'})

        async def run_loop(self, broadcast_fn):
            await broadcast_fn("garage", "<p>closed</p>")
            await asyncio.Event().wait()

    class EntryPoint:
        name = "md_garage"
        module = "md_garage.plugin"

        def load(self):
            return GaragePlugin

    socket_path = str(tmp_path / "host.sock")
    monkeypatch.setenv("MIRRORDASH_MODULE_SOCKET", socket_path)
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path / "modules"))
    monkeypatch.setattr(mirrordash_core.features.modules.loader, "module_entry_points", lambda: [EntryPoint()])
    monkeypatch.setattr(host_module.LogForward, "emit", lambda self, record: None)  # one process: no loop back
    monkeypatch.setattr(client_module, "LAN_HELPER", str(tmp_path / "no-helper"))
    config = {"modules": {"garage": {"module": "md_garage", "position": "top_left"}}, "globals": {"language": "en"},
              "admin_auth": {"hash": "secret"}}
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    manager = MagicMock()
    manager.broadcast = AsyncMock()
    monkeypatch.setattr(client_module, "manager", manager)
    monkeypatch.setattr(client_module, "module_host", client_module.ModuleHost())
    core = client_module.module_host

    server = host_here(socket_path)
    await _until(lambda: (tmp_path / "host.sock").exists())
    sent = []
    real_send = client_module.Connection.send

    async def spy(self, message):
        sent.append(message)
        await real_send(self, message)
    monkeypatch.setattr(client_module.Connection, "send", spy)
    await core.start_modules()
    try:
        await _until(lambda: core.running == ["garage"])
        start = next(m for m in sent if m["t"] == "start")
        assert "admin_auth" not in json_.dumps(start) and list(start["modules"]) == ["garage"]  # its own settings only
        await _until(lambda: manager.broadcast.await_count >= 1)
        assert manager.broadcast.await_args.args[0] == {
            "position": "top_left", "module": "garage", "carousel_group": None, "carousel_interval": 15,
            "max_width": None, "max_height": None, "z_index": None, "opacity": None, "order": 0,
            "html": "<p>closed</p>"}
        assert (tmp_path / "modules" / "data" / "garage").is_dir()

        status, headers, body = await _call("/garage/door/open", "POST", [("cookie", "mirrordash_session=owner")])
        assert status == 200
        assert json_.loads(body) == {"door": "open", "cookie": None, "api_path": "/api/modules/garage", "client": "192.168.1.5"}
        assert "set-cookie" not in headers and headers["x-garage"] == ["1"]
        assert "service-worker-allowed" not in headers and "clear-site-data" not in headers
        assert headers["content-security-policy"] == ["sandbox"]
        assert (await _call("/garage/door/ajar", "POST"))[0] == 400
        assert (await _call("/garage/elsewhere"))[0] == 404
        assert (await _call("/gone/door/open", "POST"))[0] == 404
        assert (await _call(""))[0] == 404

        garage = core._instances["garage"]
        manager.broadcast.reset_mock()
        await core._handle({"t": "html", "instance": "garage", "html": "<p>x</p>"}, core._control)
        assert not manager.broadcast.called  # the control process has no card to show
        await core._handle({"t": "html", "instance": "elsewhere", "html": "<p>x</p>"}, garage)
        assert manager.broadcast.await_args.args[0]["module"] == "garage"  # its own place, whatever it names
        with caplog.at_level(logging.INFO):
            await core._handle({"t": "log", "name": "mirrordash.core.auth", "level": 40, "text": "forged"}, garage)
        assert [r.name for r in caplog.records if r.message == "forged"] == ["mirrordash.modules.garage"]
    finally:
        await core.stop_modules()
        await asyncio.wait_for(server, 5)


FORK_PLUGIN = '''
import asyncio, os, time
from fastapi import APIRouter
class Module:
    def __init__(self, config):
        self.config = config
        self.router = APIRouter()
        self.heard = []
        bus = config["event_bus"]
        for name in ("md_fork.hello", "md_fork.private", "hardware.button", "md_other.hello"):
            bus.subscribe(name, lambda data, name=name: self.heard.append([name, data]))
        @self.router.get("/shout")
        async def shout():
            bus.publish("md_fork.hello", self.config.get("text"), public=True)
            bus.publish("md_fork.private", self.config.get("text"))
            bus.publish("hardware.button", {"press": "single"}, public=True)  # not its name to use
            bus.publish("md_other.hello", "forged", public=True)
            return {}
        @self.router.get("/heard")
        async def heard():
            return self.heard
        @self.router.get("/who")
        async def who():
            return {"pid": os.getpid(), "ppid": os.getppid(), "fds": len(os.listdir("/proc/self/fd")),
                    "oom": open("/proc/self/oom_score_adj").read().strip()}
    async def run_loop(self, broadcast):
        await broadcast("x", f"<p>{self.config.get('text')} {os.getpid()}</p>")
        if self.config.get("die"):
            os._exit(3)
        if self.config.get("stuck"):
            time.sleep(3600)  # a loop that never gives the event loop back: it can't read its connection close
        await asyncio.Event().wait()
'''


@pytest.mark.asyncio
async def test_each_instance_runs_in_a_process_of_its_own(tmp_path, monkeypatch):
    """The real module host (forkserver.py), off a mirror: each instance its own process, forked by the
    forkserver with no descriptor of another's, first for the kernel's OOM killer; one that keeps dying is
    paused alone, a changed setting restarts only its instance, and one stuck in a loop still ends when the
    core lets it go."""
    import base64
    import json
    import os

    from mirrordash_core.features.modules import client as client_module
    site = tmp_path / "modules" / "site_a"
    (site / "md_fork").mkdir(parents=True)
    (site / "md_fork" / "__init__.py").write_text("")
    (site / "md_fork" / "plugin.py").write_text(FORK_PLUGIN)
    (site / "md_fork-1.0.dist-info").mkdir()
    (site / "md_fork-1.0.dist-info" / "METADATA").write_text("Metadata-Version: 2.1\nName: md-fork\nVersion: 1.0\n")
    (site / "md_fork-1.0.dist-info" / "entry_points.txt").write_text("[mirrordash.modules]\nmd_fork = md_fork.plugin:Module\n")
    (tmp_path / "modules" / "site").symlink_to("site_a")
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path / "modules"))
    monkeypatch.delenv("MIRRORDASH_MODULE_SOCKET", raising=False)  # the core starts the host as its child
    config = {"modules": {"a": {"module": "md_fork", "text": "A"}, "b": {"module": "md_fork", "text": "B"},
                          "dies": {"module": "md_fork", "die": True}, "stuck": {"module": "md_fork", "stuck": True}},
              "globals": {}}
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    monkeypatch.setattr(client_module, "manager", MagicMock(broadcast=AsyncMock(), clear_cache=MagicMock()))
    monkeypatch.setattr(client_module, "RESTART_DELAY", 0.1)
    core = client_module.ModuleHost()

    async def who(instance):
        reply = await core.call_route({"instance": instance, "method": "GET", "path": "/who"})
        return json.loads(base64.b64decode(reply["body"]))

    async def who_heard(instance):
        reply = await core.call_route({"instance": instance, "method": "GET", "path": "/heard"})
        return json.loads(base64.b64decode(reply["body"]))

    await core.start_modules()
    try:
        await _until(lambda: core.running == ["a", "b", "stuck"] and "dies" in core.paused
                     and (core._instances.get("stuck") and core._instances["stuck"].html), 30)
        a, b = await who("a"), await who("b")
        assert a["pid"] != b["pid"] and a["ppid"] == b["ppid"] != os.getpid()
        assert a["oom"] == "500" and a["fds"] < 12  # stdio, its two sockets, its event loop's

        # A public event reaches every instance through the core, the sender's too; a private one stays home
        await core.call_route({"instance": "a", "method": "GET", "path": "/shout"})
        for _ in range(100):
            if await who_heard("b"):
                break
            await asyncio.sleep(0.05)
        assert await who_heard("b") == [["md_fork.hello", "A"]]
        assert sorted(await who_heard("a")) == [["md_fork.hello", "A"], ["md_fork.private", "A"]]
        first_a = core._instances["a"].html

        config["modules"]["b"]["text"] = "B2"
        config["modules"]["a"]["position"] = "top_left"  # a move, not a restart
        stuck_pid = core._instances["stuck"].html.split()[-1].removesuffix("</p>")
        del config["modules"]["stuck"]
        await core.reload_modules()
        await _until(lambda: (conn := core._instances.get("b")) is not None and "B2" in (conn.html or ""), 30)
        assert core._instances["a"].html == first_a and (await who("a"))["pid"] == a["pid"]
        assert set(core._instances) == {"a", "b"}

        def gone(pid):
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return True
            return False
        await _until(lambda: gone(stuck_pid), 15)  # forkserver.STOP_GRACE after its connection closed
    finally:
        await core.stop_modules()
