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


def test_ip_filters_name_only_what_systemd_knows():
    """systemd skips a word it doesn't know in IPAddressDeny= with a mere warning ("localnet" left the
    module host on the home network and the core's loopback routes), so every word must be one of its
    names or an address; and the module host and the screen must keep the home network out."""
    import ipaddress
    from pathlib import Path

    from test_configure_image import ROOTFS

    def words(text, key):
        return [w for line in text.splitlines() if line.startswith(key + "=") for w in line.split("=", 1)[1].split()]

    for path in Path(ROOTFS, "etc/systemd/system").glob("*.service"):
        text = path.read_text()
        for word in words(text, "IPAddressDeny") + words(text, "IPAddressAllow"):
            assert word in ("any", "localhost", "link-local", "multicast") or ipaddress.ip_network(word, strict=False), \
                f"{path.name}: {word}"
    home = {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "link-local", "::/0"}
    assert home | {"localhost"} <= set(words(unit("/etc/systemd/system/mirrordash-modules.service"), "IPAddressDeny"))
    assert home <= set(words(unit("/etc/systemd/system/cog-kiosk.service"), "IPAddressDeny"))


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

        async def communicate(self, input=None):
            return b"", b""

    async def fake_exec(*a, **k):
        return Proc()

    async def boom(country):
        raise RuntimeError("no raspi-config")

    monkeypatch.setattr(host.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(host, "apply_wifi_country", boom)
    assert asyncio.run(host.apply_system_timezone("Europe/Stockholm")) is True
