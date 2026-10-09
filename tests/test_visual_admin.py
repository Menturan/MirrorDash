import os
import time
import socket
import threading
import uvicorn
import pytest
import contextlib
from unittest.mock import patch, AsyncMock

# The admin pages run in Chromium. The mirror's own pages (kiosk, modules) run in WebKit, the engine
# of the mirror's Cog browser: marked only_browser("webkit"). Both come from addopts in pyproject.toml.
pytestmark = pytest.mark.only_browser("chromium")

# Setup mock config
mock_salt = "0123456789abcdef"
from mirrordash_core.api.admin import hash_password
mock_hash = hash_password("secret", mock_salt)

MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    },
    "globals": {
        "language": "en",
        "timezone": "Europe/Stockholm",
        "time_format": "24h",
        "temperature_unit": "C",
        "distance_unit": "km",
        "latitude": 59.3293,
        "longitude": 18.0686
    },
    "system": {
        "rotation": "normal",
        "resolution": "auto",
        "brightness": 100,
        "volume": 80,
        "ssh": False
    },
    "modules": {
        "mirrordash-clock": {
            "enabled": True,
            "position": "top_right",
            "format": "24h"
        }
    }
}

MOCK_BACKUPS = {
    "backups": [
        {
            "filename": "backup_2026-06-29.mirror",
            "created_at": "2026-06-29T21:30:00",
            "size_bytes": 102400,
            "encrypted": True
        }
    ]
}

# Dynamic patching helper to mock functions in all namespaces where they are imported
@contextlib.contextmanager
def patch_all_system():
    async def fake_restart():
        # A real restart starts a new process with a new boot id; the admin UI waits for that.
        import uuid
        from mirrordash_core.api import admin_shared
        admin_shared.BOOT_ID = uuid.uuid4().hex
        return True
    mock_restart = AsyncMock(side_effect=fake_restart)
    mock_get_ssh_val = AsyncMock(return_value=False) # Start with SSH disabled to test toggle
    mock_set_ssh_val = AsyncMock(return_value=True)
    
    # Mock subprocess for chpasswd and openssl
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"mocked_hash\n", b""))
    
    modules = [
        "mirrordash_core.api.admin_system",
        "mirrordash_core.api.admin_system_panels",
        "mirrordash_core.api.admin_auth",
        "mirrordash_core.api.admin_config",
        "mirrordash_core.api.admin_modules_panels",
        "mirrordash_core.api.admin_shared",
        "mirrordash_core.config",
        "mirrordash_core.system",
        "mirrordash_core.system.os",
        "mirrordash_core.system.network"
    ]
    
    stack = contextlib.ExitStack()
    with stack:
        # Patch system and utility imports in every module namespace
        for m in modules:
            for func_name, mock_obj in [
                ("load_config", MOCK_CONFIG),
                ("run_restart", mock_restart),
                ("get_ssh_status", mock_get_ssh_val),
                ("set_ssh_status", mock_set_ssh_val),
            ]:
                try:
                    if func_name == "load_config":
                        stack.enter_context(patch(f"{m}.{func_name}", return_value=mock_obj))
                    else:
                        stack.enter_context(patch(f"{m}.{func_name}", mock_obj))
                except AttributeError:
                    # Ignore if the module doesn't import or define this function
                    pass
        
        # Intercept subprocesses in admin_system.py
        stack.enter_context(patch("mirrordash_core.api.admin_system.asyncio.create_subprocess_exec", return_value=mock_proc))
        
        # Specific service level mocks
        stack.enter_context(patch("mirrordash_core.display_power.display_power_manager.start", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.display_power.display_power_manager.stop", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.module_loader.module_loader.start_modules", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.module_loader.module_loader.stop_modules", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.api.admin_logs.get_logs", return_value={"logs": "MOCK LOG LINE 1\nMOCK LOG LINE 2"}))
        stack.enter_context(patch("mirrordash_core.api.backup.list_backups", return_value=MOCK_BACKUPS))
        stack.enter_context(patch("mirrordash_core.api.backup.create_backup", return_value={"filename": "backup_2026-06-29.mirror", "status": "success"}))
        
        yield

# Import app after helper definitions to maintain logical ordering
from mirrordash_core.app import app

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

@pytest.fixture(scope="module")
def server_url():
    with patch_all_system():
        port = get_free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        
        # Give server time to bind and start
        time.sleep(1.5)
        yield f"http://127.0.0.1:{port}"

@pytest.fixture(autouse=True)
def mock_backend_functions():
    with patch_all_system(), patch("mirrordash_core.config.save_config") as mock_save:
        yield mock_save

def navigate_authenticated(page, server_url):
    # Navigate to public health page first to initialize origin localStorage securely
    page.goto(f"{server_url}/health")
    page.evaluate("() => localStorage.setItem('mirrordash_api_key', 'secret')")
    # Load the admin page directly authenticated
    page.goto(f"{server_url}/admin")

def test_admin_dashboard_navigation_and_drawer(page, server_url):
    navigate_authenticated(page, server_url)

    # 1. Verify we land on the default dashboard page
    page.wait_for_selector("h1")
    assert page.locator("#page-tab-dashboard").is_visible()
    
    # 2. Click through to the Modules tab
    page.click("#page-tab-modules")
    page.wait_for_selector("#installed-modules-container")
    
    # Ensure the clock module is listed
    clock_card = page.locator("#module-card-mirrordash_clock")
    assert clock_card.is_visible()

    # 3. Test expanding the configuration drawer ("Add to Mirror" / "Configure" button)
    config_btn = page.locator("#config-btn-mirrordash_clock")
    assert config_btn.is_visible()
    
    # Click to expand
    config_btn.click()
    
    # Wait for the config overlay inside the drawer to load and expand
    overlay = page.locator("#configOverlay")
    page.wait_for_selector("#configOverlay.open", state="visible")
    assert overlay.is_visible()

    # Click close button to collapse
    page.click(".sheet-close")
    page.wait_for_selector("#configOverlay.open", state="hidden")
    assert "open" not in overlay.get_attribute("class")

def test_admin_dashboard_restart_overlay(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Restart MirrorDash lives in the Power tab
    page.click("#page-tab-power")
    page.wait_for_selector("#restart-btn")
    restart_btn = page.locator("#restart-btn")
    assert restart_btn.is_visible()
    restart_btn.click()

    # Wait for the custom confirm overlay to appear
    page.wait_for_selector("#confirm-overlay", state="visible")
    # Click the Confirm button on the custom confirm overlay
    page.click("#confirm-ok-btn")

    # The glassmorphic restart overlay should immediately show up
    overlay = page.locator("#restart-overlay")
    page.wait_for_selector("#restart-overlay", state="visible")
    assert overlay.is_visible()
    assert page.locator("#restart-overlay-title").text_content() == "Restarting MirrorDash"

    # Wait for the overlay to naturally disappear once /health responds successfully
    page.wait_for_selector("#restart-overlay", state="hidden")
    assert not overlay.is_visible()

def test_admin_configuration_panel(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Navigate to Configuration panel
    page.click("#page-tab-config")
    page.wait_for_selector("#visual-form-container")

    # Verify Visual Editor is visible
    assert page.locator("#visual-form-container").is_visible()

    # Settings save on change; there is no Save button any more
    assert page.locator("#page-panel-config button[type=submit]").count() == 0
    page.locator("#visual-form-container select").first.dispatch_event("change")
    page.wait_for_selector("#global-status", state="visible")
    assert "Saved." in page.locator("#global-status").text_content()

def test_admin_logs_panel(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Click Logs tab
    page.click("#page-tab-logs")
    page.wait_for_selector("#logs-viewer")

    # Verify mock log lines are loaded in the log viewer
    log_content = page.locator("#logs-viewer").text_content()
    assert "MOCK LOG LINE 1" in log_content
    assert "MOCK LOG LINE 2" in log_content

    # Select module logs filter
    page.select_option("#log-type-select", "modules")
    
    # Verify module selection dropdown container becomes visible
    page.wait_for_selector("#log-module-select-container", state="visible")
    assert page.locator("#log-module-select-container").is_visible()

    # Click Refresh button
    page.click("#refresh-logs-btn")
    # Verify logs viewer container updates and content is still present
    page.wait_for_selector("#logs-viewer")
    assert "MOCK LOG LINE 1" in page.locator("#logs-viewer").text_content()

def test_admin_backup_panel(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Click Backup & Restore tab
    page.click("#page-tab-backup")
    page.wait_for_selector("#backups-list")

    # Verify mock backup is rendered in list
    assert page.locator("text=backup_2026-06-29.mirror").is_visible()

    # On a phone the row's buttons must stay on screen (they used to scroll off to the right)
    page.set_viewport_size({"width": 390, "height": 844})
    box = page.locator("#backups-list button:has-text('Restore')").bounding_box()
    assert box["x"] + box["width"] <= 390
    page.set_viewport_size({"width": 1280, "height": 800})

    # Toggle Password Protection
    assert not page.locator("#backup-password-container").is_visible()
    page.evaluate("document.getElementById('backup-encrypt-toggle').checked = true; document.getElementById('backup-encrypt-toggle').dispatchEvent(new Event('change'))")
    page.wait_for_selector("#backup-password-container", state="visible")
    assert page.locator("#backup-password-container").is_visible()

    # Fill password and generate backup
    page.fill("#backup-password", "supersecretpwd")
    page.click("#create-backup-btn")

    # Verify alert/success messaging gets displayed in global status
    page.wait_for_selector("#global-status", state="visible")
    assert "created" in page.locator("#global-status").text_content()

def test_admin_system_panel(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Click System Settings tab
    page.click("#page-tab-system")
    page.wait_for_selector("#system-settings-form")

    # Verify slider and selection elements are loaded
    assert page.locator("#sys-brightness").is_visible()
    assert page.locator("#sys-volume").is_visible()
    assert page.locator("#sys-rotation").is_visible()

    # Drag or update the brightness slider value
    page.evaluate("document.getElementById('sys-brightness').value = 85; document.getElementById('sys-brightness').dispatchEvent(new Event('input'))")
    assert page.locator("#sys-brightness-val").text_content() == "85%"

    # Settings apply on change: there is no Apply button any more
    assert page.locator("#save-system-btn").count() == 0
    page.select_option("#sys-rotation", "right")
    page.wait_for_selector("#global-status", state="visible")
    assert "Saved." in page.locator("#global-status").text_content()

    # Turning SSH on waits for the password instead of saving immediately
    page.evaluate("document.getElementById('global-status').hidden = true")
    assert not page.locator("#sys-ssh-password-group").is_visible()
    saves = []
    page.on("request", lambda r: saves.append(r.post_data) if r.url.endswith("/admin/panels/system/save") else None)
    page.evaluate("document.getElementById('sys-ssh').checked = true; document.getElementById('sys-ssh').dispatchEvent(new Event('change', {bubbles: true}))")
    page.wait_for_selector("#sys-ssh-password-group", state="visible")
    page.wait_for_timeout(300)
    assert saves == []  # nothing saved yet: SSH waits for its password
    # Type the password and press the button, as a user would (no Enter)
    page.fill("#sys-ssh-password", "pi_password_123")
    page.click("#sys-ssh-save")
    page.wait_for_selector("#global-status", state="visible")
    assert "Saved." in page.locator("#global-status").text_content()
    page.wait_for_timeout(300)
    assert len(saves) == 1 and "pi_password=pi_password_123" in saves[0]  # saved once, not twice


def test_module_upgrade_shows_progress(page, server_url):
    """Upgrade on a module card (the button the update check adds) shows the progress overlay."""
    import json
    from unittest.mock import MagicMock
    dist = MagicMock(version="0.1.0")
    dist.name = "mirrordash-clock"
    dist.read_text.return_value = json.dumps({"url": "https://github.com/Menturan/mirrordash-clock.git",
                                              "vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "v1.0.0"}})
    ep = MagicMock(dist=dist)
    with patch("mirrordash_core.api.admin_modules_panels.find_entry_point", return_value=ep), \
         patch("mirrordash_core.api.admin_modules_panels.fetch_json_cached", new_callable=AsyncMock,
               return_value={"tag_name": "v1.0.1", "body": "Fixes"}), \
         patch("mirrordash_core.api.admin_modules_panels.update_module", new_callable=AsyncMock):
        navigate_authenticated(page, server_url)
        page.wait_for_selector("h1")
        # The card's placeholders, filled by the update check exactly as on the Modules tab
        page.evaluate("""() => {
            document.body.insertAdjacentHTML('beforeend',
                '<div id="update-badge-clock"></div><div id="update-actions-clock"></div>');
            htmx.ajax('GET', '/admin/panels/modules/check-update/clock', {target: '#update-badge-clock', swap: 'none'});
        }""")
        upgrade = page.locator("#update-actions-clock button", has_text="Upgrade")
        upgrade.wait_for(state="visible")
        upgrade.click()
        page.click("#confirm-ok-btn")
        page.wait_for_selector("#restart-overlay", state="visible", timeout=5000)
        assert "Upgrading" in page.locator("#restart-overlay-title").text_content()
        assert "mirrordash-clock (v1.0.1)" in page.locator("#restart-overlay-message").text_content()
        # "visible" ignores opacity: the overlay was there but fully transparent, so check what the eye sees
        page.wait_for_function("getComputedStyle(document.getElementById('restart-overlay')).opacity === '1'", timeout=2000)
        if os.environ.get("UPGRADE_SHOT"):
            page.screenshot(path=os.environ["UPGRADE_SHOT"])


def test_add_to_mirror(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Navigate to Modules tab
    page.click("#page-tab-modules")
    page.wait_for_selector("#installed-modules-container")

    # Locate Add to Mirror button for mirrordash_calendar
    config_btn = page.locator("#config-btn-mirrordash_calendar")
    assert config_btn.is_visible()
    assert "Add to Mirror" in config_btn.text_content()

    # Register console error and network response/request handlers to check for frontend issues
    console_errors = []
    page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}"))

    # Click Add to Mirror
    config_btn.click()

    # Verify drawer opens
    overlay = page.locator("#configOverlay")
    page.wait_for_selector("#configOverlay.open", state="visible")
    assert overlay.is_visible()

    # Wait for the config fields inside the drawer to load and contain Configuration Parameters
    fields = page.locator("#config-fields-global")
    page.wait_for_selector("#config-fields-global form", state="visible")
    inner_html = fields.inner_html()
    assert "Standard Settings" in inner_html

    assert len([err for err in console_errors if "error" in err]) == 0



def test_dashboard_analytics_refresh_only_on_dashboard(page, server_url):
    page.clock.install()
    navigate_authenticated(page, server_url)
    page.wait_for_selector("#system-analytics")
    polls = []
    page.on("request", lambda r: r.url.endswith("/admin/panels/dashboard") and polls.append(r))

    page.clock.run_for(11000)
    page.wait_for_timeout(500)
    assert len(polls) == 1  # one refresh after 10 s
    page.clock.run_for(10000)
    page.wait_for_timeout(500)
    assert len(polls) == 2  # the swapped-in section keeps polling

    page.click("#page-tab-modules")
    page.wait_for_selector("#installed-modules-container")
    page.clock.run_for(30000)
    page.wait_for_timeout(500)
    assert len(polls) == 2  # another tab: no more refreshes


def test_wifi_setup_flow(page, server_url):
    """First boot on a phone: pick a network, type (and reveal) the password, see where to go next."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.route("**/admin/auth/status", lambda r: r.fulfill(json={"setup_required": True}))
    page.route("**/api/wifi/scan", lambda r: r.fulfill(json={"networks": ["Hemma-5G", "Grannen <b>x</b>"]}))
    page.route("**/api/wifi/setup", lambda r: r.fulfill(json={"status": "success", "message": "Connected."}))
    page.goto(f"{server_url}/wifi-setup")

    page.wait_for_selector("#network-list button")
    assert page.locator("#network-list").text_content().count("Grannen <b>x</b>") == 1  # names are text, never markup
    page.click("#network-list button:has-text('Hemma-5G')")
    assert page.locator("#chosen-ssid").text_content() == "Hemma-5G"

    page.fill("#password", "hemligt")
    page.click("[data-reveal=password]")
    assert page.locator("#password").get_attribute("type") == "text"
    page.click("[data-reveal=password]")
    assert page.locator("#password").get_attribute("type") == "password"

    page.click("#connect-btn")
    page.wait_for_selector("#step-done:not([hidden])")
    assert page.locator("#done-ssid").text_content() == "Hemma-5G"
    assert page.locator(".address").get_attribute("href") == "http://mirrordash.local/admin"
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


@pytest.mark.only_browser("webkit")
def test_module_script_gets_its_own_shadow_root(page):
    """A module's inline script receives its shadow root as `root`; it can't rely on document.currentScript."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json=[])
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        route.fulfill(body=file.read_bytes(), content_type="text/html" if path.endswith(".html") else "text/javascript")

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")

    script = ("<p class='out'></p><script>root.querySelector('.out').textContent = "
              "(document.currentScript ? 'current' : 'root') + ':' + (root._renders = (root._renders || 0) + 1);</script>")
    for module in ("clock-a", "clock-b", "clock-a"):
        msg = json.dumps({"module": module, "position": "top_left", "html": script})
        page.evaluate("msg => window.__ws.onmessage({ data: msg })", msg)

    read = "m => document.querySelector(`[data-module='${m}']`).shadowRoot.querySelector('.out').textContent"
    assert page.evaluate(read, "clock-a") == "root:2"  # same root across re-renders, so state like timers persists
    assert page.evaluate(read, "clock-b") == "root:1"  # and it's per instance


@pytest.mark.only_browser("webkit")
def test_mirror_page_reloads_only_for_a_new_version(page):
    """After an update the page's own code is new too: it reloads when the server's version changed,
    not when MirrorDash merely restarted."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": []})
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        types = {".html": "text/html", ".css": "text/css"}
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    hello = lambda version: page.evaluate("msg => window.__ws.onmessage({ data: msg })",
                                          json.dumps({"type": "hello", "version": version}))
    page.evaluate("window.__loaded = true")

    hello("1.0.0")  # the version the page was loaded with
    hello("1.0.0")  # MirrorDash restarted, same version: nothing to do
    assert page.evaluate("window.__loaded") is True

    with page.expect_navigation():
        hello("1.1.0")  # updated: the page loads its new code
    assert page.evaluate("window.__loaded === undefined")


@pytest.mark.only_browser("webkit")
def test_a_module_never_pulses_after_its_loading_placeholder(page):
    """The order of the first start after setup: the placeholder (pulsing) comes first, the module is
    then drawn into the same element. Nothing in the module may keep animating."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": [{"name": "clock", "position": "top_left", "title": "Clock"}]})
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        types = {".html": "text/html", ".css": "text/css"}
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_selector("[data-module='clock'].module-loading-placeholder")
    page.wait_for_function("window.__ws")
    module = page.locator("[data-module='clock']")
    assert module.evaluate("e => e.getAnimations({ subtree: true }).length") > 0  # loading: it pulses

    msg = json.dumps({"module": "clock", "position": "top_left", "html": "<p class='time'>12:00</p>"})
    page.evaluate("msg => window.__ws.onmessage({ data: msg })", msg)
    assert module.evaluate("e => e.shadowRoot.querySelector('.time').textContent") == "12:00"
    assert module.evaluate("e => e.querySelector('.module-loading-content')") is None
    assert module.evaluate("e => e.getAnimations().filter(a => a.playState === 'running' && a.effect.getComputedTiming().iterations === Infinity).length") == 0


@pytest.mark.only_browser("webkit")
def test_loading_page_shows_progress_then_error_then_recovers(page):
    """The kiosk's first page: something always moves while waiting, and it says so if the app never starts."""
    healthy = {"up": False}
    page.route("http://localhost:8000/health", lambda r: r.fulfill(json={"status": "ok"}) if healthy["up"] else r.abort())
    page.route("http://localhost:8000/", lambda r: r.fulfill(body="<p id=mirror>mirror</p>", content_type="text/html"))
    page.clock.install()
    page.goto(f"file://{os.path.abspath('mirrordash_core/static/loading.html')}")

    def advance(ms):
        # Each health check schedules the next one only after its fetch fails (real time), so
        # move the fake clock in small steps and let the page catch up in between.
        for _ in range(ms // 500):
            page.clock.run_for(500)
            page.wait_for_timeout(5)

    page.wait_for_timeout(1600)  # CSS animations run on real time, not the fake clock
    assert page.locator(".progress").evaluate("e => getComputedStyle(e).opacity") == "1"  # the gliding line
    assert page.locator("#message").text_content() == ""

    advance(46_000)
    assert page.locator("#message").text_content() == "Still starting…"

    advance(4 * 60_000)
    assert "MirrorDash didn’t start." in page.locator("#message").text_content()
    assert page.locator(".progress").is_hidden()  # an error doesn't pretend to be loading

    healthy["up"] = True  # it came up after all: carry on to the mirror
    advance(2000)
    page.wait_for_selector("#mirror")


@pytest.mark.only_browser("webkit")
def test_mirror_says_when_the_app_stops_responding(page, server_url):
    page.goto(f"{server_url}/")
    page.wait_for_selector("#ws-status")
    page.evaluate("lostAt = Date.now() - 3 * 60 * 1000; setStatus('disconnected')")
    assert page.locator("#ws-status .ws-status__label").text_content() == "MirrorDash isn’t responding"
    assert "ws-status--failed" in page.locator("#ws-status").get_attribute("class")


def test_power_tab_has_a_reload_screen_button(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")
    page.click("#page-tab-power")
    page.wait_for_selector("#screen-reload-btn")
    with page.expect_response("**/admin/panels/system/reload-screen") as response:
        page.click("#screen-reload-btn")
    assert response.value.ok


def test_admin_shows_a_loading_line_while_a_tab_loads(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    def slow(route):
        time.sleep(1.0)  # a Pi 3 can take seconds to answer
        route.continue_()
    page.route("**/admin/panels/logs", slow)
    page.click("#page-tab-logs")
    page.wait_for_selector("#page-loading:not([hidden])")
    page.wait_for_selector("#logs-viewer")
    page.wait_for_selector("#page-loading[hidden]", state="attached")


def test_status_message_floats_over_the_page(page, server_url):
    for width in (1280, 390):
        page.set_viewport_size({"width": width, "height": 844})
        navigate_authenticated(page, server_url)
        page.wait_for_selector("h1")
        top = lambda: page.evaluate("document.getElementById('tab-content').getBoundingClientRect().top")
        before = top()
        page.evaluate("showGlobal('Saved.', 'success')")
        page.wait_for_selector("#global-status", state="visible")
        assert top() == before  # the page under it doesn't move
        assert page.evaluate("getComputedStyle(document.getElementById('global-status')).position") == "fixed"


def test_admin_says_reconnecting_before_unreachable(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")
    back_at = time.time() + 3

    def health(route):
        if time.time() < back_at:
            return route.abort()
        route.continue_()
    page.route("**/health", health)
    page.route("**/admin/panels/logs", lambda route: route.abort())  # the phone's Wi-Fi is still asleep
    page.click("#page-tab-logs")
    page.wait_for_selector("#global-status", state="visible")
    assert page.locator("#global-status").text_content() == "Reconnecting to the mirror…"
    page.wait_for_selector("#global-status", state="hidden", timeout=10000)
