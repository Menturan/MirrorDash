import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mirrordash_core.features.hardware.display import (
    get_available_resolutions,
    set_screen_power,
)
from mirrordash_core.features.settings.ssh import get_ssh_status, set_ssh_status
from mirrordash_core.features.wifi.network import connect_wifi, scan_wifi_networks
from mirrordash_core.host import apply_system_timezone, poweroff_system, reboot_system


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
async def test_connect_wifi_success(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"Connection successfully activated", b"")
    mock_subproc.return_value = mock_proc
    
    success, msg = await connect_wifi("MySSID", "MyPassword")
    assert success is True
    assert "successfully activated" in msg
    # verify password was passed in the command line (headless nmcli support)
    assert mock_subproc.call_count >= 1
    # The last call should be the actual connection command containing the password
    args = mock_subproc.call_args_list[-1][0]
    assert "MyPassword" in args
    mock_proc.communicate.assert_called_with(input=None)

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
async def test_set_ssh_status_only_starts_ssh_with_the_owners_password(mock_subproc, tmp_path, monkeypatch):
    """A stray `ssh: true` (once saved by any settings change) must not start SSH while pi has no password
    of the owner's: SSH starts only once its hash is on /storage."""
    from mirrordash_core.features.settings import ssh
    monkeypatch.setattr(ssh, "PASSWORD_HASH_FILE", str(tmp_path / "pi_password.hash"))
    mock_subproc.return_value = AsyncMock(wait=AsyncMock(return_value=0))
    actions = lambda: [c.args[3] for c in mock_subproc.call_args_list]

    assert await set_ssh_status(True) is True
    assert actions() == ["disable", "stop"]

    mock_subproc.reset_mock()
    (tmp_path / "pi_password.hash").write_text("$6$salt$hash")
    assert await set_ssh_status(True) is True
    assert actions() == ["enable", "start"]


def test_saving_another_setting_doesnt_turn_ssh_on():
    """Home Assistant's brightness or the Power tab sends only its own setting; the rest are filled in
    from the defaults, and SSH's must be off."""
    from mirrordash_core import system_settings
    saved = {}
    with patch.object(system_settings, "load_config", return_value={"system": {}}), \
         patch.object(system_settings, "save_config", side_effect=saved.update), \
         patch.object(system_settings, "apply_ssh", new_callable=AsyncMock) as apply_ssh:
        asyncio.run(system_settings.update_system_settings(settings={"display_control": {"mode": "manual"}}))
    assert saved["system"]["ssh"] is False and not apply_ssh.called


@pytest.mark.asyncio
@patch("asyncio.create_subprocess_exec")
async def test_apply_system_password_hash(mock_subproc):
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = (b"", b"")
    mock_subproc.return_value = mock_proc

    from mirrordash_core.host import apply_system_password_hash
    res = await apply_system_password_hash("somehash")
    assert res is True
    mock_subproc.assert_called_once_with(
        "sudo", "-n", "/usr/local/bin/mirrordash-pi-password",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    mock_proc.communicate.assert_called_once_with(input=b"somehash\n")



def test_hdmi_brightness_goes_over_ddc(monkeypatch):
    """No backlight device (HDMI): the brightness goes to the screen over DDC/CI, and a screen
    that doesn't answer is reported as unsupported."""
    from mirrordash_core.features.hardware import display
    from mirrordash_core.features.hardware.display import apply_brightness
    monkeypatch.setattr(display, "brightness_supported", None)  # restored for the other tests

    def ddc(returncode):
        proc = MagicMock(returncode=returncode)
        proc.communicate = AsyncMock(return_value=(b"", b"No monitor detected"))
        return AsyncMock(return_value=proc)

    with patch("mirrordash_core.features.hardware.display.glob.glob", return_value=[]):
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
    from mirrordash_core import host as system_os
    (tmp_path / "zone.tab").write_text("# comment\nSE\t+5920+01803\tEurope/Stockholm\nUS\t+404251-0740023\tAmerica/New_York\tEastern\n")
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text("overlayroot=tmpfs:recurse=0 root=PARTUUID=x rootwait cfg80211.ieee80211_regdom=US console=tty3\n")
    monkeypatch.setattr(system_os, "ZONE_TAB", str(tmp_path / "zone.tab"))
    monkeypatch.setattr(system_os, "KERNEL_COMMAND_LINE_FILE", str(cmdline))

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
    assert run.await_args.args == ("sudo", "-n", "/usr/local/bin/mirrordash-wifi-country", "SE")


def test_run_command_never_raises():
    """A missing program and one that hangs come back as the shell's exit codes, not as exceptions."""
    from mirrordash_core.host import EXIT_CODE_PROGRAM_NOT_FOUND, EXIT_CODE_TIMED_OUT, run_command
    assert asyncio.run(run_command("no-such-program-anywhere"))[0] == EXIT_CODE_PROGRAM_NOT_FOUND
    assert asyncio.run(run_command("sleep", "5", timeout=0.1))[0] == EXIT_CODE_TIMED_OUT
    assert asyncio.run(run_command("cat", input_text="hello")) == (0, "hello", "")


def test_nmcli_escapes_are_undone():
    """nmcli -t writes a ':' or '\\' inside a field as '\\:' or '\\\\': an SSID like "Home:5G" stays whole."""
    from mirrordash_core.features.wifi.network import split_nmcli_terse_line, unescape_nmcli_value
    assert split_nmcli_terse_line(r"yes:Home\:5G:80") == ["yes", "Home:5G", "80"]
    assert unescape_nmcli_value(r"back\\slash\:colon") == "back\\slash:colon"


@pytest.mark.parametrize("throttled, undervoltage", [("0x0", False), ("0x1", True), ("0x10000", True),
                                                      ("0x4", False), ("0x40000", False), ("0x50005", True)])
def test_only_the_undervoltage_bits_warn_about_the_power_supply(throttled, undervoltage):
    """Throttling (bits 2 and 18) also comes from heat: only bits 0 and 16 are about the supply."""
    from mirrordash_core.features.dashboard import telemetry
    with patch.object(telemetry, "run_command", AsyncMock(return_value=(0, f"throttled={throttled}\n", ""))):
        assert asyncio.run(telemetry.get_undervoltage_detected()) is undervoltage
