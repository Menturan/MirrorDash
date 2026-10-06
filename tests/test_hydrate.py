"""Runs the real mirrordash-hydrate.sh (extracted from setup_appliance.sh) against a temp /storage."""
import os
import re
import subprocess
from pathlib import Path

SETUP = Path(__file__).parent.parent / "scripts" / "setup_appliance.sh"


def run_hydrate(tmp_path: Path) -> Path:
    body = re.search(r"cat << 'EOF' > /usr/local/bin/mirrordash-hydrate\.sh\n(.*?)\nEOF\n", SETUP.read_text(), re.S).group(1)
    storage, base = tmp_path / "storage", tmp_path / "base_venv"
    base.mkdir(exist_ok=True)
    script = tmp_path / "hydrate.sh"
    script.write_text(body.replace("/storage/mirrordash", f"{storage}/mirrordash").replace("/home/pi/mirrordash/base_venv", str(base)))
    # Stub out root-only commands; mountpoint=0 skips the NetworkManager bind mount
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for cmd in ("chown", "mount", "mountpoint"):
        (bin_dir / cmd).write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / cmd).chmod(0o755)
    subprocess.run(["bash", str(script)], check=True, env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})
    return storage / "mirrordash"


def test_first_boot_seeds_venv_a(tmp_path):
    md = run_hydrate(tmp_path)
    assert os.readlink(md / "venv") == "venv_a"
    assert (md / "venv_a").is_dir()


def test_reboot_keeps_ab_update(tmp_path):
    # State after commit_venv_next(): venv_a renamed to venv_old, link -> venv_b
    md = tmp_path / "storage" / "mirrordash"
    (md / "venv_b").mkdir(parents=True)
    (md / "venv_old").mkdir()
    (md / "venv").symlink_to("venv_b")
    run_hydrate(tmp_path)
    assert os.readlink(md / "venv") == "venv_b"
    assert not (md / "venv_a").exists()


def test_dangling_link_is_reseeded(tmp_path):
    md = tmp_path / "storage" / "mirrordash"
    md.mkdir(parents=True)
    (md / "venv").symlink_to("venv_b")  # target missing
    run_hydrate(tmp_path)
    assert os.readlink(md / "venv") == "venv_a"
    assert (md / "venv_a").is_dir()
