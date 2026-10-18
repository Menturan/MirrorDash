"""A/B OS updates: the bootloader backend RAUC calls (rauc-bootloader in configure_image.sh) and the
admin page's choice of update bundle. The backend runs here against fake files instead of the Pi's
autoboot.txt and device tree; what the real firmware does was tested on a Pi 3B (tryboot spike)."""
import os
import subprocess
from pathlib import Path

import pytest

from mirrordash_core.features.updates import service
from test_configure_image import unit

BACKEND = unit("/usr/local/lib/mirrordash/rauc-bootloader")
AUTOBOOT = "[all]\ntryboot_a_b=1\nboot_partition=2\n[tryboot]\nboot_partition=3\n"
GPIO = ("# --- MirrorDash GPIO (managed by the admin page) ---\n[all]\n"
        "dtoverlay=gpio-key,gpio=17\n# --- end MirrorDash GPIO ---\n")


@pytest.fixture
def pi(tmp_path):
    """A fake mirror running slot A (boot partition 2), not on trial; returns run(*args)."""
    script = tmp_path / "rauc-bootloader"
    script.write_text(BACKEND)
    (tmp_path / "autoboot.txt").write_text(AUTOBOOT)
    dt = tmp_path / "dt"
    dt.mkdir()
    current = tmp_path / "current-boot"
    current.mkdir()
    (current / "cmdline.txt").write_text("console=tty3 root=PARTUUID=27b94958-05 rootfstype=squashfs "
                                         "cfg80211.ieee80211_regdom=SE quiet\n")
    (current / "config.txt").write_text("[all]\ndisable_splash=1\n" + GPIO)
    env = {**os.environ, "MIRRORDASH_AUTOBOOT": str(tmp_path / "autoboot.txt"), "MIRRORDASH_BOOTLOADER_DT": str(dt),
           "MIRRORDASH_CURRENT_BOOT": str(current), "MIRRORDASH_VCMAILBOX": "false"}  # a tryboot call would fail

    def run(*args, partition=2, tryboot=0):
        (dt / "partition").write_bytes(partition.to_bytes(4, "big"))
        (dt / "tryboot").write_bytes(tryboot.to_bytes(4, "big"))
        return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)

    run.autoboot = tmp_path / "autoboot.txt"
    run.tmp = tmp_path
    return run


def test_which_slot_boots_and_runs(pi):
    assert pi("get-primary").stdout == "A\n"
    assert pi("get-current", partition=3).stdout == "B\n"
    assert pi("get-state", "B").stdout == "good\n"
    assert pi("is-tryboot", tryboot=1).returncode == 0
    assert pi("is-tryboot").returncode == 1


def test_a_good_trial_boot_becomes_the_normal_one(pi):
    """mark-good on slot B while it runs on trial swaps the partitions in autoboot.txt."""
    assert pi("set-state", "B", "good", partition=3, tryboot=1).returncode == 0
    assert pi.autoboot.read_text() == "[all]\ntryboot_a_b=1\nboot_partition=3\n[tryboot]\nboot_partition=2\n"
    assert pi("get-primary").stdout == "B\n"
    assert not (pi.tmp / "autoboot.txt.tmp").exists()


def test_marking_the_normal_slot_good_changes_nothing(pi):
    assert pi("set-state", "A", "good").returncode == 0
    assert pi.autoboot.read_text() == AUTOBOOT


def test_the_slot_the_mirror_boots_is_never_overwritten(pi):
    """RAUC marks the slot it will write bad first; refusing that for the primary slot stops an
    install during an unfinished trial boot, which would leave nothing bootable if cut short."""
    refused = pi("set-state", "A", "bad", partition=3, tryboot=1)
    assert refused.returncode != 0 and "finish the trial boot" in refused.stderr
    assert pi("set-state", "B", "bad").returncode == 0


def test_set_primary_on_the_normal_slot_does_nothing(pi):
    """No tryboot call (it would fail here: MIRRORDASH_VCMAILBOX=false)."""
    assert pi("set-primary", "A").returncode == 0


def test_a_new_boot_partition_gets_this_mirrors_root_and_settings(pi):
    """The bundle's boot partition is built for slot A on no particular card: it gets its own root
    on this card, and the Wi-Fi country and GPIO block of the running slot."""
    new = pi.tmp / "new-boot"
    new.mkdir()
    (new / "cmdline.txt").write_text("console=tty3 root=PARTUUID=aaaaaaaa-05 rootfstype=squashfs "
                                     "cfg80211.ieee80211_regdom=US quiet\n")
    (new / "config.txt").write_text("[all]\ndisable_splash=1\ndtoverlay=vc4-kms-v3d\n")
    assert pi("prepare-boot", str(new), "6").returncode == 0
    assert (new / "cmdline.txt").read_text() == ("console=tty3 root=PARTUUID=27b94958-06 rootfstype=squashfs "
                                                 "cfg80211.ieee80211_regdom=SE quiet\n")
    assert (new / "config.txt").read_text() == "[all]\ndisable_splash=1\ndtoverlay=vc4-kms-v3d\n" + GPIO
    # Run again (an old block in the new partition is replaced, not doubled)
    assert pi("prepare-boot", str(new), "6").returncode == 0
    assert (new / "config.txt").read_text() == "[all]\ndisable_splash=1\ndtoverlay=vc4-kms-v3d\n" + GPIO


def test_gpio_markers_match_the_gpio_helper():
    helper = unit("/usr/local/bin/mirrordash-gpio-overlays")
    for marker in ("# --- MirrorDash GPIO (managed by the admin page) ---", "# --- end MirrorDash GPIO ---"):
        assert marker in helper and marker in BACKEND


def test_a_failed_trial_boot_restarts_into_the_old_slot():
    """mirrordash-mark-good: only on a trial boot; network, then the app, then mark-good, and a
    restart (the old slot) when any of them fails."""
    mark_good = unit("/etc/systemd/system/mirrordash-mark-good.service")
    assert "ExecCondition=/usr/local/lib/mirrordash/rauc-bootloader is-tryboot" in mark_good
    assert "FailureAction=reboot" in mark_good
    starts = [line for line in mark_good.splitlines() if line.startswith("ExecStart=")]
    assert [s.split()[0] for s in starts] == ["ExecStart=/usr/bin/nm-online", "ExecStart=/usr/bin/curl",
                                              "ExecStart=/usr/bin/rauc"]


def bundle(name, size=800_000_000):
    return {"name": name, "browser_download_url": f"https://example.com/{name}", "size": size}


RELEASES = [
    {"tag_name": "v0.7.1rc1", "prerelease": True, "assets": [bundle("mirrordash-os-v0.7.1rc1-20261201-0900.raucb")]},
    {"tag_name": "v0.7.0-os1", "prerelease": False, "assets": [bundle("mirrordash-os-v0.7.0.img.xz"),
                                                               bundle("mirrordash-os-v0.7.0-20261101-1200.raucb")]},
    {"tag_name": "v0.6.9", "prerelease": False, "assets": []},
    {"tag_name": "v0.8.0", "draft": True, "assets": [bundle("mirrordash-os-v0.8.0-20270101-0000.raucb")]},
]


def test_the_newest_bundle_is_offered():
    assert service.newest_os_bundle(RELEASES, prerelease=False)["build"] == "20261101-1200"
    assert service.newest_os_bundle(RELEASES, prerelease=True)["tag"] == "v0.7.1rc1"
    assert service.newest_os_bundle([], prerelease=True) is None


def test_failed_os_update_is_reported_once(tmp_path, monkeypatch):
    pending = tmp_path / "pending"
    monkeypatch.setattr(service, "OS_PENDING_FILE", pending)
    monkeypatch.setattr(service, "TRYBOOT_FLAG", tmp_path / "tryboot")
    # Back on the old build: reported, then forgotten
    pending.write_text("20261101-1200")
    assert service.failed_os_update("20261001-0800") == "20261101-1200"
    assert service.failed_os_update("20261001-0800") is None
    # On the new build, still on trial: nothing decided yet
    pending.write_text("20261101-1200")
    (tmp_path / "tryboot").write_bytes((1).to_bytes(4, "big"))
    assert service.failed_os_update("20261101-1200") is None and pending.exists()
    # On the new build after the trial: it stuck
    (tmp_path / "tryboot").write_bytes((0).to_bytes(4, "big"))
    assert service.failed_os_update("20261101-1200") is None and not pending.exists()


def test_no_os_updates_without_an_ab_image(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(service, "OS_BUILD_FILE", tmp_path / "missing")
    assert asyncio.run(service.check_os_update()) == {"supported": False}
