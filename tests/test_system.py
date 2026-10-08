import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from mirrordash_core.system import (
    is_root_read_only,
    remount_rw,
    remount_ro,
    reboot_system,
    poweroff_system,
    apply_system_timezone,
    get_available_resolutions,
    apply_system_settings,
    set_screen_power,
    scan_wifi_networks,
    connect_wifi,
    get_ssh_status,
    set_ssh_status,
)

def test_is_root_read_only_proc_mounts():
    mock_data = "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n/dev/mmcblk0p2 / ext4 ro,noatime,commit=60 0 1\n"
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: mock_data, __iter__=lambda s: iter(mock_data.splitlines())))):
        assert is_root_read_only() is True

def test_is_root_read_only_proc_mounts_rw():
    mock_data = "sysfs /sys sysfs rw,nosuid,nodev,noexec,relatime 0 0\n/dev/mmcblk0p2 / ext4 rw,noatime,commit=60 0 1\n"
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: mock_data, __iter__=lambda s: iter(mock_data.splitlines())))):
        assert is_root_read_only() is False

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_remount_rw_success(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc
    
    with patch("mirrordash_core.system.os.is_root_read_only", return_value=True):
        from mirrordash_core.system.os import _originally_read_only
        with patch("mirrordash_core.system.os._originally_read_only", None):
            res = await remount_rw()
            assert res is True
            mock_subproc.assert_called_once_with(
                "sudo", "-n", "mount", "-o", "remount,rw", "/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_remount_ro_success(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc
    
    with patch("mirrordash_core.system.os.is_root_read_only", return_value=True):
        with patch("mirrordash_core.system.os._originally_read_only", True):
            res = await remount_ro()
            assert res is True
            mock_subproc.assert_called_once_with(
                "sudo", "-n", "mount", "-o", "remount,ro", "/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
@pytest.mark.parametrize("func, command", [(reboot_system, "reboot"), (poweroff_system, "poweroff")])
async def test_reboot_and_poweroff_system(mock_subproc, func, command):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_subproc.return_value = mock_proc

    await func(delay_sec=0.001)
    # Wait briefly for scheduled task to run
    await asyncio.sleep(0.01)
    # -n: fail instead of hanging on a password prompt if the sudoers rule is missing
    mock_subproc.assert_called_once_with(
        "sudo", "-n", command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_apply_system_timezone(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc
    
    res = await apply_system_timezone("Europe/Stockholm")
    assert res is True
    mock_subproc.assert_called_once_with(
        "sudo", "-n", "timedatectl", "set-timezone", "Europe/Stockholm",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_available_resolutions(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    # Simulate xrandr output containing resolutions
    mock_proc.communicate.return_value = (b"Screen 0: minimum 320 x 200, current 1920 x 1080\nHDMI-1 connected primary\n  1920x1080     60.00*\n  1280x720      60.00\n", b"")
    mock_subproc.return_value = mock_proc
    
    res = await get_available_resolutions()
    assert "1920x1080" in res
    assert "1280x720" in res
    assert res[0] == "auto"

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_set_screen_power_on(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc
    
    await set_screen_power(True)
    # Checks that it tried at least one screen power command (e.g. wlr-randr or xset or xrandr)
    assert mock_subproc.called

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_scan_wifi_networks(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"HomeWiFi\nGuestWiFi\nHomeWiFi\n", b"")
    mock_subproc.return_value = mock_proc
    
    res = await scan_wifi_networks()
    assert res == ["HomeWiFi", "GuestWiFi"]

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
@patch("mirrordash_core.system.network.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.system.network.remount_ro", new_callable=AsyncMock)
async def test_connect_wifi_success(mock_ro, mock_rw, mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"Connection successfully activated", b"")
    mock_subproc.return_value = mock_proc
    
    success, msg = await connect_wifi("MySSID", "MyPassword")
    assert success is True
    assert "successfully activated" in msg
    mock_rw.assert_called_once()
    mock_ro.assert_called_once()
    # verify password was passed in the command line (headless nmcli support)
    assert mock_subproc.call_count >= 1
    # The last call should be the actual connection command containing the password
    args = mock_subproc.call_args_list[-1][0]
    assert "MyPassword" in args
    mock_proc.communicate.assert_called_with()

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_get_ssh_status(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"active\n", b"")
    mock_subproc.return_value = mock_proc
    
    status = await get_ssh_status()
    assert status is True

@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
@patch("mirrordash_core.system.network.remount_rw", new_callable=AsyncMock)
@patch("mirrordash_core.system.network.remount_ro", new_callable=AsyncMock)
async def test_set_ssh_status(mock_ro, mock_rw, mock_subproc):
    mock_proc = AsyncMock()
    mock_subproc.return_value = mock_proc
    
    res = await set_ssh_status(True)
    assert res is True
    mock_rw.assert_called_once()
    mock_ro.assert_called_once()
    assert mock_subproc.call_count == 2


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_apply_system_password_hash(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc

    from mirrordash_core.system.os import apply_system_password_hash
    res = await apply_system_password_hash("somehash")
    assert res is True
    mock_subproc.assert_called_once_with(
        "sudo", "-n", "chpasswd", "-e",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    mock_proc.communicate.assert_called_once_with(input=b"pi:somehash\n")



def test_hdmi_brightness_goes_over_ddc(monkeypatch):
    """No backlight device (HDMI): the brightness goes to the screen over DDC/CI, and a screen
    that doesn't answer is reported as unsupported."""
    from mirrordash_core.system import apply_brightness, display
    monkeypatch.setattr(display, "brightness_supported", None)  # restored for the other tests

    def ddc(returncode):
        proc = MagicMock(returncode=returncode)
        proc.communicate = AsyncMock(return_value=(b"", b"No monitor detected"))
        return AsyncMock(return_value=proc)

    with patch("mirrordash_core.system.display.glob.glob", return_value=[]):
        with patch("asyncio.create_subprocess_exec", ddc(0)) as run:
            assert asyncio.run(apply_brightness(40)) is True
        run.assert_awaited_once()
        assert run.await_args.args == ("sudo", "-n", "ddcutil", "--noverify", "setvcp", "10", "40")
        assert display.brightness_supported is True
        with patch("asyncio.create_subprocess_exec", ddc(1)):
            assert asyncio.run(apply_brightness(40)) is False
        assert display.brightness_supported is False


def test_wifi_country_follows_the_time_zone(tmp_path, monkeypatch):
    """The time zone (sent by the phone during Wi-Fi setup) picks the Wi-Fi country; cmdline.txt is
    only rewritten when the country changes."""
    from mirrordash_core.system import os as system_os
    (tmp_path / "zone.tab").write_text("# comment\nSE\t+5920+01803\tEurope/Stockholm\nUS\t+404251-0740023\tAmerica/New_York\tEastern\n")
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text("overlayroot=tmpfs:recurse=0 root=PARTUUID=x rootwait cfg80211.ieee80211_regdom=US console=tty3\n")
    monkeypatch.setattr(system_os, "ZONE_TAB", str(tmp_path / "zone.tab"))
    monkeypatch.setattr(system_os, "CMDLINE", str(cmdline))

    assert system_os.wifi_country_for("Europe/Stockholm") == "SE"
    assert system_os.wifi_country_for("America/New_York") == "US"
    assert system_os.wifi_country_for("UTC") is None

    proc = MagicMock(returncode=0)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)) as run:
        assert asyncio.run(system_os.apply_wifi_country("US")) is True  # already set: nothing written
        assert asyncio.run(system_os.apply_wifi_country(None)) is False
        assert asyncio.run(system_os.apply_wifi_country("S;")) is False
        run.assert_not_awaited()
        assert asyncio.run(system_os.apply_wifi_country("SE")) is True
    assert run.await_args.args == ("sudo", "-n", "raspi-config", "nonint", "do_wifi_country", "SE")
