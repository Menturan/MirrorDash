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


def test_wifi_country_writes_cmdline_in_place(tmp_path):
    """The boot slot's cmdline.txt keeps its length (the image ships regdom=00), so the country is written
    over in place: the same file (inode), no new one moved over it, which on FAT can leave none at all
    after a power cut. One of another length is replaced; one without root= is never written."""
    cmdline = tmp_path / "cmdline.txt"
    script = tmp_path / "wifi-country"
    script.write_text(unit("/usr/local/bin/mirrordash-wifi-country").replace("CMDLINE=/boot/firmware/cmdline.txt",
                                                                           f"CMDLINE={cmdline}"))
    env = {"PATH": "/usr/bin:/bin"}
    def run(country):
        return subprocess.run(["bash", str(script), country], env=env, capture_output=True).returncode

    cmdline.write_text("console=tty1 root=PARTUUID=abcd-05 cfg80211.ieee80211_regdom=00 rootwait\n")
    inode = cmdline.stat().st_ino
    assert run("SE") == 0
    assert cmdline.read_text() == "console=tty1 root=PARTUUID=abcd-05 rootwait cfg80211.ieee80211_regdom=SE\n"
    assert run("NO") == 0  # replaced, never a second one
    assert cmdline.read_text() == "console=tty1 root=PARTUUID=abcd-05 rootwait cfg80211.ieee80211_regdom=NO\n"
    assert cmdline.stat().st_ino == inode  # written over in place, both times

    cmdline.write_text("console=tty1 root=PARTUUID=abcd-05 rootwait\n")  # no country yet: another length
    assert run("SE") == 0
    assert cmdline.read_text() == "console=tty1 root=PARTUUID=abcd-05 rootwait cfg80211.ieee80211_regdom=SE\n"
    assert not (tmp_path / "cmdline.txt.mirrordash-tmp").exists()

    cmdline.write_text("")  # already broken: left alone
    assert run("SE") == 1
    assert cmdline.read_text() == "" and not (tmp_path / "cmdline.txt.mirrordash-tmp").exists()


def test_the_image_ships_what_keeps_boot_writes_in_place():
    """cmdline.txt with a country (00, the world's rules) and config.txt with an empty GPIO block: both
    keep their length from the first save on (mirrordash-wifi-country, mirrordash-gpio-overlays)."""
    assert 'new_opts+=("cfg80211.ieee80211_regdom=00")' in SETUP
    watchdog_step = SETUP.split("step_watchdog_boot_optimization() {", 1)[1].split("\n}\n", 1)[0]
    hardening = watchdog_step.index("dtoverlay=disable-bt")
    assert watchdog_step.index("\n  /usr/local/bin/mirrordash-gpio-overlays\n") > hardening  # the block last in the file


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
    assert home <= set(words(unit("/etc/systemd/system/screen-kiosk.service"), "IPAddressDeny"))


def test_polkit_names_the_actions_it_allows():
    rules = unit("/etc/polkit-1/rules.d/50-mirrordash-networkmanager.rules")
    assert 'indexOf("org.freedesktop.NetworkManager.") == 0' not in rules
    assert "NetworkManager.settings.modify.system" in rules


def test_failed_os_update_is_counted_for_the_nightly_update(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "OS_PENDING_FILE", tmp_path / "pending")
    monkeypatch.setattr(service, "TRYBOOT_FLAG", tmp_path / "tryboot")
    # The tests share one config file: count from what an earlier failure of this build left there
    before = load_config().get("system", {}).get("auto_update_failed") or {}
    times = before.get("times", 0) if before.get("build") == "20261101-1200" else 0
    (tmp_path / "pending").write_text("20261101-1200")
    assert service.failed_os_update("20261001-0800") == "20261101-1200"
    assert load_config()["system"]["auto_update_failed"] == {"build": "20261101-1200", "times": times + 1}


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
