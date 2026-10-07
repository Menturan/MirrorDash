import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from mirrordash_core.app import app
from mirrordash_core.system.network import scan_wifi_networks

@pytest.fixture
def client():
    with patch("mirrordash_core.app.load_config") as mock_load:
        mock_load.return_value = {}
        yield TestClient(app)

def test_index_redirect_captive_host(client):
    response = client.get("/", headers={"host": "10.42.0.1"}, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/wifi-setup"

def test_index_redirect_captive_param(client):
    response = client.get("/?captive=true", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/wifi-setup"

def test_index_no_redirect_normal(client):
    # Test normal request (without redirect)
    response = client.get("/", headers={"host": "localhost:8000"}, follow_redirects=False)
    assert response.status_code == 200

def test_wifi_setup_page(client):
    response = client.get("/wifi-setup")
    assert response.status_code == 200
    assert "Choose your Wi-Fi" in response.text

@patch("mirrordash_core.app.scan_wifi_networks", new_callable=AsyncMock)
def test_wifi_scan(mock_scan, client):
    mock_scan.return_value = ["MyHomeWiFi", "CoffeeShopWiFi"]
    response = client.get("/api/wifi/scan")
    assert response.status_code == 200
    assert response.json() == {"networks": ["MyHomeWiFi", "CoffeeShopWiFi"]}
    mock_scan.assert_called_once()

@patch("mirrordash_core.app.connect_wifi", new_callable=AsyncMock)
@patch("mirrordash_core.app.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_success(mock_reboot, mock_connect, client):
    mock_connect.return_value = (True, "Successfully connected!")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "pass"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "restarting" in response.json()["message"]
    mock_connect.assert_called_once_with("HomeNet", "pass")
    mock_reboot.assert_called_once_with(delay_sec=3.0)

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=False)
@patch("mirrordash_core.app.connect_wifi", new_callable=AsyncMock)
@patch("mirrordash_core.app.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure(mock_reboot, mock_connect, _hotspot, client):
    mock_connect.return_value = (False, "Wrong password")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["message"] == "Wrong password"
    mock_connect.assert_called_once_with("HomeNet", "wrong")
    mock_reboot.assert_not_called()

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
@patch("mirrordash_core.app.connect_wifi", new_callable=AsyncMock, return_value=(False, "Wrong password"))
@patch("mirrordash_core.app.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure_from_hotspot_restarts(mock_reboot, _connect, _hotspot, client):
    """The hotspot is already gone when the connection fails: restart so it comes back."""
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    assert response.json()["status"] == "error"
    mock_reboot.assert_called_once_with(delay_sec=3.0)

def test_wifi_setup_missing_ssid(client):
    response = client.post("/api/wifi/setup", json={"password": "wrong"})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["message"] == "SSID is required"


@patch("mirrordash_core.system.network._load_cached_scan")
@patch("mirrordash_core.system.network.asyncio.create_subprocess_exec")
def test_wifi_scan_fallback_to_cache(mock_subprocess, mock_cache, client):
    """When nmcli fails, scan_wifi_networks should fall back to the cached file."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"device not ready"))
    mock_proc.returncode = 1
    mock_subprocess.return_value = mock_proc
    mock_cache.return_value = ["CachedNet"]

    response = client.get("/api/wifi/scan")
    assert response.status_code == 200
    assert response.json() == {"networks": ["CachedNet"]}
    mock_cache.assert_called_once()


@patch("mirrordash_core.system.network._load_cached_scan")
@patch("mirrordash_core.system.network.asyncio.create_subprocess_exec")
def test_wifi_scan_no_cache_returns_empty(mock_subprocess, mock_cache, client):
    """When nmcli fails and there is no cache file, return an empty list."""
    mock_cache.return_value = []
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"device not ready"))
    mock_proc.returncode = 1
    mock_subprocess.return_value = mock_proc

    import asyncio

    coro = scan_wifi_networks()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
    finally:
        loop.close()
    assert result == []


@patch("mirrordash_core.system.network._teardown_captive_ap", new_callable=AsyncMock)
@patch("mirrordash_core.app.reboot_system", new_callable=AsyncMock)
@patch("mirrordash_core.app.connect_wifi")
def test_wifi_setup_tears_down_ap(mock_connect, mock_reboot, mock_teardown, client):
    """connect_wifi should tear down the captive AP before connecting."""
    mock_connect.return_value = (True, "Successfully connected!")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "pass"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_index_serves_wifi_prompt_when_hotspot_active(mock_hotspot, client):
    mock_hotspot.return_value = True
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "Connect the mirror to Wi-Fi" in response.text

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_index_serves_admin_prompt_when_setup_required(mock_hotspot, client):
    mock_hotspot.return_value = False
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "Welcome to MirrorDash" in response.text

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock)
@patch("mirrordash_core.app.load_config")
def test_index_serves_dashboard_when_configured(mock_load, mock_hotspot, client):
    mock_hotspot.return_value = False
    mock_load.return_value = {"admin_auth": {"hash": "dummy_hash", "salt": "dummy_salt"}}
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "MirrorDash" in response.text
    assert "Connect the mirror to Wi-Fi" not in response.text
    assert "Welcome to MirrorDash" not in response.text

@patch("mirrordash_core.app.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_captive_portal_redirects_remote_client(mock_hotspot, client):
    mock_hotspot.return_value = True

    # Remote client tries to access Apple's captive detection portal path
    response = client.get("/hotspot-detect.html", headers={"host": "captive.apple.com"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://10.42.0.1/wifi-setup"

    # Remote client tries to access root /
    response = client.get("/", headers={"host": "google.com"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://10.42.0.1/wifi-setup"

    # Remote client tries to access an allowed path (/wifi-setup)
    response = client.get("/wifi-setup", headers={"host": "10.42.0.1"}, follow_redirects=False)
    assert response.status_code == 200

    # Remote client tries to access an allowed path (/static/style.css)
    response = client.get("/static/style.css", headers={"host": "10.42.0.1"}, follow_redirects=False)
    assert response.status_code != 302



def test_hotspot_check_is_not_cached_forever():
    """Boot race: the first check runs before the hotspot is up and must not stick."""
    import asyncio
    from unittest.mock import MagicMock
    from mirrordash_core.system import network

    def nmcli_result(names):
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(names.encode(), b""))
        return proc

    network._hotspot_active_cached = None
    clock = [1000.0]
    with patch("mirrordash_core.system.network.time.monotonic", side_effect=lambda: clock[0]), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = [nmcli_result("lo\n"), nmcli_result("lo\nMirrorDash-Setup\n")]
        assert asyncio.run(network.is_wifi_hotspot_active()) is False
        clock[0] += 1  # within TTL: cached, no new nmcli call
        assert asyncio.run(network.is_wifi_hotspot_active()) is False
        assert mock_exec.call_count == 1
        clock[0] += network.HOTSPOT_CACHE_TTL  # TTL expired: hotspot is now seen
        assert asyncio.run(network.is_wifi_hotspot_active()) is True
    network._hotspot_active_cached = None
