"""Hardening of the OS image (sudo, polkit, root helpers) and the failures found in the audit."""
import asyncio
import subprocess

from test_configure_image import SETUP, unit

from mirrordash_core.config import load_config
from mirrordash_core.features.updates import service


def test_pi_has_no_blanket_sudo():
    assert "rm -f /etc/sudoers.d/010_pi-nopasswd" in SETUP and "gpasswd -d pi sudo" in SETUP


def test_sudo_runs_the_wifi_country_wrapper_not_raspi_config(tmp_path):
    assert "raspi-config" not in unit("/etc/sudoers.d/mirrordash")
    assert "NOPASSWD: /usr/local/bin/mirrordash-wifi-country *" in unit("/etc/sudoers.d/mirrordash")
    script = tmp_path / "wifi-country"
    script.write_text(unit("/usr/local/bin/mirrordash-wifi-country"))
    for bad in ("", "se", "SEX", "S;"):
        assert subprocess.run(["bash", str(script), bad]).returncode == 2


def test_polkit_names_the_actions_it_allows():
    rules = unit("/etc/polkit-1/rules.d/50-mirrordash-networkmanager.rules")
    assert 'indexOf("org.freedesktop.NetworkManager.") == 0' not in rules
    assert "NetworkManager.settings.modify.system" in rules


def test_failed_os_update_makes_the_nightly_update_skip_that_build(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "OS_PENDING_FILE", tmp_path / "pending")
    monkeypatch.setattr(service, "TRYBOOT_FLAG", tmp_path / "tryboot")
    (tmp_path / "pending").write_text("20261101-1200")
    assert service.failed_os_update("20261001-0800") == "20261101-1200"
    assert load_config()["system"]["auto_update_skip_build"] == "20261101-1200"


def test_wifi_country_failure_is_not_a_time_zone_failure(monkeypatch):
    from mirrordash_core import host

    class Proc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*a, **k):
        return Proc()

    async def boom(country):
        raise RuntimeError("no raspi-config")

    monkeypatch.setattr(host.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(host, "apply_wifi_country", boom)
    assert asyncio.run(host.apply_system_timezone("Europe/Stockholm")) is True
