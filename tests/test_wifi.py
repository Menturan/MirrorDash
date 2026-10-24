from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.features.wifi.network import scan_wifi_networks


@pytest.fixture
def client():
    with patch("mirrordash_core.features.wifi.routes.load_config") as mock_load, \
         patch("mirrordash_core.features.kiosk.routes.load_config", new=mock_load):
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

@patch("mirrordash_core.features.wifi.routes.scan_wifi_networks", new_callable=AsyncMock)
def test_wifi_scan(mock_scan, client):
    mock_scan.return_value = ["MyHomeWiFi", "CoffeeShopWiFi"]
    response = client.get("/api/wifi/scan")
    assert response.status_code == 200
    assert response.json() == {"networks": ["MyHomeWiFi", "CoffeeShopWiFi"]}
    mock_scan.assert_called_once()

@patch("mirrordash_core.features.modules.client.module_host.reload_modules", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.routes.connect_wifi", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.routes.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_success_needs_no_restart(mock_reboot, mock_connect, mock_reload, client):
    """Connected: no restart (the mirror's screen follows by itself), only the modules start over."""
    mock_connect.return_value = (True, "Successfully connected!")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "pass"})
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "Connected."}
    mock_connect.assert_called_once_with("HomeNet", "pass")
    mock_reboot.assert_not_called()
    mock_reload.assert_awaited_once()

@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=False)
@patch("mirrordash_core.features.wifi.routes.connect_wifi", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.routes.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure(mock_reboot, mock_connect, _hotspot, client):
    mock_connect.return_value = (False, "Wrong password")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["message"] == "Wrong password"
    mock_connect.assert_called_once_with("HomeNet", "wrong")
    mock_reboot.assert_not_called()

@patch("mirrordash_core.features.wifi.routes.restore_captive_ap", new_callable=AsyncMock, return_value=True)
@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
@patch("mirrordash_core.features.wifi.routes.connect_wifi", new_callable=AsyncMock, return_value=(False, "Wrong password"))
@patch("mirrordash_core.features.wifi.routes.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure_from_hotspot_brings_the_hotspot_back(mock_reboot, _connect, _hotspot, mock_restore, client):
    """The hotspot is already gone when the connection fails: start it again, no restart."""
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    assert response.json()["status"] == "error"
    mock_restore.assert_awaited_once()
    mock_reboot.assert_not_called()


@patch("mirrordash_core.features.wifi.routes.restore_captive_ap", new_callable=AsyncMock, return_value=False)
@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
@patch("mirrordash_core.features.wifi.routes.connect_wifi", new_callable=AsyncMock, return_value=(False, "Wrong password"))
@patch("mirrordash_core.features.wifi.routes.reboot_system", new_callable=AsyncMock)
def test_wifi_setup_failure_restarts_if_the_hotspot_wont_come_back(mock_reboot, _connect, _hotspot, _restore, client):
    """Last resort: if the hotspot can't be started, restart so the Wi-Fi check at boot opens it."""
    client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "wrong"})
    mock_reboot.assert_called_once_with(delay_sec=3.0)


@patch("mirrordash_core.features.wifi.network.asyncio.create_subprocess_exec", new_callable=AsyncMock)
def test_restore_captive_ap_starts_the_same_profile(mock_exec):
    """The hotspot comes back with nmcli up on the kept profile, so its password stays the same."""
    import asyncio

    from mirrordash_core.features.wifi.network import restore_captive_ap
    proc = AsyncMock(returncode=0)
    proc.communicate.return_value = (b"", b"")
    mock_exec.return_value = proc
    assert asyncio.run(restore_captive_ap()) is True
    assert mock_exec.call_args.args == ("nmcli", "connection", "up", "MirrorDash-Setup")

def test_wifi_setup_missing_ssid(client):
    response = client.post("/api/wifi/setup", json={"password": "wrong"})
    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["message"] == "SSID is required"


@patch("mirrordash_core.features.wifi.network._load_cached_scan")
@patch("mirrordash_core.features.wifi.network.asyncio.create_subprocess_exec")
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


@patch("mirrordash_core.features.wifi.network._load_cached_scan", return_value=["CachedNet", "MirrorDash-Setup"])
@patch("mirrordash_core.features.wifi.network.asyncio.create_subprocess_exec")
def test_wifi_scan_empty_while_hotspot_uses_cache(mock_subprocess, _cache, client):
    """In hotspot mode nmcli can succeed with nothing (the radio can't scan): use the saved list,
    and never offer the mirror's own setup network."""
    mock_proc = AsyncMock()
    mock_proc.communicate = AsyncMock(return_value=(b"MirrorDash-Setup\n", b""))
    mock_proc.returncode = 0
    mock_subprocess.return_value = mock_proc
    assert client.get("/api/wifi/scan").json() == {"networks": ["CachedNet"]}


@patch("mirrordash_core.features.wifi.network._load_cached_scan")
@patch("mirrordash_core.features.wifi.network.asyncio.create_subprocess_exec")
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


@patch("mirrordash_core.features.modules.client.module_host.reload_modules", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.network._teardown_captive_ap", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.routes.reboot_system", new_callable=AsyncMock)
@patch("mirrordash_core.features.wifi.routes.connect_wifi")
def test_wifi_setup_tears_down_ap(mock_connect, mock_reboot, mock_teardown, _reload, client):
    """connect_wifi should tear down the captive AP before connecting."""
    mock_connect.return_value = (True, "Successfully connected!")
    response = client.post("/api/wifi/setup", json={"ssid": "HomeNet", "password": "pass"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"

@patch("mirrordash_core.features.kiosk.routes.get_hotspot_password", new_callable=AsyncMock, return_value="abcde23456")
@patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_index_serves_wifi_prompt_when_hotspot_active(mock_hotspot, mock_password, client):
    """A phone on the hotspot can claim to be localhost, but never gets the hotspot password."""
    mock_hotspot.return_value = True
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "Connect the mirror to Wi-Fi" in response.text
    assert "abcde23456" not in response.text and "qrcode.js" not in response.text
    mock_password.assert_not_awaited()


@patch("mirrordash_core.features.kiosk.routes.get_hotspot_password", new_callable=AsyncMock, return_value="abcde23456")
@patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
def test_wifi_prompt_shows_password_and_qr_on_the_mirror(mock_hotspot, mock_password):
    """The mirror's own screen (loopback) shows the hotspot password and a Wi-Fi QR code."""
    mirror = TestClient(app, client=("127.0.0.1", 50000))
    response = mirror.get("/", headers={"host": "localhost:8000"})
    assert "abcde23456" in response.text
    assert "/static/js/qrcode.js" in response.text
    assert '"WIFI:T:WPA;S:MirrorDash-Setup;P:abcde23456;;"' in response.text


@patch("mirrordash_core.features.wifi.network.asyncio.create_subprocess_exec", new_callable=AsyncMock)
def test_teardown_keeps_the_hotspot_profile(mock_exec):
    """Setup only takes the hotspot down; deleting it would give the mirror a new password every time."""
    import asyncio

    from mirrordash_core.features.wifi.network import _teardown_captive_ap
    mock_exec.return_value.wait = AsyncMock(return_value=0)
    mock_exec.return_value.returncode = 0
    asyncio.run(_teardown_captive_ap())
    commands = [call.args for call in mock_exec.call_args_list]
    assert commands == [("nmcli", "connection", "down", "MirrorDash-Setup")]

@patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_index_serves_admin_prompt_when_setup_required(mock_hotspot, client):
    mock_hotspot.return_value = False
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "Welcome to MirrorDash" in response.text

@patch("mirrordash_core.features.kiosk.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
@patch("mirrordash_core.features.kiosk.routes.load_config")
def test_index_serves_dashboard_when_configured(mock_load, mock_hotspot, client):
    mock_hotspot.return_value = False
    mock_load.return_value = {"admin_auth": {"hash": "dummy_hash", "salt": "dummy_salt"}}
    response = client.get("/", headers={"host": "localhost:8000"})
    assert response.status_code == 200
    assert "MirrorDash" in response.text
    assert "Connect the mirror to Wi-Fi" not in response.text
    assert "Welcome to MirrorDash" not in response.text
    # The modules' cards run here: their JS may talk to this server only (ARCHITECTURE #41)
    csp = response.headers["content-security-policy"]
    assert "connect-src 'self' ws://localhost:8000;" in csp and "frame-ancestors 'none'" in csp
    evil = client.get("/", headers={"host": "x; connect-src *"}).headers["content-security-policy"]
    assert "connect-src 'self';" in evil and "*;" not in evil.replace("img-src *", "")

@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock)
def test_captive_portal_redirects_remote_client(mock_hotspot, client):
    mock_hotspot.return_value = True

    # Remote client tries to access Apple's captive detection portal path
    response = client.get("/hotspot-detect.html", headers={"host": "captive.apple.com"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://mirrordash.setup/wifi-setup"

    # Remote client tries to access root /
    response = client.get("/", headers={"host": "google.com"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://mirrordash.setup/wifi-setup"

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

    from mirrordash_core.features.wifi import network

    def nmcli_result(names):
        proc = MagicMock(returncode=0)
        proc.communicate = AsyncMock(return_value=(names.encode(), b""))
        return proc

    network._hotspot_active_cached = None
    clock = [1000.0]
    with patch("mirrordash_core.features.wifi.network.time.monotonic", side_effect=lambda: clock[0]), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = [nmcli_result("lo\n"), nmcli_result("lo\nMirrorDash-Setup\n")]
        assert asyncio.run(network.is_wifi_hotspot_active()) is False
        clock[0] += 1  # within TTL: cached, no new nmcli call
        assert asyncio.run(network.is_wifi_hotspot_active()) is False
        assert mock_exec.call_count == 1
        clock[0] += network.HOTSPOT_CACHE_TTL  # TTL expired: hotspot is now seen
        assert asyncio.run(network.is_wifi_hotspot_active()) is True
    network._hotspot_active_cached = None


@pytest.mark.parametrize("host,path", [("captive.apple.com", "/hotspot-detect.html"),
                                       ("connectivitycheck.gstatic.com", "/generate_204"),
                                       ("www.msftconnecttest.com", "/connecttest.txt")])
@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
def test_phone_connectivity_checks_get_the_sign_in_redirect(_hotspot, client, host, path):
    """On the hotspot every name resolves to the mirror; a redirect makes the phone show 'Sign in to network'."""
    response = client.get(path, headers={"host": host}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "http://mirrordash.setup/wifi-setup"


@patch("mirrordash_core.features.wifi.routes.is_wifi_hotspot_active", new_callable=AsyncMock, return_value=True)
def test_setup_page_and_what_it_needs_are_not_redirected(_hotspot, client):
    for path in ("/wifi-setup", "/admin/auth/status", "/static/favicon.svg"):
        response = client.get(path, headers={"host": "mirrordash.setup"}, follow_redirects=False)
        assert response.status_code == 200, path
    assert "const viaHotspot = true" in client.get("/wifi-setup", headers={"host": "mirrordash.setup"}).text


def test_hotspot_dns_answers_every_name_with_the_mirror():
    """The OS image must make NetworkManager's hotspot dnsmasq resolve everything to the mirror."""
    from pathlib import Path
    script = (Path(__file__).parent.parent / "scripts" / "configure_image.sh").read_text()
    assert "echo 'address=/#/10.42.0.1' > /etc/NetworkManager/dnsmasq-shared.d/mirrordash-captive.conf" in script


def run_wifi_check(tmp_path, saved_password=None, *args, online=False, hotspot=False, saved_wifi=False, phone=False,
                   traffic=True, chip=True, chip_comes_back=False):
    """Run the real mirrordash-wifi-check.sh (from configure_image.sh) offline, against a fake nmcli
    that keeps the hotspot profile's password in a file. args: "--watch". online: the network (or the
    saved Wi-Fi, once the hotspot lets go of the radio) answers. traffic: ping gets an answer (online but
    no traffic is the Pi 3's stuck Wi-Fi). chip=False: brcmfmac is loaded but there's no wlan0 (its firmware
    didn't load); chip_comes_back: reloading the driver brings wlan0. Returns (password, and the nmcli, curl,
    save-log, modprobe and systemctl calls)."""
    import os
    import subprocess

    from test_configure_image import unit
    body = unit("/usr/local/bin/mirrordash-wifi-check.sh")
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "wifi-check.sh"
    psk, log = tmp_path / "psk", tmp_path / "nmcli.log"
    bin_dir = tmp_path / "bin"
    script.write_text(body.replace("/var/lib/mirrordash-wifi-scan.cache", str(tmp_path / "scan.cache"))
                      .replace("/run/mirrordash-wifi-fails", str(tmp_path / "fails"))
                      .replace("/run/mirrordash-wifi-chip-logged", str(tmp_path / "chip-logged"))
                      .replace("/usr/local/lib/mirrordash/save-log", str(bin_dir / "save-log")))
    sys_dir = tmp_path / "sys"
    (sys_dir / "module" / "brcmfmac").mkdir(parents=True, exist_ok=True)
    (sys_dir / "class" / "net").mkdir(parents=True, exist_ok=True)
    if chip:
        (sys_dir / "class" / "net" / "wlan0").mkdir(exist_ok=True)
    if saved_password:
        psk.write_text(saved_password)
    bin_dir.mkdir(exist_ok=True)
    fakes = {
        "nm-online": f"exit {0 if online else 1}",
        "logger": "exit 0",
        "ip": ('[ "$1" = route ] && { echo "default via 192.168.1.1 dev wlan0 proto dhcp"; exit 0; }\n'
               + ("echo '10.42.0.23 lladdr 3a:00:00:00:00:01 REACHABLE'" if phone else "exit 0")),
        "ping": f"exit {0 if traffic else 1}",
        "save-log": f'echo "save-log $*" >> {log}',
        "modprobe": f'echo "modprobe $*" >> {log}' + (f'; [ "$1" = -r ] || mkdir -p {sys_dir}/class/net/wlan0' if chip_comes_back else ""),
        "udevadm": "exit 0",
        "systemctl": f'echo "systemctl $*" >> {log}',
        "curl": f'echo "curl $*" >> {log}',
        "nmcli": f"""echo "$*" >> {log}
case "$*" in
  "-t -f NAME connection show --active") {"echo MirrorDash-Setup" if hotspot else "true"} ;;
  "-t -f TIMESTAMP,TYPE,NAME connection show") echo 0:802-11-wireless:MirrorDash-Setup; {"echo 1791246478:802-11-wireless:Home" if saved_wifi else "true"} ;;
  "--wait 45 connection up id Home") exit {0 if online else 4} ;;
  "-s -g 802-11-wireless-security.psk connection show MirrorDash-Setup") [ -f {psk} ] && cat {psk} || exit 10 ;;
  "connection delete MirrorDash-Setup") rm -f {psk} ;;
  "connection modify MirrorDash-Setup wifi-sec.psk "*) printf %s "$5" > {psk} ;;
esac
exit 0""",
    }
    for name, text in fakes.items():
        (bin_dir / name).write_text(f"#!/bin/bash\n{text}\n")
        (bin_dir / name).chmod(0o755)
    subprocess.run(["bash", str(script), *args], check=True, env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                                 "MIRRORDASH_SYS": str(sys_dir),
                                                                 "MIRRORDASH_CHIP_REBOOT_FILE": str(tmp_path / "chip-restart")})
    return (psk.read_text() if psk.exists() else None), log.read_text().splitlines()


def test_first_hotspot_gets_its_own_password(tmp_path):
    import re
    password, calls = run_wifi_check(tmp_path)
    assert re.fullmatch(r"[abcdefghjkmnpqrstuvwxyz23456789]{10}", password)
    assert "connection modify MirrorDash-Setup connection.autoconnect no" in calls
    assert calls[-1] == "connection up MirrorDash-Setup"


def test_hotspot_keeps_its_password_across_starts(tmp_path):
    password, calls = run_wifi_check(tmp_path, saved_password="k7mxp2qrtw")
    assert password == "k7mxp2qrtw"
    assert not any(c.startswith(("connection add", "connection delete")) for c in calls)
    assert calls[-1] == "connection up MirrorDash-Setup"


def test_hotspot_from_an_old_image_replaces_the_shared_password(tmp_path):
    password, calls = run_wifi_check(tmp_path, saved_password="mirrordash")
    assert password != "mirrordash" and len(password) == 10
    assert "connection delete MirrorDash-Setup" in calls


def test_the_hotspot_goes_back_to_the_saved_wifi_when_it_returns(tmp_path):
    """After a power cut the router comes up after the mirror: the watch timer tries again."""
    _, calls = run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", hotspot=True, saved_wifi=True, online=True)
    # By name, after a scan: `device connect` brought the hotspot back up instead (the chip had no scan
    # results right after it), and nm-online took that for the saved Wi-Fi
    i = calls.index("connection down MirrorDash-Setup")
    assert calls.index("-t -f SSID device wifi list --rescan yes", i) < calls.index("--wait 45 connection up id Home", i)
    assert "connection up MirrorDash-Setup" not in calls and not any("device connect" in c for c in calls)
    assert calls[-1].startswith("curl") and "/api/wifi/changed" in calls[-1]


def test_the_hotspot_comes_back_when_the_saved_wifi_is_still_gone(tmp_path):
    _, calls = run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", hotspot=True, saved_wifi=True)
    assert calls[-2:] == ["connection up MirrorDash-Setup", calls[-1]] and calls[-1].startswith("curl")


def test_the_watch_leaves_the_hotspot_alone_during_setup(tmp_path):
    for case in ({"phone": True, "saved_wifi": True}, {"saved_wifi": False}):
        _, calls = run_wifi_check(tmp_path / str(len(case)), "k7mxp2qrtw", "--watch", hotspot=True, **case)
        assert "connection down MirrorDash-Setup" not in calls and not any(c.startswith("curl") for c in calls)


def test_the_watch_starts_the_hotspot_when_the_wifi_is_gone_for_good(tmp_path):
    _, calls = run_wifi_check(tmp_path / "online", "k7mxp2qrtw", "--watch", online=True)
    assert "connection up MirrorDash-Setup" not in calls
    _, calls = run_wifi_check(tmp_path / "offline", "k7mxp2qrtw", "--watch")
    assert calls[-2] == "connection up MirrorDash-Setup" and calls[-1].startswith("curl")


def test_only_the_mirror_itself_can_say_the_hotspot_changed(client):
    with patch("mirrordash_core.features.wifi.routes.manager.broadcast", AsyncMock()) as broadcast:
        assert client.post("/api/wifi/changed").status_code == 403  # TestClient's address is "testclient"
        with patch("starlette.requests.Request.client", new=type("C", (), {"host": "127.0.0.1"})()), \
             patch("mirrordash_core.admin._local_token", "t0ken"):
            assert client.post("/api/wifi/changed").status_code == 403  # a page on the screen has no token
            assert client.post("/api/wifi/changed", headers={"X-MirrorDash-Local": "t0ken"}).status_code == 200
    broadcast.assert_called_once_with({"action": "reload"})


def _nmcli_fake(calls, active="Home", saved=("Home",)):
    async def fake(*args, timeout=15):
        calls.append(args)
        if args[:5] == ("-t", "-f", "NAME,TYPE", "connection", "show"):
            names = [active] if "--active" in args else [*saved, "MirrorDash-Setup"]
            return 0, "\n".join(f"{n}:802-11-wireless" for n in names)
        return 0, ""
    return fake


def test_a_failed_wifi_change_goes_back_to_the_old_network():
    import asyncio

    from mirrordash_core.features.wifi import network
    calls = []
    with patch.object(network, "_nmcli", _nmcli_fake(calls)), \
         patch.object(network, "connect_wifi", AsyncMock(return_value=(False, "Secrets were required"))):
        asyncio.run(network.switch_wifi("Guest", "wrong"))
    assert ("connection", "delete", "id", "Guest") in calls  # the profile this attempt made
    assert ("connection", "up", "id", "Home") in calls
    assert network.failed_switch == {"ssid": "Guest", "reason": "Secrets were required", "previous": "Home"}

    # A network saved before keeps its profile; a working change clears the error
    calls.clear()
    with patch.object(network, "_nmcli", _nmcli_fake(calls, saved=("Home", "Guest"))), \
         patch.object(network, "connect_wifi", AsyncMock(return_value=(False, "timeout"))):
        asyncio.run(network.switch_wifi("Guest", "x"))
    assert not any(c[:2] == ("connection", "delete") for c in calls)
    with patch.object(network, "_nmcli", _nmcli_fake(calls)), \
         patch.object(network, "connect_wifi", AsyncMock(return_value=(True, "ok"))):
        asyncio.run(network.switch_wifi("Guest", "right"))
    assert network.failed_switch == {}


def test_wifi_card_and_change(client):
    from mirrordash_core.admin import require_api_key
    from mirrordash_core.features.wifi import network
    assert client.post("/admin/panels/wifi/connect", data={"ssid": "Guest"}).status_code in (401, 403)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        network.failed_switch.update(ssid="Guest", reason="Secrets were required", previous="Home")
        with patch("mirrordash_core.features.wifi.routes.get_wifi_info",
                   AsyncMock(return_value={"ssid": "Home", "signal": 70, "type": "wifi"})), \
             patch("mirrordash_core.features.wifi.routes.scan_wifi_networks", AsyncMock(return_value=["Home", "Guest"])):
            html = client.get("/admin/panels/wifi").text
        assert "Connected to <strong>Home</strong>" in html and '<option value="Guest">' in html
        assert "Couldn't join Guest (Secrets were required)" in html and "still on Home" in html

        assert client.post("/admin/panels/wifi/connect", data={"ssid": ""}).status_code == 400
        with patch("mirrordash_core.features.wifi.routes.switch_wifi", AsyncMock()):
            r = client.post("/admin/panels/wifi/connect", data={"ssid": "Guest", "password": "pw"})
        assert r.status_code == 200 and "switching to Guest" in r.headers["HX-Trigger-After-Swap"]
    finally:
        app.dependency_overrides.clear()
        network.failed_switch.clear()



def test_a_wifi_without_traffic_is_joined_again(tmp_path):
    """Joined (NetworkManager says online) but nothing gets through: after 2 watch rounds in a row the
    log is saved once and the Wi-Fi joined again; a round with traffic starts the count over."""
    run = lambda **kw: run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", online=True, saved_wifi=True, **kw)[1]
    log = tmp_path / "nmcli.log"
    assert not any("device" in c for c in run(traffic=False))  # one round can be a router restarting
    calls = run(traffic=False)
    assert calls[calls.index("save-log wifi-lost -b -n 3000"):] == [
        "save-log wifi-lost -b -n 3000", "device disconnect wlan0", "-t -f SSID device wifi list --rescan yes",
        "-t -f TIMESTAMP,TYPE,NAME connection show", "--wait 45 connection up id Home"]
    log.unlink()
    calls = run(traffic=False)  # still nothing: joined again, the log isn't saved twice
    assert "--wait 45 connection up id Home" in calls and not any(c.startswith("save-log") for c in calls)
    log.unlink()
    run(traffic=True)
    assert not (tmp_path / "fails").exists()
    assert not any("device" in c for c in run(traffic=False))


def test_a_wifi_chip_that_didnt_start_is_started_again(tmp_path):
    """brcmfmac loaded but no wlan0 (its firmware didn't load): the log is kept once and the driver loaded
    again; that's enough when wlan0 comes back, and nothing else happens."""
    _, calls = run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", chip=False, chip_comes_back=True)
    assert calls[0] == "save-log wifi-chip -b -n 3000"
    assert calls[1].startswith("modprobe -r") and calls[1].endswith("brcmfmac") and calls[2] == "modprobe brcmfmac"
    assert not any(c.startswith("systemctl") for c in calls)


def test_a_wifi_chip_that_stays_dead_restarts_the_mirror_at_most_once_an_hour(tmp_path):
    _, calls = run_wifi_check(tmp_path / "a", "k7mxp2qrtw", "--watch", chip=False)
    assert calls[-1] == "systemctl reboot"
    # Again within the hour (the stamp survives on /storage): no second restart
    restart = tmp_path / "a" / "chip-restart"
    assert restart.exists()
    (tmp_path / "a" / "nmcli.log").unlink()
    (tmp_path / "a" / "bin").rename(tmp_path / "a" / "bin-old")
    _, calls = run_wifi_check(tmp_path / "a", "k7mxp2qrtw", "--watch", chip=False)
    assert not any(c.startswith("systemctl") for c in calls)


def test_no_restart_for_the_wifi_chip_when_a_cable_is_connected(tmp_path):
    _, calls = run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", chip=False, online=True)
    assert "modprobe brcmfmac" in calls and not any(c.startswith("systemctl") for c in calls)


def test_a_working_wifi_chip_is_left_alone(tmp_path):
    """Every model with working Wi-Fi has wlan0: no driver reload, ever."""
    _, calls = run_wifi_check(tmp_path, "k7mxp2qrtw", "--watch", online=True)
    assert not any(c.startswith(("modprobe", "save-log wifi-chip")) for c in calls)

