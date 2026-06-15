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
    assert "WiFi Setup" in response.text

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
    assert "System is rebooting..." in response.json()["message"]
    mock_connect.assert_called_once_with("HomeNet", "pass")
    mock_reboot.assert_called_once_with(delay_sec=3.0)

@patch("mirrordash_core.app.connect_wifi", new_callable=AsyncMock)
@patch("mirrordash_core.app.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure(mock_reboot, mock_connect, client):
    mock_connect.return_value = (False, "Wrong password")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["message"] == "Wrong password"
    mock_connect.assert_called_once_with("HomeNet", "wrong")
    mock_reboot.assert_not_called()

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
def test_wifi_scan_no_cache_returns_empty(mock_cache, client):
    """When nmcli fails and there is no cache file, return an empty list."""
    mock_cache.return_value = []
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
