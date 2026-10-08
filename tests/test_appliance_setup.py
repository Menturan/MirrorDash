"""How setup_appliance.sh locks the mirror. Nothing here can run off a Pi, so this guards the lines
that once made /storage a RAM overlay (lost at every reboot) and the Wi-Fi password land in the log."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP = (ROOT / "scripts" / "setup_appliance.sh").read_text()
FINALIZE = (ROOT / "scripts" / "finalize_appliance.sh").read_text()
RECURSE_SED = "sed -i '1{/overlayroot=/!s/^/overlayroot=tmpfs:recurse=0 /}' /boot/firmware/cmdline.txt"


def unit(path: str) -> str:
    return re.search(rf"cat << 'EOF' > {re.escape(path)}\n(.*?)\nEOF\n", SETUP, re.S).group(1)


def test_only_the_root_is_overlaid():
    """The lock is one kernel parameter; recurse=0 keeps /storage on the card. Checked before rebooting."""
    lock = unit("/etc/systemd/system/mirrordash-lock.service")
    check = "grep -q overlayroot=tmpfs:recurse=0 /boot/firmware/cmdline.txt"
    assert lock.index("dpkg -s overlayroot") < lock.index(RECURSE_SED) < lock.index(check) < lock.index("systemctl reboot")
    assert FINALIZE.index("dpkg -s overlayroot") < FINALIZE.index(RECURSE_SED) < FINALIZE.index(check) < FINALIZE.index("\nreboot")
    assert "enable_overlayfs" not in SETUP + FINALIZE


def test_boot_units_know_the_locked_root():
    for path in ("/etc/systemd/system/mirrordash-lock.service",
                 "/etc/systemd/system/mirrordash-repart.service",
                 "/etc/systemd/system/systemd-remount-fs.service.d/mirrordash.conf"):
        assert "ConditionKernelCommandLine=!overlayroot" in unit(path), path
    assert "boot=overlay" not in SETUP  # not the parameter overlayroot reads


def test_swap_is_zram_only():
    assert unit("/etc/rpi/swap.conf.d/mirrordash.conf") == "[Main]\nMechanism=zram"


def test_sudo_doesnt_log_the_wifi_password():
    assert "Defaults!/usr/bin/nmcli !log_allowed" in unit("/etc/sudoers.d/mirrordash")


def test_no_hobbyist_patterns_in_the_build():
    """AGENTS rules (DevOps): no polling, no scraping human-readable output, no commands that don't
    exist hidden behind `|| true`."""
    build = (ROOT / "scripts" / "build_image.sh").read_text()
    for script in (SETUP, FINALIZE, build):
        assert "sleep " not in script
        assert "nmcli device wait" not in script  # not an nmcli command
        assert "/proc/cmdline | grep" not in script and "unit s print | awk" not in script
        assert "grep -m 1 '^version" not in script
    assert "cache-purge" not in SETUP
    assert "dev wifi list --rescan yes" in unit("/usr/local/bin/mirrordash-wifi-check.sh")
    assert "plymouth/themes/mirrordash/mirrordash.script" in build
    assert "cfg80211.ieee80211_regdom=" in build


def test_golden_image_shows_the_scripts_the_build_installs():
    golden = (ROOT / "GOLDEN_IMAGE.md").read_text()
    assert unit("/usr/local/bin/mirrordash-wifi-check.sh") in golden
    repart = re.search(r"cat << 'EOF' > /usr/local/bin/mirrordash-repart.sh\n(.*?)\nEOF\n", SETUP, re.S).group(1)
    assert "\n".join(("   " + l) if l else "" for l in repart.split("\n")) in golden
