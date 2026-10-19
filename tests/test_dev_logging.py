"""Developer logging (Settings → Developer): the real mirrordash-dev-logging from configure_image.sh,
run against a temporary /storage with fake systemctl/journalctl/mount (the admin switch: test_api_admin.py)."""
import os
import subprocess
import time

import pytest
from test_configure_image import unit

SCRIPT = unit("/usr/local/bin/mirrordash-dev-logging")


@pytest.fixture
def dev(tmp_path):
    """run(*args) -> CompletedProcess; .storage, .dropin, .calls() for what it did to the system."""
    script = tmp_path / "dev-logging"
    script.write_text(SCRIPT)
    storage, log, bin_dir = tmp_path / "storage", tmp_path / "calls.log", tmp_path / "bin"
    storage.mkdir()
    bin_dir.mkdir()
    mounted = tmp_path / "mounted"
    fakes = {
        "systemctl": f'echo "systemctl $*" >> {log}',
        "journalctl": f'echo "journalctl $*" >> {log}',
        "systemd-tmpfiles": "exit 0",
        "install": 'mkdir -p "${@: -1}"',  # install -d -o root ... DIR: only root may set the owner
        "mount": f'echo "mount $*" >> {log}; touch {mounted}',
        "umount": f'echo "umount $*" >> {log}; rm -f {mounted}',
        "mountpoint": f"[ -e {mounted} ]",
    }
    for name, text in fakes.items():
        (bin_dir / name).write_text(f"#!/bin/bash\n{text}\n")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "machine-id").write_text("current\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "MIRRORDASH_STORAGE": str(storage),
           "MIRRORDASH_DROPIN": str(tmp_path / "journald.conf.d" / "90-mirrordash-dev-logging.conf"),
           "MIRRORDASH_JOURNAL_TARGET": str(tmp_path / "var-log-journal"),
           "MIRRORDASH_MACHINE_ID_FILE": str(tmp_path / "machine-id"), "MIRRORDASH_JOURNAL_CAP_MB": "3"}

    def run(*args):
        return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)
    run.storage = storage
    run.dropin = tmp_path / "journald.conf.d" / "90-mirrordash-dev-logging.conf"
    run.calls = lambda: log.read_text().splitlines() if log.exists() else []
    return run


def test_on_keeps_the_system_log_on_storage_for_seven_days(dev):
    assert dev("on").returncode == 0
    until = int((dev.storage / "dev-logging-until").read_text())
    assert abs(until - (time.time() + 7 * 86400)) < 60
    assert dev.dropin.read_text() == "[Journal]\nStorage=persistent\nSystemMaxUse=3M\n"
    calls = dev.calls()
    assert calls[0].startswith("mount --bind") and calls[0].split()[2].endswith("storage/journal")
    assert "systemctl restart systemd-journald" in calls and "journalctl --flush" in calls


def test_off_goes_back_to_ram_and_keeps_the_saved_log_until_cleared(dev):
    dev("on")
    (dev.storage / "journal" / "current").mkdir(parents=True)
    assert dev("clear").returncode == 1  # not while it's on
    assert dev("off").returncode == 0
    assert not (dev.storage / "dev-logging-until").exists() and not dev.dropin.exists()
    calls = dev.calls()
    assert calls[-3:] == ["journalctl --relinquish-var", "systemctl restart systemd-journald", calls[-1]]
    assert calls[-1].startswith("umount")
    assert (dev.storage / "journal").exists()
    assert dev("clear").returncode == 0 and not (dev.storage / "journal").exists()


def test_it_turns_itself_off_when_the_seven_days_are_over(dev):
    dev("on")
    (dev.storage / "dev-logging-until").write_text(str(int(time.time()) - 1))
    assert dev("apply").returncode == 0
    assert not (dev.storage / "dev-logging-until").exists() and not dev.dropin.exists()


def test_the_saved_log_stays_under_its_cap(dev):
    """Every start has its own machine-id folder; the oldest starts go first, never the running one.
    (du rounds up: three 1 MB folders count as 4 MB, two as 3.)"""
    dev("on")
    journal = dev.storage / "journal"
    for i, name in enumerate(["oldest", "older", "current"]):
        (journal / name).mkdir(parents=True)
        (journal / name / "system.journal").write_bytes(b"x" * 1024 * 1024)
        os.utime(journal / name, (1_000_000 + i, 1_000_000 + i))
    assert dev("apply").returncode == 0
    assert sorted(p.name for p in journal.iterdir()) == ["current", "older"]


def test_only_the_three_admin_actions_are_allowed_through_sudo():
    sudoers = unit("/etc/sudoers.d/mirrordash-dev-logging")
    allowed = [line.split("NOPASSWD: ")[1] for line in sudoers.splitlines() if "NOPASSWD" in line]
    assert allowed == [f"/usr/local/bin/mirrordash-dev-logging {a}" for a in ("on", "off", "clear")]

