"""How configure_image.sh and the files it installs (scripts/image/rootfs/) lock the mirror. Nothing here can run off a Pi, so this guards the lines
that once made /storage a RAM overlay (lost at every reboot) and the Wi-Fi password land in the log."""
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP = (ROOT / "scripts" / "configure_image.sh").read_text()
ROOTFS = ROOT / "scripts" / "image" / "rootfs"
# Everything the setup puts on the mirror, for checks that something is *not* there anywhere
EVERYTHING = SETUP + "".join(p.read_text() for p in sorted(ROOTFS.rglob("*")) if p.is_file())


def unit(path: str) -> str:
    """A file the setup installs, without its last newline: from scripts/image/rootfs/ (put), or a
    heredoc still in configure_image.sh (the ones with build-time values)."""
    if (ROOTFS / path.lstrip("/")).is_file():
        assert "put 0" in SETUP and re.search(rf"put 0\d+ {re.escape(path)}\n", SETUP), f"{path} is never installed"
        return (ROOTFS / path.lstrip("/")).read_text().removesuffix("\n")
    return re.search(rf"cat << 'EOF' > {re.escape(path)}\n(.*?)\nEOF\n", SETUP, re.S).group(1)


def test_only_the_root_is_overlaid():
    """An image is locked from its first boot: a squashfs root under overlayroot, set in the build.
    recurse=0 keeps /storage on the card."""
    image_opts = re.search(r'if \[ -n "\$\{BUILDING_IMAGE:-\}" \]; then\n\s*target_opts\+=\((.*?)\)', SETUP).group(1)
    assert image_opts == ('"rootfstype=squashfs" "overlayroot=tmpfs:recurse=0" "systemd.condition_first_boot=false"'
                          ' "rootdelay=20"')
    assert "enable_overlayfs" not in EVERYTHING
    assert "boot=overlay" not in EVERYTHING  # not the parameter overlayroot reads
    assert "ConditionKernelCommandLine=!overlayroot" in unit("/etc/systemd/system/systemd-remount-fs.service.d/mirrordash.conf")


def test_repart_finds_the_root_partition_under_overlayroot():
    """`/` is an overlay on a locked mirror, so repart reads root= from the kernel command line;
    overlayroot= comes first there and must not be taken for it."""
    expr = re.search(r"findfs \"\$\(sed -n '(.*?)' /proc/cmdline\)\"", unit("/usr/local/bin/mirrordash-repart.sh")).group(1)
    for cmdline in ("overlayroot=tmpfs:recurse=0 console=tty3 root=PARTUUID=27b94958-02 rootfstype=squashfs rootwait",
                    "root=PARTUUID=27b94958-02 quiet"):
        assert subprocess.run(["sed", "-n", expr], input=cmdline + "\n", capture_output=True, text=True).stdout == "PARTUUID=27b94958-02\n"


def test_first_boot_units_dont_run_on_every_boot():
    """What Pi OS does on a first boot is done in the build or kept on /storage: an image has no first boot."""
    assert "touch /etc/cloud/cloud-init.disabled" in SETUP
    assert "userconf.txt\n" not in EVERYTHING and "usermod -s /bin/bash -p" in SETUP
    assert "rm -f /etc/ssh/sshd_config.d/rename_user.conf" in SETUP
    assert "ssh-keygen -A -f /storage/mirrordash/ssh" in unit("/usr/local/bin/mirrordash-hydrate.sh")
    assert "HostKey /storage/mirrordash/ssh/etc/ssh/ssh_host_ed25519_key" in unit("/etc/ssh/sshd_config.d/mirrordash-hostkeys.conf")


def test_hydrate_never_fills_a_ram_storage():
    """On an image a /storage that didn't mount is a folder in the RAM overlay: hydrate repairs the
    partition, and stops before writing anything there if it still isn't mounted."""
    hydrate = unit("/usr/local/bin/mirrordash-hydrate.sh")
    stop = hydrate.index("! mountpoint -q /storage; then\n    echo \"Error: /storage could not be mounted")
    assert hydrate.index('e2fsck -p "$DATA_PART"') < stop < hydrate.index("mkdir -p /storage")


def test_swap_is_zram_only():
    assert unit("/etc/rpi/swap.conf.d/mirrordash.conf") == "[Main]\nMechanism=zram"


def test_sudo_doesnt_log_the_wifi_password():
    assert "Defaults!/usr/bin/nmcli !log_allowed" in unit("/etc/sudoers.d/mirrordash")


def test_no_hobbyist_patterns_in_the_build():
    """AGENTS rules (DevOps): no polling, no scraping human-readable output, no commands that don't
    exist hidden behind `|| true`."""
    build = (ROOT / "scripts" / "build_image.sh").read_text()
    for script in (EVERYTHING, build):
        assert "sleep " not in script
        assert "nmcli device wait" not in script  # not an nmcli command
        assert "/proc/cmdline | grep" not in script and "unit s print | awk" not in script
        assert "grep -m 1 '^version" not in script
    assert "cache-purge" not in EVERYTHING
    assert "dev wifi list --rescan yes" in unit("/usr/local/bin/mirrordash-wifi-check.sh")
    assert "plymouth/themes/mirrordash/mirrordash.script" in build
    assert "cfg80211.ieee80211_regdom=" in build



def test_app_watch_restarts_what_hangs(tmp_path):
    """Two silent rounds restart the app (or the kiosk), and again every second round while it stays."""
    script = tmp_path / "app-watch"
    script.write_text(unit("/usr/local/lib/mirrordash/app-watch"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text('#!/bin/bash\n[ -n "$HEALTH" ] || exit 7\necho "$HEALTH"\n')
    (bin_dir / "systemctl").write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/restarts\n')
    (bin_dir / "logger").write_text("#!/bin/bash\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)

    def rounds(health: str, n: int) -> list[str]:
        (tmp_path / "restarts").write_text("")
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HEALTH": health, "MIRRORDASH_WATCH_STATE": str(tmp_path / "state")}
        for _ in range(n):
            subprocess.run(["bash", str(script)], env=env, check=True, capture_output=True)
        return (tmp_path / "restarts").read_text().splitlines()

    assert rounds("", 4) == ["restart mirrordash.service"] * 2
    assert rounds('{"status":"ok","screen_seen_seconds_ago":30}', 3) == []  # an answer resets the count
    assert rounds('{"status":"ok","screen_seen_seconds_ago":400}', 2) == ["restart cog-kiosk.service"]
    assert rounds('{"status":"ok","screen_seen_seconds_ago":null}', 4) == []  # screen off


def test_hydrate_moves_the_apps_python_to_storage(tmp_path):
    """A venv that uses the root's Python gets a copy of it on /storage and is pointed at that, so an
    OS update with another Python never leaves the venvs unable to start (it used to delete them)."""
    hydrate = unit("/usr/local/bin/mirrordash-hydrate.sh")
    assert "rm -rf /storage/mirrordash/venv " not in hydrate  # nothing of the app is thrown away
    block = hydrate[hydrate.index("PY_ROOT="):hydrate.index('[ -d "$PY_STORAGE" ] && chown')]
    root, storage, venvs = tmp_path / "root", tmp_path / "python", tmp_path / "storage"
    full = root / "cpython-3.14.8-linux-aarch64-gnu"
    (full / "bin").mkdir(parents=True)
    (full / "bin" / "python3.14").write_text("#!/bin/sh\n")
    (root / "cpython-3.14-linux-aarch64-gnu").symlink_to(full)  # uv's minor link, absolute
    minor = root / "cpython-3.14-linux-aarch64-gnu"
    for name in ("venv_a", "venv_old"):
        (venvs / name / "bin").mkdir(parents=True)
        (venvs / name / "pyvenv.cfg").write_text(f"home = {minor}/bin\nimplementation = CPython\nversion_info = 3.14\n")
        (venvs / name / "bin" / "python").symlink_to(minor / "bin" / "python3.14")
        (venvs / name / "bin" / "python3").symlink_to("python")
    env = {"PATH": "/usr/bin:/bin", "MIRRORDASH_PY_ROOT": str(root), "MIRRORDASH_PY_STORAGE": str(storage),
           "MIRRORDASH_VENVS": str(venvs)}
    for _ in range(2):  # every boot: the second run finds nothing to do
        subprocess.run(["bash", "-euo", "pipefail", "-c", block], env=env, check=True)
    assert (storage / "cpython-3.14.8-linux-aarch64-gnu" / "bin" / "python3.14").is_file()
    assert os.readlink(storage / "cpython-3.14-linux-aarch64-gnu") == "cpython-3.14.8-linux-aarch64-gnu"
    for name in ("venv_a", "venv_old"):
        assert (venvs / name / "pyvenv.cfg").read_text().startswith(f"home = {storage}/cpython-3.14-linux-aarch64-gnu/bin\n")
        assert os.readlink(venvs / name / "bin" / "python") == f"{storage}/cpython-3.14-linux-aarch64-gnu/bin/python3.14"
        assert os.readlink(venvs / name / "bin" / "python3") == "python"
    # The root's Python can now change (an OS update): the venvs still find theirs
    shutil.rmtree(full)
    assert (venvs / "venv_a" / "bin" / "python").resolve().is_file()


def test_every_file_in_rootfs_is_installed_and_every_put_has_its_file():
    """A file added under scripts/image/rootfs/ but never put would silently be missing on the mirror;
    a put without its file stops the build an hour in."""
    files = {"/" + str(p.relative_to(ROOTFS)) for p in ROOTFS.rglob("*") if p.is_file()}
    puts = re.findall(r"^\s*put (0\d{3}) (\S+)$", SETUP, re.M)
    assert sorted(files) == sorted(path for _, path in puts)
    for mode, path in puts:  # a script must be executable, a sudo rule must not be (sudo refuses it)
        text = (ROOTFS / path.lstrip("/")).read_text()
        assert (mode == "0755") == text.startswith("#!"), path
        assert mode == "0440" if path.startswith("/etc/sudoers.d/") else True, path


def test_the_clock_starts_at_the_last_saved_time(tmp_path):
    """No clock battery: the time is saved on /storage once NTP has set it, and a start moves the
    clock forward to it, never back."""
    script = tmp_path / "clock"
    script.write_text(unit("/usr/local/lib/mirrordash/clock"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "timedatectl").write_text('#!/bin/bash\necho "$SYNCED"\n')
    (bin_dir / "date").write_text(f'#!/bin/bash\n[ "$1" = +%s ] && {{ echo "$NOW"; exit; }}\n'
                                  f'[ "$1" = -s ] && echo "$2" >> {tmp_path}/set\nexit 0\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    saved = tmp_path / "clock-file"

    def run(*args, synced="yes", now=1_000):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "MIRRORDASH_CLOCK_FILE": str(saved), "SYNCED": synced, "NOW": str(now)}
        subprocess.run(["bash", str(script), *args], env=env, check=True, capture_output=True)

    run("save", synced="no")
    assert not saved.exists()  # a time NTP hasn't confirmed is never saved
    run("save")
    os.utime(saved, (5_000, 5_000))
    run("restore", now=1_000)  # an old time at start: forward to the saved one
    run("restore", now=9_000)  # NTP is already ahead: left alone
    assert (tmp_path / "set").read_text() == "@5000\n"


def test_factory_reset_leaves_only_the_logs(tmp_path):
    """Everything of the owner's goes (settings, Wi-Fi, modules, keys); the saved logs stay, and the
    mirror restarts. It does nothing without --yes."""
    storage = tmp_path / "storage"
    for name in ("data/config.json", "system-connections/Home.nmconnection", "venv_a/x", "python/x", "ssh/key",
                 "clock", "logs/os-trial-1.log"):
        (storage / name).parent.mkdir(parents=True, exist_ok=True)
        (storage / name).write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "systemctl").write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/systemctl\n')
    (bin_dir / "logger").write_text("#!/bin/bash\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    script = tmp_path / "reset"
    script.write_text(unit("/usr/local/bin/mirrordash-factory-reset"))
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "MIRRORDASH_STORAGE": str(storage)}
    assert subprocess.run(["bash", str(script)], env=env, capture_output=True).returncode == 2
    assert (storage / "data" / "config.json").exists()
    subprocess.run(["bash", str(script), "--yes"], env=env, check=True, capture_output=True)
    assert [p.name for p in storage.iterdir()] == ["logs"] and (storage / "logs" / "os-trial-1.log").exists()
    assert (tmp_path / "systemctl").read_text() == "reboot\n"
    assert "pi ALL=(ALL) NOPASSWD: /usr/local/bin/mirrordash-factory-reset --yes" in unit("/etc/sudoers.d/mirrordash")
