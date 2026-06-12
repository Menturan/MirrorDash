import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from mirrordash_core.app import app

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
