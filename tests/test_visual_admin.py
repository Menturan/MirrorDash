import time
import socket
import threading
import uvicorn
import pytest
from unittest.mock import patch, AsyncMock
from mirrordash_core.app import app
from mirrordash_core.api.admin import hash_password

# Setup mock config
mock_salt = "0123456789abcdef"
mock_hash = hash_password("secret", mock_salt)

MOCK_CONFIG = {
    "admin_auth": {
        "hash": mock_hash,
        "salt": mock_salt
    },
    "system": {
        "rotation": "normal",
        "resolution": "auto",
        "brightness": 100,
        "volume": 80
    },
    "modules": {
        "mirrordash-clock": {
            "enabled": True,
            "position": "top_right",
            "format": "24h"
        }
    }
}

def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

@pytest.fixture(scope="module")
def server_url():
    # Globally patch load_config and startup sequences for the server thread
    with patch("mirrordash_core.api.admin_shared.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.admin_auth.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.admin_modules_panels.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.config.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.display_power.display_power_manager.start", new_callable=AsyncMock), \
         patch("mirrordash_core.display_power.display_power_manager.stop", new_callable=AsyncMock), \
         patch("mirrordash_core.module_loader.module_loader.start_modules", new_callable=AsyncMock), \
         patch("mirrordash_core.module_loader.module_loader.stop_modules", new_callable=AsyncMock):
        
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
    # Mock OS/system actions so the test server doesn't shut down or edit read-only FS
    with patch("mirrordash_core.api.admin_shared.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.admin_auth.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.api.admin_modules_panels.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.config.load_config", return_value=MOCK_CONFIG), \
         patch("mirrordash_core.system.remount_rw", new_callable=AsyncMock), \
         patch("mirrordash_core.system.remount_ro", new_callable=AsyncMock), \
         patch("mirrordash_core.system.run_restart", new_callable=AsyncMock):
        yield

def test_admin_dashboard_navigation_and_drawer(page, server_url):
    # Intercept prompt dialog for API key input and confirm dialogs
    def handle_dialog(dialog):
        if dialog.type == "prompt":
            dialog.accept("secret")
        elif dialog.type == "confirm":
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", handle_dialog)

    # Navigate to the dashboard
    page.goto(f"{server_url}/admin")

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
    def handle_dialog(dialog):
        if dialog.type == "prompt":
            dialog.accept("secret")
        elif dialog.type == "confirm":
            dialog.accept()
        else:
            dialog.dismiss()

    page.on("dialog", handle_dialog)

    page.goto(f"{server_url}/admin")
    page.wait_for_selector("h1")

    # Click the main Restart button in the header
    restart_btn = page.locator("#restart-btn")
    assert restart_btn.is_visible()
    restart_btn.click()

    # The glassmorphic restart overlay should immediately show up
    overlay = page.locator("#restart-overlay")
    page.wait_for_selector("#restart-overlay", state="visible")
    assert overlay.is_visible()
    assert page.locator("#restart-overlay-title").text_content() == "Restarting MirrorDash"

    # Wait for the overlay to naturally disappear once /health responds successfully
    page.wait_for_selector("#restart-overlay", state="hidden")
    assert not overlay.is_visible()
