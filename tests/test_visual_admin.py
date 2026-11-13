import contextlib
import importlib.metadata
import os
import socket
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
import uvicorn

# The admin pages run in Chromium. The mirror's own pages (kiosk, modules) run in WebKit, the engine
# of the mirror's screen (WPE WebKit): marked only_browser("webkit"). Both come from addopts in pyproject.toml.
pytestmark = pytest.mark.only_browser("chromium")

# Setup mock config
mock_salt = "0123456789abcdef"
from mirrordash_core.admin import hash_password  # noqa: E402 (after pytestmark, which reads first)

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
        },
        {
            "filename": "mirrordash_autobackup_20261108_033012.mirror",
            "created_at": "2026-11-08T03:30:12",
            "size_bytes": 422195,
            "encrypted": False,
            "automatic": True
        }
    ]
}

# Dynamic patching helper to mock functions in all namespaces where they are imported
class FakeClock:
    """Stand-ins for the clock and calendar modules: the admin page is tested with modules the test
    brings itself, not whatever happens to be installed in the developer's venv."""
    config_schema = {"title": "Clock", "properties": {}}


class FakeCalendar:
    config_schema = {"title": "Calendar", "properties": {}}


FAKE_MODULES = importlib.metadata.EntryPoints([
    importlib.metadata.EntryPoint("mirrordash_clock", f"{__name__}:FakeClock", "mirrordash.modules"),
    importlib.metadata.EntryPoint("mirrordash_calendar", f"{__name__}:FakeCalendar", "mirrordash.modules"),
])


@contextlib.contextmanager
def patch_all_system():
    async def fake_restart():
        # A real restart starts a new process with a new boot id; the admin UI waits for that.
        import uuid

        from mirrordash_core import admin as admin_shared
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
        "mirrordash_core.admin",
        "mirrordash_core.config",
        "mirrordash_core.host",
        "mirrordash_core.features.settings.system_settings",
        "mirrordash_core.features.auth.routes",
        "mirrordash_core.features.hardware.routes",
        "mirrordash_core.features.modules.instances",
        "mirrordash_core.features.modules.routes",
        "mirrordash_core.features.modules.service",
        "mirrordash_core.features.power.routes",
        "mirrordash_core.features.settings.routes",
        "mirrordash_core.features.modules.schema",
        "mirrordash_core.features.settings.ssh",
        "mirrordash_core.features.updates.routes",
        "mirrordash_core.features.updates.service",
        "mirrordash_core.features.wifi.network",
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
        
        # The installed modules are the two above (clock on the mirror, calendar not yet). module_files is
        # where find_entry_point looks (the settings drawer); the others imported the name before the patch.
        for m in ("module_files", "loader", "service"):
            stack.enter_context(patch(f"mirrordash_core.features.modules.{m}.module_entry_points", lambda: list(FAKE_MODULES)))
        stack.enter_context(patch("mirrordash_core.features.modules.installed.module_entry_points", lambda: list(FAKE_MODULES)))

        # Intercept subprocesses in admin_system.py
        stack.enter_context(patch("mirrordash_core.features.updates.service.asyncio.create_subprocess_exec", return_value=mock_proc))
        
        # Specific service level mocks
        stack.enter_context(patch("mirrordash_core.features.power.display_power.display_power_manager.start", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.features.power.display_power.display_power_manager.stop", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.features.modules.client.module_host.start_modules", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.features.modules.client.module_host.stop_modules", new_callable=AsyncMock))
        stack.enter_context(patch("mirrordash_core.features.logs.routes.get_logs", return_value={"logs": "MOCK LOG LINE 1\nMOCK LOG LINE 2"}))
        stack.enter_context(patch("mirrordash_core.features.backup.routes.list_backups", return_value=MOCK_BACKUPS))
        stack.enter_context(patch("mirrordash_core.features.backup.routes.create_backup", return_value={"filename": "backup_2026-06-29.mirror", "status": "success"}))
        
        yield

# Import app after helper definitions to maintain logical ordering
from mirrordash_core.app import app  # noqa: E402


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
    # Log in as the page does: the answer sets the session cookie, which page.request shares with the page
    assert page.request.post(f"{server_url}/admin/auth/login", data={"password": "secret"}).ok
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

    # Wide, with the preview beside the settings; on a phone the preview sits above them (ARCHITECTURE #43)
    page.wait_for_selector("#config-fields-global .module-config-form")
    page.wait_for_function("document.getElementById('modulePreview').srcdoc !== ''")
    preview = page.locator("#modulePreview")
    assert preview.get_attribute("sandbox") == "allow-scripts"
    assert page.locator("#configSheet").bounding_box()["width"] > 900
    assert preview.bounding_box()["x"] > page.locator("#config-fields-global").bounding_box()["x"] + 300
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(400)
    assert preview.bounding_box()["y"] < page.locator("#config-fields-global").bounding_box()["y"]
    page.set_viewport_size({"width": 1280, "height": 720})

    # The schedule: its own collapsible section after the other standard settings, every day ticked
    schedule = page.locator(".standard-settings-accordion", has_text="Schedule")
    schedule.locator("summary").click()
    assert schedule.locator("input[type=checkbox]:checked").count() == 7
    assert schedule.locator("input[type=time]").count() == 2
    if os.environ.get("SCHEDULE_SHOT"):
        schedule.locator("input[type=checkbox][value=sat]").uncheck()
        schedule.locator("input[type=checkbox][value=sun]").uncheck()
        schedule.locator("input[type=time]").first.fill("06:00")
        schedule.locator("input[type=time]").last.fill("09:00")
        page.locator("#config-fields-global").screenshot(path=os.environ["SCHEDULE_SHOT"])

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
    assert page.locator("#visual-form-container button[type=submit]").count() == 0
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
    # An automatic backup says so, and can be restored but not downloaded (it holds the passwords unencrypted)
    automatic = page.locator("#backups-list li", has_text="mirrordash_autobackup_")
    assert automatic.locator("text=Automatic").is_visible()
    assert automatic.locator("text=Kept on the mirror only").is_visible()
    assert automatic.locator("a[download]").count() == 0 and automatic.locator("button:has-text('Restore')").count() == 1
    if os.environ.get("BACKUP_SHOT"):
        page.locator("#backups-list").screenshot(path=os.environ["BACKUP_SHOT"])

    # On a phone the row's buttons must stay on screen (they used to scroll off to the right)
    page.set_viewport_size({"width": 390, "height": 844})
    box = page.locator("#backups-list button:has-text('Restore')").first.bounding_box()
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

    assert page.locator("#sys-ssh").count() == 0  # SSH lives in Settings › Developer


def test_turning_ssh_on_waits_for_the_password_and_says_so(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")
    page.click("#page-tab-config")
    page.wait_for_selector("#ssh-form")

    # Turning SSH on waits for the password instead of saving immediately
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
    assert "SSH is on" in page.locator("#global-status").text_content()
    page.wait_for_timeout(300)
    assert len(saves) == 1 and "pi_password=pi_password_123" in saves[0]  # saved once, not twice
    assert not page.locator("#sys-ssh-password-group").is_visible()  # done: the field goes away


def test_module_upgrade_shows_progress(page, server_url):
    """Upgrade on a module card (the button the update check adds) shows the progress overlay."""
    import json
    from unittest.mock import MagicMock
    dist = MagicMock(version="0.1.0")
    dist.name = "mirrordash-clock"
    dist.read_text.return_value = json.dumps({"url": "https://github.com/Menturan/mirrordash-clock.git",
                                              "vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "v1.0.0"}})
    ep = MagicMock(dist=dist)
    with patch("mirrordash_core.features.modules.service.find_entry_point", return_value=ep), \
         patch("mirrordash_core.features.modules.service.fetch_json_cached", new_callable=AsyncMock,
               return_value={"tag_name": "v1.0.1", "body": "Fixes"}), \
         patch("mirrordash_core.features.modules.routes.update_modules", new_callable=AsyncMock):
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


def test_update_check_fills_the_modules_and_when(page, server_url):
    """Settings → Updates → Check for Updates: the modules section lists what's new, and the card says when."""
    news = {"module_name": "mirrordash_news", "package_name": "mirrordash-news", "current_version": "1.2.0",
            "latest_version": "1.2.1", "target": "mirrordash-news", "notes": True}
    with patch("mirrordash_core.features.updates.routes.module_updates", new_callable=AsyncMock,
               return_value=([news], time.time())), \
         patch("mirrordash_core.features.updates.routes.check_core_update", new_callable=AsyncMock,
               return_value={"current_version": "0.9.0", "latest_version": "0.9.0", "update_available": False}):
        navigate_authenticated(page, server_url)
        page.click("#page-tab-config")
        page.click("#core-check-btn")
        page.locator("#modules-update-result", has_text="mirrordash-news: v1.2.0 → v1.2.1").wait_for()
        assert page.locator("#modules-update-result button", has_text="Update All Modules").is_visible()
        assert page.locator("#update-checked-at").text_content().startswith("Last checked ")
        if os.environ.get("UPDATES_SHOT"):
            page.locator("#core-check-btn").locator("xpath=ancestor::section").screenshot(path=os.environ["UPDATES_SHOT"])


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



def test_save_keeps_the_settings_open_under_the_instances_name(page, server_url):
    navigate_authenticated(page, server_url)
    page.click("#page-tab-modules")
    page.click("#config-btn-mirrordash_clock")
    heading = page.locator("#config-fields-global .module-config-instance")
    heading.wait_for()
    assert heading.text_content() == "Clock"
    page.evaluate("document.querySelector('#instances-list-global .instance-item').dataset.before = 'save'")

    with page.expect_response("**/save?**"):
        page.click("#config-fields-global button[type=submit]")
    # The instances are drawn again from the refreshed modules list, and the settings stay open
    page.wait_for_function("!document.querySelector('#instances-list-global [data-before]')")
    assert "open" in page.locator("#configOverlay").get_attribute("class")
    assert heading.text_content() == "Clock"
    assert page.locator("#instances-list-global .instance-item").count() == 1


def test_an_icon_setting_suggests_lucide_names_and_draws_the_one_typed(page, server_url):
    from mirrordash_core.forms import render_schema_form
    # As Home Assistant's: an icon in each item of a list (its entities)
    entities = {"type": "array", "items": {"type": "object", "properties": {"custom_icon": {"type": "string", "format": "icon"}}}}
    form = render_schema_form({"properties": {"entities": entities}}, {"entities": [{"custom_icon": "house"}]}, "modules[ha]")
    navigate_authenticated(page, server_url)
    page.wait_for_function("window.lucide")
    page.evaluate("""html => { const box = document.createElement('div'); box.id = 'icon-test'; box.innerHTML = html;
                               document.body.append(box); setUpIconInputs(box); }""", form)
    preview = page.locator("#icon-test .icon-preview")
    assert preview.count() == 1
    assert preview.locator("svg").count() == 1  # the saved one, drawn at once
    assert page.locator("#lucide-icons option").count() > 1000
    assert page.locator("#lucide-icons option[value='thermometer-sun']").count() == 1
    page.click("#icon-test summary")  # the item opens, as when tapped
    page.fill("#icon-test [data-icon-input]", "no-such-icon")
    assert "unknown" in preview.get_attribute("class") and preview.locator("svg").count() == 0
    page.fill("#icon-test [data-icon-input]", "thermometer-sun")
    assert "unknown" not in preview.get_attribute("class") and preview.locator("svg").count() == 1


@pytest.mark.only_browser("webkit")  # as a phone's Safari: createImageBitmap's orientation, toBlob
def test_a_files_setting_shrinks_a_photo_to_the_screen_before_sending_it(page, server_url):
    """The admin page shrinks an image on the phone (a JPEG at the screen's longest side, the photo's date
    first in its name), sends it, shows it, and removes it (features/modules/files.py, ARCHITECTURE #44)."""
    import re
    import struct
    import zlib

    from mirrordash_core.forms import render_schema_form

    def png(width, height):  # a grey photo, built without an imaging library
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))

    form = render_schema_form({"properties": {"photos": {"format": "files", "accept": "image", "title": "Photos"}}},
                              {}, "modules[photos-1]", "mirrordash_photos")
    stored = [{"name": f"2026-07-{day:02d}_photo.jpg", "size": 1, "modified": day} for day in range(1, 41)]
    sent, thumbnails = [], []

    def files_route(route):
        request = route.request
        if request.method == "GET" and request.url.endswith("/files/photos"):
            return route.fulfill(json={"files": stored, "max_files": 500, "max_side": 2560, "free_bytes": 3 << 30})
        if request.method == "GET":
            thumbnails.append(request.url)
            return route.fulfill(status=404)
        if request.method == "POST":
            sent.append(request.url)
            return route.fulfill(json={"name": "added.jpg"})
        if request.method == "DELETE":
            return route.fulfill(json={"removed": request.url.rsplit("/", 1)[1]})
        route.fulfill(status=404)

    navigate_authenticated(page, server_url)
    page.route("**/admin/modules/photos-1/files/photos**", files_route)
    page.evaluate("""html => { const box = document.createElement('div'); box.id = 'files-test'; box.innerHTML = html;
                               document.body.append(box); box.querySelectorAll('[data-files-field]').forEach(setUpFilesField); }""", form)
    status, tiles, more = (page.locator(f"#files-test {selector}") for selector in (".files-status", ".files-tile", ".files-more"))
    page.wait_for_function("document.querySelector('#files-test .files-status').textContent.includes('40 of 500')")
    assert "3072 MB free" in status.text_content()
    # 40 photos: the newest 30 first, as their small copies, and the rest a tap away
    assert tiles.count() == 30 and tiles.first.locator("img").get_attribute("alt") == "2026-07-40_photo.jpg"
    assert tiles.first.locator("img").get_attribute("src").endswith("/2026-07-40_photo.jpg?thumb=1")
    assert more.is_visible() and more.text_content() == "Show more (10 left)"
    more.click()
    assert tiles.count() == 40 and more.is_hidden()
    # What the page sends, read in the page: WebKit gives Playwright no multipart body with a blob in it
    page.evaluate("""() => { const realFetch = window.fetch; window.__sent = [];
        window.fetch = async (url, options) => {
            if (options?.method === 'POST') {
                const sizes = async (file) => { const bitmap = await createImageBitmap(file);
                    return { name: file.name, type: file.type, size: file.size, width: bitmap.width, height: bitmap.height }; };
                window.__sent.push({ file: await sizes(options.body.get('file')), thumb: await sizes(options.body.get('thumb')) });
            }
            return realFetch(url, options);
        }; }""")
    page.set_input_files("#files-test input[type=file]", files=[{"name": "big.png", "mimeType": "image/png", "buffer": png(4000, 3000)}])
    page.wait_for_function("document.querySelector('#files-test .files-status').textContent.includes('41 of 500')")
    upload = page.evaluate("window.__sent")[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_big\.png", upload["file"]["name"])
    assert upload["file"]["type"] == "image/jpeg" and (upload["file"]["width"], upload["file"]["height"]) == (2560, 1920)
    assert upload["file"]["size"] < 3.5 * 1024 * 1024 and len(sent) == 1
    assert (upload["thumb"]["width"], upload["thumb"]["height"]) == (240, 180) and upload["thumb"]["size"] < 64 * 1024
    assert tiles.first.locator("img").get_attribute("alt") == "added.jpg"  # the newest first
    page.click("#files-test [aria-label='Remove 2026-07-01_photo.jpg']")
    page.wait_for_function("document.querySelector('#files-test .files-status').textContent.includes('40 of 500')")


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
    """A module's inline script receives its shadow root as `root`; it can't rely on document.currentScript.
    Under the real page's CSP only a card whose module may run scripts does (ARCHITECTURE #41): another
    card's script, inline handler or frame runs nothing."""
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from mirrordash_core.app import app
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"
    with patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=False), \
         patch("mirrordash_core.features.kiosk.routes.load_config", return_value={"admin_auth": {"hash": "h", "salt": "s"}}):
        index = TestClient(app, client=("127.0.0.1", 50000)).get("/")

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/":
            return route.fulfill(body=index.text, content_type="text/html",
                                 headers={"Content-Security-Policy": index.headers["content-security-policy"]})
        if path == "/api/active-modules":
            return route.fulfill(json=[])
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        route.fulfill(body=file.read_bytes(), content_type="text/html" if path.endswith(".html") else "text/javascript")

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/")

    script = ("<p class='out'></p><script>root.querySelector('.out').textContent = "
              "(document.currentScript ? 'current' : 'root') + ':' + (root._renders = (root._renders || 0) + 1);</script>")
    evil = ("<p class='out'>untouched</p><script>window.ran = 'script'</script>"
            "<img src='/nope' onerror=\"window.ran = 'onerror'\">"
            "<iframe srcdoc=\"<script>parent.ran = 'frame'</script>\"></iframe>")
    for module, html, scripts in (("clock-a", script, True), ("clock-b", script, True), ("clock-a", script, True),
                                  ("evil", evil, False)):
        msg = json.dumps({"module": module, "position": "top_left", "html": html, "scripts": scripts})
        page.evaluate("msg => window.__ws.onmessage({ data: msg })", msg)

    read = "m => document.querySelector(`[data-module='${m}']`).shadowRoot.querySelector('.out').textContent"
    assert page.evaluate(read, "clock-a") == "root:2"  # same root across re-renders, so state like timers persists
    assert page.evaluate(read, "clock-b") == "root:1"  # and it's per instance
    page.wait_for_timeout(500)
    assert page.evaluate("() => window.ran") is None and page.evaluate(read, "evil") == "untouched"


def test_a_rows_switch_and_show_if_work_and_follow_the_row_when_it_moves(page, server_url):
    """A list row has the form's own boxes: its switch hides its Name (show_if), and after the row moves its
    names, ids and show_if rule carry its new place (admin.js reindexArrayContainer)."""
    page.goto(f"{server_url}/design#forms")
    page.add_script_tag(url="/static/admin/admin.js")  # moving rows; the admin page has it
    card = page.locator(".component-card", has_text="List of Items")
    card.evaluate("c => c.querySelectorAll('.array-item-card').forEach(row => row.open = true)")  # a filled row folds
    name = card.locator(".show-if:has([name$='[custom_name]'])").first
    assert name.is_visible()
    card.locator(".array-item-card").nth(1).locator("button[title='Move up']").evaluate("b => b.click()")
    moved = card.locator(".array-item-card").nth(1)  # the living room, now the second row
    assert moved.locator("[name='modules[demo][entities][1][own_name]'][type=checkbox]").count() == 1
    assert "[entities][1][own_name]" in moved.locator(".show-if").first.get_attribute("data-show-if")
    moved.locator("input[type=checkbox][name$='[own_name]']").evaluate("box => box.click()")
    assert moved.locator(".show-if").first.is_hidden()


def test_show_if_follows_a_dropdown_and_a_switch(page, server_url):
    """show_if.js on /design's examples: the field appears and goes as the other field changes."""
    page.goto(f"{server_url}/design#forms")
    url = page.locator(".show-if:has([name='modules[demo][immich_url]'])")
    cover = page.locator(".show-if:has([name='modules[demo][cover_style]'])")
    assert url.is_hidden() and cover.is_visible()
    page.select_option("select[name='modules[demo][source]']", "immich")
    assert url.is_visible()
    page.locator("input[type=checkbox][name='modules[demo][show_cover]']").evaluate("box => box.click()")
    assert cover.is_hidden()
    # a hidden field breaking a rule is shown, so the browser can point at it instead of a save that does nothing
    page.select_option("select[name='modules[demo][source]']", "uploaded")
    page.add_script_tag(url="/static/admin/admin.js")  # its invalid handler; the admin page has it
    page.evaluate("""() => { const url = document.querySelector("[name='modules[demo][immich_url]']");
        url.required = true; url.value = ''; url.form.reportValidity(); }""")
    assert url.is_visible()


@pytest.mark.only_browser("webkit")
def test_every_design_component_looks_the_same_in_a_card_without_scripts(page):
    """What /design shows a module (modules.css, Lucide icons, the Inter font) works in a card whose module
    may not run scripts, under the screen's real CSP (ARCHITECTURE #41): each component's own styles are
    those /design shows. Layout from the surroundings (display, gap) and /design's admin text color aside."""
    import json
    import re
    from pathlib import Path

    from fastapi.testclient import TestClient

    from mirrordash_core.app import app
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"
    with patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=False), \
         patch("mirrordash_core.features.kiosk.routes.load_config", return_value={"admin_auth": {"hash": "h", "salt": "s"}}):
        index = TestClient(app, client=("127.0.0.1", 50000)).get("/")
    types = {".html": "text/html", ".css": "text/css", ".woff2": "font/woff2"}

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/":
            return route.fulfill(body=index.text, content_type="text/html",
                                 headers={"Content-Security-Policy": index.headers["content-security-policy"]})
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": []})
        file = static / ("design.html" if path == "/design" else path.removeprefix("/static/"))
        if not file.is_file():
            return route.fulfill(status=404)
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    classes = sorted(set(re.findall(r"\.([a-z][a-z0-9_-]+)", (static / "modules.css").read_text())))
    props = ["font-size", "font-weight", "color", "letter-spacing", "text-transform", "border-radius",
             "flex-direction", "opacity", "background-color", "border-top-width", "border-top-color"]
    # A component (its outermost element) and the styles of its parts
    helpers = """const comp = el => [...el.classList].some(c => classes.includes(c));
      const parts = el => [el, ...el.querySelectorAll('*')].filter(comp)
        .map(e => Object.fromEntries(props.map(p => [p, getComputedStyle(e).getPropertyValue(p)])));"""
    page.route("http://mirror.test/**", serve)
    page.goto("http://mirror.test/design")
    shown = page.evaluate(f"""([classes, props]) => {{ {helpers}
      return [...document.querySelectorAll('*')].filter(el => comp(el) && !el.closest('pre')
          && !el.parentElement.closest('*:is(' + classes.map(c => '.' + c).join(',') + ')')
          && !el.closest('.component-card')?.querySelector('.explorer-own-ui'))  // the mirror's own UI
        .map(el => ({{html: el.outerHTML, parts: parts(el)}}));
    }}""", [classes, props])
    assert len(shown) > 20

    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    refused = []
    page.on("console", lambda m: refused.append(m.text) if "Content Security Policy" in m.text else None)
    page.goto("http://mirror.test/")
    page.wait_for_function("window.__ws")
    html = "".join(f"<div data-sample>{c['html']}</div>" for c in shown)
    page.evaluate("msg => window.__ws.onmessage({ data: msg })",
                  json.dumps({"module": "community", "position": "middle_center", "html": html, "scripts": False}))
    page.wait_for_timeout(500)
    card = "document.querySelector(\"[data-module='community']\").shadowRoot"
    in_card = page.evaluate(f"""([classes, props]) => {{ {helpers}
      return [...{card}.querySelectorAll('[data-sample]')].map(s => parts(s.firstElementChild));
    }}""", [classes, props])
    admin_text = ("rgb(248, 250, 252)", "rgb(255, 255, 255)")  # /design's body text (admin.css), the mirror's
    differ = [(c["html"][:80], p, v, mine[p]) for c, got in zip(shown, in_card, strict=True)
              for there, mine in zip(c["parts"], got, strict=True) for p, v in there.items()
              if v != mine[p] and (v, mine[p]) != admin_text]
    assert not differ, differ
    assert page.evaluate(f"!!{card}.querySelector('svg.lucide')")  # data-lucide icons are drawn
    assert page.evaluate("[...document.fonts].some(f => f.family === 'Inter' && f.status === 'loaded')")
    assert not refused, refused


@pytest.mark.only_browser("webkit")
def test_mirror_page_keeps_the_settings_order_whichever_card_comes_first(page):
    """After a restart the page can load before the modules run: no placeholders hold the places, and the
    card that renders first (the clock) mustn't take the place of one that waits for the network (Home
    Assistant). Each card carries its place in the settings (order)."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": []})  # the module host hasn't reported yet
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        types = {".html": "text/html", ".css": "text/css"}
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    for name, order in (("clock", 2), ("ha", 0), ("namnsdag", 1)):
        page.evaluate("msg => window.__ws.onmessage({ data: msg })",
                      json.dumps({"module": name, "position": "top_left", "order": order, "html": name}))
    assert page.evaluate("[...document.querySelectorAll('#top_left [data-module]')].map(e => e.dataset.module)") == \
        ["ha", "namnsdag", "clock"]


@pytest.mark.only_browser("webkit")
def test_privacy_mode_keeps_only_the_marked_modules_else_the_monogram_or_nothing(page):
    """Privacy mode (features/power/privacy.py): only cards marked show_in_privacy stay; with none, the
    monogram or nothing, as the owner chose; off, everything is back."""
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
    send = lambda message: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(message))
    screen = lambda on, shows="monogram", scheduled_out=(): send({"type": "screen", "privacy": on, "privacy_shows": shows,
                                                                  "cards_over_photo": "see_through", "scheduled_out": list(scheduled_out)})
    shown = lambda name: page.evaluate(f"document.querySelector(`[data-module='{name}']`).offsetParent !== null")
    monogram = lambda: page.evaluate("getComputedStyle(document.getElementById('privacy-monogram')).display") != "none"
    send({"module": "clock", "position": "top_right", "html": "12:00", "show_in_privacy": True})
    send({"module": "calendar", "position": "top_left", "html": "Dentist 09:15"})
    send({"module": "photos", "position": "background", "html": "photo"})
    assert shown("clock") and shown("calendar") and not monogram()
    screen(True)
    assert shown("clock") and not shown("calendar") and not shown("photos") and not monogram()
    send({"module": "clock", "position": "top_right", "html": "12:00", "show_in_privacy": False})  # unmarked
    assert not shown("clock") and monogram()
    screen(True, "nothing")
    assert not monogram()
    screen(False)
    assert shown("clock") and shown("calendar") and shown("photos") and not monogram()

    # A module's schedule (display_power.py) hides its card the same way, and a carousel turns past it
    for name in ("news", "namnsdag"):
        send({"module": name, "position": "bottom_left", "html": name, "carousel_group": "morning"})
    active = lambda: page.evaluate("document.querySelector('#bottom_left .carousel-active').dataset.module")
    assert active() == "news"
    screen(False, scheduled_out=["news"])
    assert not shown("news") and active() == "namnsdag"
    page.evaluate("stepCarousel(document.querySelector('#bottom_left .carousel-group-container'))")
    assert active() == "namnsdag"
    send({"module": "news", "position": "bottom_left", "html": "news 2", "carousel_group": "morning"})  # an update
    assert not shown("news")
    screen(False)
    assert shown("news")


@pytest.mark.only_browser("webkit")
def test_background_and_foreground_fill_the_screen_behind_and_in_front(page):
    """The two layers cover the whole screen, the background under the nine anchors and the foreground
    over them, and their cards are see-through: the anchors' near-black card background would black out
    the mirror."""
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

    page.set_viewport_size({"width": 1280, "height": 720})
    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    for name, position in (("photos", "background"), ("clock", "top_left"), ("snow", "foreground")):
        page.evaluate("msg => window.__ws.onmessage({ data: msg })",
                      json.dumps({"module": name, "position": position, "html": name}))
    card = "(name => document.querySelector(`[data-module='${name}']`))"
    for name in ("photos", "snow"):
        # offset*: the layout, not the 10 px the first-mount fade (.module-enter) slides it in from
        box = page.evaluate(f"name => {{ const e = {card}(name); "
                            "return [e.offsetLeft, e.offsetTop, e.offsetWidth, e.offsetHeight]; }", name)
        assert box == [0, 0, 1280, 720], name
        assert page.evaluate(f"name => getComputedStyle({card}(name)).backgroundColor", name) == "rgba(0, 0, 0, 0)"
    # The anchors' cards over a background photo: see-through with a shadow under the text by default,
    # dark when the owner chose it, and dark again without a module in the background
    page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(
        {"type": "screen", "privacy": False, "privacy_shows": "monogram", "cards_over_photo": "see_through"}))
    clock = f"(() => {{ const style = getComputedStyle({card}('clock')); return [style.backgroundColor, style.textShadow]; }})()"
    background, shadow = page.evaluate(clock)
    assert background == "rgba(0, 0, 0, 0)" and shadow != "none"
    page.evaluate("document.documentElement.dataset.cardsOverPhoto = 'solid'")
    assert page.evaluate(clock)[0] not in ("rgba(0, 0, 0, 0)", "transparent")
    page.evaluate(f"document.documentElement.dataset.cardsOverPhoto = 'see_through'; (window.__photos = {card}('photos')).remove()")
    assert page.evaluate(clock)[0] not in ("rgba(0, 0, 0, 0)", "transparent")
    page.evaluate("document.getElementById('background').append(window.__photos)")
    # What a module's own CSS reads to fill the screen in a layer and keep its box elsewhere
    size = "name => getComputedStyle({card}(name)).getPropertyValue('--md-layer-size').trim()"
    assert [page.evaluate(size.format(card=card), name) for name in ("photos", "clock")] == ["100%", ""]
    # The order at the clock: the foreground (made hit-testable for the look), the clock, the background
    assert page.evaluate("getComputedStyle(document.getElementById('foreground')).pointerEvents") == "none"
    order = page.evaluate(f"""() => {{
        document.getElementById('foreground').style.pointerEvents = 'auto';
        const r = {card}('clock').getBoundingClientRect();
        return document.elementsFromPoint(r.x + 5, r.y + 5)
            .map(e => e.closest('[data-module]')?.dataset.module).filter(Boolean);
    }}""")
    assert [n for i, n in enumerate(order) if n not in order[:i]][:3] == ["snow", "clock", "photos"]
    # The connection indicator stays above the foreground
    assert int(page.evaluate("getComputedStyle(document.getElementById('ws-status')).zIndex")) > \
        int(page.evaluate("getComputedStyle(document.getElementById('foreground')).zIndex"))


@pytest.mark.only_browser("webkit")
def test_mirror_page_reloads_only_for_a_new_version(page):
    """After an update the page's own code is new too: it reloads when the server's version or boot id
    changed (a restart may have changed the modules), not when the socket merely reconnected."""
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
    send = lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))
    hello = lambda version, boot="a": send({"type": "hello", "version": version, "boot_id": boot})
    module = lambda name: send({"module": name, "position": "top_left", "carousel_group": "g", "html": name})
    page.evaluate("window.__loaded = true")

    # A module redrawing keeps the slide that is showing, and the group's timer
    module("one")
    module("two")
    group = "document.querySelector('.carousel-group-container')"
    page.evaluate(f"{group}.children[0].classList.remove('carousel-active'); {group}.children[1].classList.add('carousel-active')")
    timer = page.evaluate(f"{group}._carouselTimer")
    module("two")
    assert page.evaluate(f"{group}.children[1].classList.contains('carousel-active')")
    assert page.evaluate(f"{group}._carouselTimer") == timer

    hello("1.0.0")  # the version the page was loaded with
    hello("1.0.0")  # the socket reconnected, same version and boot: nothing to do
    assert page.evaluate("window.__loaded") is True

    with page.expect_navigation():
        hello("1.0.0", "b")  # MirrorDash restarted (module installed, backup restored): fresh content
    assert page.evaluate("window.__loaded === undefined")

    page.wait_for_function("window.__ws")
    page.evaluate("window.__loaded = true")
    hello("1.0.0")
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


def test_logging_in_with_the_form_keeps_the_page_logged_in(page, server_url):
    """The login form: a wrong password says so, the right one opens the page, and a reload stays
    logged in (the session cookie), with no password left in the browser's storage."""
    page.goto(f"{server_url}/admin")
    page.wait_for_selector("#auth-overlay.open")
    page.fill("#auth-password-input", "wrong")
    page.click("#auth-submit-btn")
    page.wait_for_selector("#auth-error", state="visible")
    page.fill("#auth-password-input", "secret")
    page.click("#auth-submit-btn")
    page.wait_for_selector("#auth-overlay.open", state="hidden")
    page.reload()
    page.wait_for_selector("#page-tab-dashboard")
    assert not page.locator("#auth-overlay.open").count()
    assert page.evaluate("() => localStorage.getItem('mirrordash_api_key')") is None


def test_turning_matter_on_shows_the_pairing_code(page, server_url, tmp_path):
    from mirrordash_core.features.matter import node as matter

    async def node_starts(action):  # the node writes its codes once it is up
        (tmp_path / "pairing.json").write_text('{"qr": "MT:Y.K90Q12122UVH7SR00", "manual": "11403521097", "commissioned": false}')
        return True
    config = {"system": {}}
    with patch.object(matter, "MATTER_DIR", tmp_path), patch.object(matter, "ENV_FILE", tmp_path / "env"), \
         patch.object(matter, "load_config", return_value=config), patch.object(matter, "save_config"), \
         patch.object(matter, "systemctl", node_starts):
        navigate_authenticated(page, server_url)
        page.click("#page-tab-config")
        page.wait_for_selector("#matter-card")
        assert page.locator("#matter-qr").count() == 0
        page.locator("#matter-card .switch").click()
        page.wait_for_selector("#matter-qr svg")
        assert "1140-352-1097" in page.locator("#matter-card").text_content()
        assert config["matter"]["enabled"] is True


@pytest.mark.only_browser("webkit")
def test_while_a_video_plays_the_page_holds_still(page):
    """ARCHITECTURE #33: modules see video_is_playing on their host and as an event, their animations
    stand still, and the carousels drop their fade and their own timers (they step on the minute)."""
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
    send = lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))
    spinner = "<span class='module-loading-spinner'></span>"
    for module in ("news", "weather"):
        send({"module": module, "position": "top_left", "html": spinner, "carousel_group": "g", "carousel_interval": 5})
    page.evaluate("addEventListener('mirrordash:video_is_playing', e => window.__heard = e.detail.playing)")
    group = "document.querySelector('.carousel-group-container')"
    slide = "document.querySelector('[data-module=news]')"
    assert page.evaluate(f"{group}._carouselTimer") is not None

    send({"type": "video_is_playing", "playing": True})
    assert page.evaluate("window.__heard") is True
    assert page.evaluate(f"document.documentElement.hasAttribute('data-video-is-playing') && {slide}.hasAttribute('data-video-is-playing')")
    assert page.evaluate(f"getComputedStyle({slide}).transitionDuration") == "0s"
    assert page.evaluate(f"getComputedStyle({slide}.shadowRoot.querySelector('span')).animationDuration") == "0s"
    assert page.evaluate(f"{group}._carouselTimer") is None
    send({"module": "clock", "position": "top_right", "html": "<p>12:00</p>"})  # drawn while playing
    fade = "<style>@keyframes f { from { opacity: 0 } } p { animation: f 0.5s }</style><p>x</p>"
    send({"module": "clock", "position": "top_right", "html": fade})  # a fade-in under the hold is not stuck invisible
    page.wait_for_timeout(100)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-module=clock]').shadowRoot.querySelector('p')).opacity") == "1"
    assert page.evaluate("document.querySelector('[data-module=clock]').hasAttribute('data-video-is-playing')")

    send({"type": "video_is_playing", "playing": False})
    assert page.evaluate("window.__heard") is False
    assert not page.evaluate(f"{slide}.hasAttribute('data-video-is-playing')")
    assert page.evaluate(f"{group}._carouselTimer") is not None


@pytest.mark.only_browser("webkit")
def test_the_page_tells_its_load_and_shows_the_crash_guards_message(page):
    """ARCHITECTURE #47: the page sends its load's id on connecting (a new one the core didn't ask for is a
    crash), and at the guard's top step shows the core's words at the bottom edge until it's let go."""
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
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; this.sent = []; } send(m) { this.sent.push(m); } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    page.evaluate("window.__ws.readyState = 1; window.__ws.onopen()")
    told = json.loads(page.evaluate("window.__ws.sent[0]"))
    assert told["type"] == "page" and told["id"] and told["boot"] is None  # just loaded: no core start heard of yet
    send = lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))
    screen = {"type": "screen", "privacy": False, "privacy_shows": "monogram", "cards_over_photo": "see_through",
              "scheduled_out": []}
    notice = "document.getElementById('guard-notice')"
    send({**screen, "guard": 0, "guard_message": None})
    assert page.evaluate(f"{notice}.hidden")
    send({**screen, "guard": 3, "guard_message": {"title": "Skärmen kraschade flera gånger", "body": "Modulerna är pausade."}})
    assert not page.evaluate(f"{notice}.hidden")
    assert page.evaluate(f"{notice}.querySelector('.guard-notice__title').textContent") == "Skärmen kraschade flera gånger"
    box = page.evaluate(f"(() => {{ const r = {notice}.getBoundingClientRect(); return [r.bottom, innerHeight]; }})()")
    assert box[1] - box[0] < 120  # at the bottom edge, the centre stays empty
    send({**screen, "guard": 0, "guard_message": None})  # Show Again
    assert page.evaluate(f"{notice}.hidden")


@pytest.mark.only_browser("webkit")
def test_the_page_says_where_a_modules_video_window_is(page):
    """features/video asks; the page answers with the data-video-window element's place in CSS pixels."""
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
    page.add_init_script("window.__sent = []; window.WebSocket = class { constructor() { window.__ws = this; }"
                         " send(text) { window.__sent.push(text); } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    send = lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))
    send({"module": "tv", "position": "top_left", "html": "<div data-video-window style='width:480px;height:270px'></div>"})
    send({"module": "clock", "position": "top_right", "html": "<p>12:00</p>"})

    send({"type": "video_window", "module": "tv"})  # within the card's entrance (fadeIn): where it ends up, not where it is
    send({"type": "video_window", "module": "clock"})
    answers = [json.loads(t) for t in page.evaluate("window.__sent") if t.startswith("{")]
    box = page.evaluate("(b => [b.left, b.top])(document.querySelector('[data-module=tv]')"
                        ".shadowRoot.querySelector('div').getBoundingClientRect())")
    assert answers[0] == {"type": "video_window", "module": "tv", "rect": [round(box[0]), round(box[1]), 480, 270]}
    assert answers[1] == {"type": "video_window", "module": "clock", "rect": None}  # no window on its card


@pytest.mark.only_browser("webkit")
def test_a_carousel_group_name_with_quotes_and_a_title_with_markup_stay_text(page):
    """The carousel group is the owner's free text and a title comes from a module's translations: a quote
    in the group once broke the page's selector (the card never drew), and a title went in as markup."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"
    group = 'Kitchen "A"]'

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": [{"name": "clock", "position": "top_left", "carousel_group": group,
                                                    "title": "<b id='injected'>Clock</b>"}]})
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        types = {".html": "text/html", ".css": "text/css"}
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_selector("[data-module='clock'].module-loading-placeholder")
    assert page.evaluate("document.getElementById('injected')") is None
    assert "<b id='injected'>" in page.locator("[data-module='clock']").text_content()
    page.wait_for_function("window.__ws")
    page.evaluate("msg => window.__ws.onmessage({ data: msg })",
                  json.dumps({"module": "clock", "position": "top_left", "carousel_group": group, "html": "<p class='time'>12:00</p>"}))
    assert page.locator("[data-module='clock']").evaluate("e => e.shadowRoot.querySelector('.time').textContent") == "12:00"
    assert page.evaluate("document.querySelectorAll('.carousel-group-container').length") == 1


def test_the_bell_counts_lists_and_marks_read(page, server_url):
    """Unread notifications are counted on the bell; its list opens on a click, marks one read, and the
    count follows."""
    from mirrordash_core import notifications
    notifications.add("info", "MirrorDash updated to 0.16.0")
    notifications.add("error", "Clock kept crashing and was stopped")
    navigate_authenticated(page, server_url)
    count = page.locator("#notif-count")
    count.wait_for()
    assert count.inner_text().startswith("2")
    page.click(".notif-bell")
    page.wait_for_selector(".notif-item.error")
    if os.environ.get("MIRRORDASH_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["MIRRORDASH_SCREENSHOTS"], "bell.png"))
    page.locator(".notif-item.error .notif-read").click()
    page.wait_for_selector(".notif-item.error.read")
    page.wait_for_function("document.getElementById('notif-count').innerText.startsWith('1')")
    page.mouse.click(5, 400)  # a click outside closes the list
    assert not page.locator(".notif-menu").is_visible()


@pytest.mark.only_browser("webkit")
def test_the_mirror_shows_an_unread_error(page):
    """While an error notification is unread, the mirror says to read it on the admin page."""
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
    send = lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))
    badge = page.locator("#error-badge")
    send({"type": "hello", "version": "1.0.0", "boot_id": "a", "online": False, "error": True})
    assert badge.is_visible() and "admin page" in badge.inner_text()
    if os.environ.get("MIRRORDASH_SCREENSHOTS"):
        page.screenshot(path=os.path.join(os.environ["MIRRORDASH_SCREENSHOTS"], "mirror-error.png"))
    send({"type": "notices", "error": False})
    assert not badge.is_visible()
