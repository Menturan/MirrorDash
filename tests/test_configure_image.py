"""How configure_image.sh locks the mirror. Nothing here can run off a Pi, so this guards the lines
that once made /storage a RAM overlay (lost at every reboot) and the Wi-Fi password land in the log."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP = (ROOT / "scripts" / "configure_image.sh").read_text()


def unit(path: str) -> str:
    return re.search(rf"cat << 'EOF' > {re.escape(path)}\n(.*?)\nEOF\n", SETUP, re.S).group(1)


def test_only_the_root_is_overlaid():
    """An image is locked from its first boot: a squashfs root under overlayroot, set in the build.
    recurse=0 keeps /storage on the card."""
    image_opts = re.search(r'if \[ -n "\$\{BUILDING_IMAGE:-\}" \]; then\n\s*target_opts\+=\((.*?)\)', SETUP).group(1)
    assert image_opts == ('"rootfstype=squashfs" "overlayroot=tmpfs:recurse=0" "systemd.condition_first_boot=false"'
                          ' "rootdelay=20"')
    assert "enable_overlayfs" not in SETUP
    assert "boot=overlay" not in SETUP  # not the parameter overlayroot reads
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
    assert "userconf.txt\n" not in SETUP and "usermod -s /bin/bash -p" in SETUP
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
    for script in (SETUP, build):
        assert "sleep " not in script
        assert "nmcli device wait" not in script  # not an nmcli command
        assert "/proc/cmdline | grep" not in script and "unit s print | awk" not in script
        assert "grep -m 1 '^version" not in script
    assert "cache-purge" not in SETUP
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
