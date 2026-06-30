import time
import socket
import threading
import uvicorn
import pytest
import contextlib
from unittest.mock import patch, AsyncMock

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
    mock_rw = AsyncMock(return_value=True)
    mock_ro = AsyncMock(return_value=True)
    mock_restart = AsyncMock(return_value=True)
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
                ("remount_rw", mock_rw),
                ("remount_ro", mock_ro),
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

    # 1. Verify we land on the default configuration page
    page.wait_for_selector("h1")
    assert page.locator("#page-tab-config").is_visible()
    
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
    
    # Wait for the config fields inside the drawer to load and expand
    drawer = page.locator("#config-drawer-mirrordash_clock")
    page.wait_for_selector("#config-drawer-mirrordash_clock", state="visible")
    assert drawer.is_visible()

    # Click again to collapse
    config_btn.click()
    page.wait_for_selector("#config-drawer-mirrordash_clock", state="hidden")
    assert not drawer.is_visible()

def test_admin_dashboard_restart_overlay(page, server_url):
    navigate_authenticated(page, server_url)
    page.wait_for_selector("h1")

    # Click the main Restart button in the header
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

    # Verify Visual Editor is visible
    assert page.locator("#panel-visual").is_visible()
    
    # Switch to Raw JSON tab
    page.click("#tab-raw")
    page.wait_for_selector("#panel-raw", state="visible")
    assert page.locator("#panel-raw").is_visible()
    assert not page.locator("#panel-visual").is_visible()
    
    # Switch back to Visual Editor
    page.click("#tab-visual")
    page.wait_for_selector("#panel-visual", state="visible")
    assert page.locator("#panel-visual").is_visible()

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
    page.wait_for_selector("#backups-list-tbody")

    # Verify mock backup is rendered in list
    assert page.locator("text=backup_2026-06-29.mirror").is_visible()

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
    assert "Backup generated successfully" in page.locator("#global-status").text_content()

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

    # Toggle SSH switch and set user password
    assert not page.locator("#sys-ssh-password-group").is_visible()
    page.evaluate("document.getElementById('sys-ssh').checked = true; document.getElementById('sys-ssh').dispatchEvent(new Event('change'))")
    page.wait_for_selector("#sys-ssh-password-group", state="visible")
    assert page.locator("#sys-ssh-password-group").is_visible()
    page.fill("#sys-ssh-password", "pi_password_123")

    # Change rotation settings
    page.select_option("#sys-rotation", "right")

    # Submit settings form
    page.click("#save-system-btn")

    # Verify status bar notifies user of successful save
    page.wait_for_selector("#global-status", state="visible")
    assert "System settings applied successfully" in page.locator("#global-status").text_content()


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
    drawer = page.locator("#config-drawer-mirrordash_calendar")
    page.wait_for_selector("#config-drawer-mirrordash_calendar", state="visible")
    assert drawer.is_visible()

    # Wait for the config fields inside the drawer to load and contain Configuration Parameters
    fields = page.locator("#config-fields-mirrordash_calendar")
    page.wait_for_selector("#config-fields-mirrordash_calendar *", state="visible")
    inner_html = fields.inner_html()
    assert "Configuration Parameters" in inner_html

    assert len([err for err in console_errors if "error" in err]) == 0

