"""Runs the real mirrordash-hydrate.sh (extracted from configure_image.sh) against a temp /storage."""
import os
import subprocess
from pathlib import Path

from test_configure_image import unit

SETUP = Path(__file__).parent.parent / "scripts" / "configure_image.sh"


def run_hydrate(tmp_path: Path) -> Path:
    body = unit("/usr/local/bin/mirrordash-hydrate.sh")
    storage, base = tmp_path / "storage", tmp_path / "base_venv"
    base.mkdir(exist_ok=True)
    script = tmp_path / "hydrate.sh"
    script.write_text(body.replace("/storage/mirrordash", f"{storage}/mirrordash").replace("/home/pi/mirrordash/base_venv", str(base))
                      .replace("/storage/rauc", f"{storage}/rauc"))
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


def test_the_cores_data_is_pis_alone_and_the_modules_have_a_folder_of_their_own(tmp_path):
    """The module host (another user) can't read the core's data dir: config.json holds every key."""
    md = run_hydrate(tmp_path)
    assert (md / "data").stat().st_mode & 0o777 == 0o700
    assert (md / "modules").is_dir()


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


def test_a_venv_whose_python_doesnt_start_is_kept(tmp_path):
    """It used to be thrown away (and the modules with it) when an OS update brought another Python.
    The app's Python is on /storage now; if a venv still can't start, launch.sh falls back to the
    previous one or Safe mode, whose Reinstall button reads the modules from this venv."""
    md = tmp_path / "storage" / "mirrordash"
    (md / "venv_b" / "bin").mkdir(parents=True)
    (md / "venv_b" / "bin" / "python").symlink_to("/nonexistent/python3.99")
    (md / "venv").symlink_to("venv_b")
    run_hydrate(tmp_path)
    assert os.readlink(md / "venv") == "venv_b"
    assert (md / "venv_b").is_dir() and not (md / "venv_a").exists()


def run_launch(tmp_path: Path, trial: bool, pythons: tuple[str, str] = ("3.14", "3.14")) -> Path:
    """Runs the real launch.sh with an app that crashes at once, on venv_b with venv_old behind it
    (their Pythons: pythons), and the module site on site_b."""
    md = tmp_path / "storage" / "mirrordash"
    for name, python in (("venv_b", pythons[0]), ("venv_old", pythons[1]), ("base_venv", "3.14")):
        (md / name / "bin").mkdir(parents=True)
        (md / name / "lib" / f"python{python}").mkdir(parents=True)
        (md / name / "bin" / "python").write_text(f"#!/bin/sh\necho {name} >> {tmp_path}/started\nexit 1\n")
        (md / name / "bin" / "python").chmod(0o755)
    (md / "venv").symlink_to("venv_b")
    for site in ("site_a", "site_b"):
        (md / "modules" / site).mkdir(parents=True)
    (md / "modules" / "site").symlink_to("site_b")
    bootloader = tmp_path / "rauc-bootloader"
    bootloader.write_text(f'#!/bin/sh\n[ "$1" = is-update-trial ] && exit {0 if trial else 1}\n')
    bootloader.chmod(0o755)
    script = tmp_path / "launch.sh"
    script.write_text((Path(__file__).parent.parent / "scripts" / "launch.sh").read_text()
                      .replace("/storage/mirrordash", str(md)).replace("/home/pi/mirrordash/base_venv", str(md / "base_venv"))
                      .replace("/home/pi/mirrordash", str(tmp_path)).replace("/usr/local/lib/mirrordash/rauc-bootloader", str(bootloader)))
    subprocess.run(["bash", str(script)], env={"PATH": "/usr/bin:/bin"}, capture_output=True)
    return md


def test_a_crash_on_an_os_trial_boot_leaves_the_app_alone(tmp_path):
    """On the trial boot of a new OS the crash is the OS's doing: no rollback (that would downgrade
    the app for good, and Safe mode would get the broken OS kept); mark-good gives up instead."""
    md = run_launch(tmp_path / "trial", trial=True)
    assert os.readlink(md / "venv") == "venv_b" and (md / "venv_old").is_dir() and not (md / "venv_failed").exists()
    assert (tmp_path / "trial" / "started").read_text() == "venv_b\n"
    # A normal start rolls back as before
    md = run_launch(tmp_path / "normal", trial=False)
    assert os.readlink(md / "venv") == "venv_a" and (md / "venv_failed").is_dir()
    assert (tmp_path / "normal" / "started").read_text() == "venv_b\nvenv_old\n"  # venv_old, now named venv_a
    assert os.readlink(md / "modules" / "site") == "site_b"  # the same Python: the site wasn't switched


def test_a_rollback_from_a_new_python_takes_the_old_module_site_back(tmp_path):
    """A Python upgrade builds the module site anew beside the old one; going back to venv_old goes back
    to that one too, or modules with compiled code (built for the new Python) stop."""
    md = run_launch(tmp_path, trial=False, pythons=("3.15", "3.14"))
    assert os.readlink(md / "venv") == "venv_a" and os.readlink(md / "modules" / "site") == "site_a"
