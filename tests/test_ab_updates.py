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
           "MIRRORDASH_CURRENT_BOOT": str(current), "MIRRORDASH_VCMAILBOX": "false",  # a tryboot call would fail
           "MIRRORDASH_RAUC_STATUS": str(tmp_path / "central.raucs")}

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


def test_only_the_trial_of_the_slot_activated_last_is_kept(pi):
    """mark-good runs on the trial boot of the slot RAUC activated last (its own central.raucs), not on
    a trial started by hand, which would keep an older slot."""
    status = pi.tmp / "central.raucs"
    def raucs(**slots):
        status.write_text("".join(f"[slot.rootfs.{n}]\nstatus=ok\nactivated.timestamp={t}\n\n[slot.boot.{n}]\nstatus=ok\n\n"
                                  for n, t in slots.items()))
    assert pi("is-update-trial", partition=3, tryboot=1).returncode == 1  # no record at all
    raucs(**{"1": "2026-10-18T22:56:32Z"})  # B installed and activated; A came with the card
    assert pi("is-update-trial", partition=3, tryboot=1).returncode == 0
    assert pi("is-update-trial", partition=2, tryboot=1).returncode == 1  # A tried by hand: never activated
    raucs(**{"0": "2026-10-19T10:00:00Z", "1": "2026-10-18T22:56:32Z"})  # later A was installed
    assert pi("is-update-trial", partition=2, tryboot=1).returncode == 0
    assert pi("is-update-trial", partition=3, tryboot=1).returncode == 1  # B is the older one now
    assert pi("is-update-trial", partition=2).returncode == 1  # not a trial boot

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
    """mirrordash-mark-good: only on a trial boot; network, then the app, then mark-good. When any
    of them fails, the trial boot's log is saved and the mirror restarts (into the old slot)."""
    mark_good = unit("/etc/systemd/system/mirrordash-mark-good.service")
    assert "ExecCondition=/usr/local/lib/mirrordash/rauc-bootloader is-update-trial" in mark_good
    assert "OnFailure=mirrordash-save-trial-log.service" in mark_good
    save = unit("/etc/systemd/system/mirrordash-save-trial-log.service")
    assert "FailureAction=reboot" in save
    assert [line for line in save.splitlines() if line.startswith("ExecStart=")] == [
        "ExecStart=-/usr/local/lib/mirrordash/save-log os-trial -b", "ExecStart=/usr/bin/systemctl reboot"]
    starts = [line for line in mark_good.splitlines() if line.startswith("ExecStart=")]
    assert [s.split()[0] for s in starts] == ["ExecStart=/usr/bin/nm-online", "ExecStart=/usr/bin/curl",
                                              "ExecStart=/usr/bin/rauc", "ExecStart=-/bin/sh"]
    # Renewing p1 comes last, after mark-good, and can't fail the unit (the "-")
    assert "/usr/local/lib/mirrordash/update-p1 || /usr/local/lib/mirrordash/save-log p1-update" in starts[-1]


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


# --- Logs saved when an update fails (save-log, features/logs/saved.py) ---

@pytest.fixture
def save_log(tmp_path):
    """Runs the real save-log with a fake journalctl; returns (run, log_dir)."""
    script = tmp_path / "save-log"
    script.write_text(unit("/usr/local/lib/mirrordash/save-log"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "journalctl").write_text('#!/bin/sh\necho "journal $*"\n')
    (bin_dir / "journalctl").chmod(0o755)
    log_dir = tmp_path / "logs"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MIRRORDASH_LOG_DIR": str(log_dir)}

    def run(*args):
        return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)
    return run, log_dir


def test_a_saved_log_holds_the_journal(save_log):
    run, log_dir = save_log
    result = run("os-trial", "-b")
    assert result.returncode == 0
    saved = Path(result.stdout.strip())
    assert saved.parent == log_dir and saved.name.startswith("os-trial-") and saved.suffix == ".log"
    assert saved.read_text() == "journal --no-pager --utc -o short-iso -b\n"
    assert not list(log_dir.glob("*.part"))


def test_only_the_newest_five_saved_logs_are_kept(save_log):
    run, log_dir = save_log
    log_dir.mkdir()
    for i in range(6):
        old = log_dir / f"app-rollback-2026010{i}-000000.log"
        old.write_text("old")
        os.utime(old, (1_000_000 + i, 1_000_000 + i))
    run("os-install", "-u", "rauc")
    names = sorted(p.name for p in log_dir.glob("*.log"))
    assert len(names) == 5
    assert "app-rollback-20260100-000000.log" not in names and "app-rollback-20260101-000000.log" not in names
    assert any(n.startswith("os-install-") for n in names)


def test_save_log_refuses_odd_names(save_log):
    run, log_dir = save_log
    assert run("../etc").returncode == 2
    assert not log_dir.exists()


def test_saved_logs_in_the_admin_page(tmp_path, monkeypatch):
    """Listed newest first, downloaded and deleted by name; nothing outside the folder is reachable."""
    from fastapi import HTTPException
    from mirrordash_core.features.logs import saved
    monkeypatch.setattr(saved, "SAVED_LOG_DIR", tmp_path)
    (tmp_path / "os-trial-20261018-1200.log").write_text("a")
    (tmp_path / "app-rollback-20261019-1200.log").write_text("b")
    os.utime(tmp_path / "os-trial-20261018-1200.log", (1_000_000, 1_000_000))
    (tmp_path.parent / "secret.log").write_text("not yours")

    logs = saved.saved_logs()
    assert [log["filename"] for log in logs] == ["app-rollback-20261019-1200.log", "os-trial-20261018-1200.log"]
    assert logs[1]["kind"].startswith("OS update")
    with pytest.raises(HTTPException):
        saved.saved_log_file("../secret.log")
    saved.delete_saved_logs("os-trial-20261018-1200.log")
    assert [log["filename"] for log in saved.saved_logs()] == ["app-rollback-20261019-1200.log"]
    saved.delete_saved_logs()
    assert saved.saved_logs() == [] and (tmp_path.parent / "secret.log").exists()


# --- Renewing p1 (update-p1) ---

@pytest.fixture
def p1(tmp_path):
    """The real update-p1 with fake disk tools: p1 starts where p1.start says; returns run(*args) -> (rc, calls)."""
    script = tmp_path / "update-p1"
    script.write_text(unit("/usr/local/lib/mirrordash/update-p1"))
    firmware, autoboot, new = tmp_path / "firmware", tmp_path / "autoboot", tmp_path / "new"
    for d in (firmware, autoboot):
        d.mkdir()
        for name in ("bootcode.bin", "start.elf", "fixup.dat"):
            (d / name).write_bytes(b"same " + name.encode())
    (autoboot / "autoboot.txt").write_text(AUTOBOOT.replace("boot_partition=2\n[tryboot]", "boot_partition=3\n[tryboot]"))
    state, log, bin_dir = tmp_path / "start", tmp_path / "calls.log", tmp_path / "bin"
    state.write_text("8192")
    bin_dir.mkdir()
    fakes = {
        "sfdisk": f"""echo "sfdisk $*" >> {log}
if [ "$1" = --dump ]; then echo "/dev/fake-disk : x"; echo "/dev/fake-diskp1 : start=        $(cat {state}), size=       65536, type=c"; exit 0; fi
read -r line; echo "${{line%%,*}}" > {state}; echo "sfdisk input $line" >> {log}""",
        "losetup": f'echo "losetup $*" >> {log}; [ "$1" = -d ] || echo /dev/loop9',
        "mkfs.vfat": f'echo "mkfs.vfat $*" >> {log}',
        "mount": f'echo "mount $*" >> {log}',
        "umount": f'echo "umount $*" >> {log}',
        "partx": f'echo "partx $*" >> {log}',
        "udevadm": "exit 0",
    }
    for name, text in fakes.items():
        (bin_dir / name).write_text(f"#!/bin/bash\n{text}\n")
        (bin_dir / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MIRRORDASH_DISK": "/dev/fake-disk",
           "MIRRORDASH_FIRMWARE_DIR": str(firmware), "MIRRORDASH_AUTOBOOT_DIR": str(autoboot),
           "MIRRORDASH_P1_NEW_DIR": str(new)}

    def run(*args):
        result = subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)
        return result.returncode, (log.read_text().splitlines() if log.exists() else [])
    run.firmware, run.autoboot, run.new, run.state, run.bin = firmware, autoboot, new, state, bin_dir
    return run


def test_p1_is_left_alone_when_its_firmware_is_current(p1):
    rc, calls = p1()
    assert rc == 0 and calls == []


def test_newer_firmware_builds_a_new_p1_in_the_other_half_and_switches(p1):
    (p1.firmware / "start.elf").write_bytes(b"newer start.elf")
    rc, calls = p1()
    assert rc == 0
    assert "losetup --find --show --offset 37748736 --sizelimit 33554432 /dev/fake-disk" in calls  # 73728 * 512
    # The new p1 has the new firmware and the autoboot.txt as it was (slot B primary stays primary)
    assert (p1.new / "start.elf").read_bytes() == b"newer start.elf"
    assert (p1.new / "autoboot.txt").read_text() == (p1.autoboot / "autoboot.txt").read_text()
    switch = calls.index("sfdisk input 73728,65536,c")
    assert calls[switch - 2] == f"umount {p1.autoboot}"  # p1 let go of first, then the one MBR write
    assert calls[switch + 1:] == ["partx --delete --nr 1 /dev/fake-disk", "partx --add --nr 1 /dev/fake-disk",
                                  f"mount {p1.autoboot}"]
    assert p1.state.read_text().strip() == "73728"
    # And back to the first half the next time
    rc, calls = p1("--force")
    assert rc == 0 and p1.state.read_text().strip() == "8192"


def test_p1_outside_its_reserved_place_is_never_touched(p1):
    p1.state.write_text("10000")
    rc, calls = p1("--force")
    assert rc == 1 and not any(c.startswith(("losetup", "sfdisk input", "umount")) for c in calls)


def test_a_bad_copy_switches_nothing(p1):
    """The new p1 is checked file by file before the MBR points at it."""
    (p1.firmware / "start.elf").write_bytes(b"newer start.elf")
    (p1.bin / "sync").write_text(f"#!/bin/bash\n[ -f {p1.new}/start.elf ] && echo broken > {p1.new}/start.elf\nexit 0\n")
    (p1.bin / "sync").chmod(0o755)
    rc, calls = p1()
    assert rc == 1 and not any(c.startswith("sfdisk input") for c in calls)
    assert p1.state.read_text().strip() == "8192"
    assert "losetup -d /dev/loop9" in calls  # cleaned up

