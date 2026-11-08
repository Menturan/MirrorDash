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


def run_damaged_storage(tmp_path: Path, e2fsck_rc: int) -> tuple[int, Path, Path]:
    """mirrordash-hydrate.sh on an image whose /storage doesn't mount; e2fsck answers e2fsck_rc. A fake
    mount mounts /storage only once mkfs.ext4 made the partition anew. (exit code, /storage/mirrordash, state)"""
    storage, state, rescue = tmp_path / "storage", tmp_path / "state", tmp_path / "rescue"
    for folder in (state, rescue, tmp_path / "base_venv"):
        folder.mkdir()
    (tmp_path / "cmdline").write_text("root=PARTUUID=abcd-05 rootfstype=squashfs\n")
    part = tmp_path / "p7"
    part.write_text("")
    body = (unit("/usr/local/bin/mirrordash-hydrate.sh")
            .replace("/storage/mirrordash", f"{storage}/mirrordash").replace("/storage/rauc", f"{storage}/rauc")
            .replace("/home/pi/mirrordash/base_venv", str(tmp_path / "base_venv"))
            .replace("/proc/cmdline", str(tmp_path / "cmdline")).replace("/run/mirrordash-rescue", str(rescue))
            .replace("/dev/disk/by-label/mirrordash-data", str(part)).replace('-b "$DATA_PART"', '-e "$DATA_PART"')
            .replace("/usr/local/lib/mirrordash/", f"{tmp_path}/lib/"))
    (tmp_path / "hydrate.sh").write_text(body)
    stubs = {
        "chown": "exit 0", "umount": "exit 0", "udevadm": "exit 0",
        # Only /storage is ever unmounted (so the NetworkManager bind mount and the modules' image are skipped)
        "mountpoint": f'[ "$2" != /storage ] || [ -e {state}/mounted ]',
        "mount": f'case "$*" in *" /storage"|/storage) [ -e {state}/made ] && touch {state}/mounted ;; *) exit 0 ;; esac',
        "e2fsck": f"echo \"$*\" >> {state}/e2fsck; exit {e2fsck_rc}",
        "mkfs.ext4": f'echo "$*" >> {state}/mkfs; touch {state}/made',
    }
    (tmp_path / "bin").mkdir()
    (tmp_path / "lib").mkdir()
    for name, text in stubs.items():
        (tmp_path / "bin" / name).write_text(f"#!/bin/sh\n{text}\n")
        (tmp_path / "bin" / name).chmod(0o755)
    for name, text in {"storage-rescue": 'touch "$2/mirrordash_autobackup_20261108_033000.mirror"; echo rescued',
                       "save-log": f'echo "$1" >> {state}/saved-logs'}.items():
        (tmp_path / "lib" / name).write_text(f"#!/bin/sh\n{text}\n")
        (tmp_path / "lib" / name).chmod(0o755)
    result = subprocess.run(["bash", str(tmp_path / "hydrate.sh")], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"})
    return result.returncode, storage / "mirrordash", state


def test_a_storage_partition_beyond_repair_is_made_anew_with_its_newest_backups(tmp_path):
    code, md, state = run_damaged_storage(tmp_path, e2fsck_rc=4)
    assert code == 0
    assert (state / "mkfs").read_text().split() == ["-F", "-L", "mirrordash-data", str(tmp_path / "p7")]
    backup = md / "data" / "backups" / "mirrordash_autobackup_20261108_033000.mirror"
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    assert (state / "saved-logs").read_text().split() == ["storage-rebuilt"]  # the red notice; not storage-repair
    assert os.readlink(md / "venv") == "venv_a"  # and it starts as a new mirror


def test_a_storage_partition_e2fsck_fixed_is_never_made_anew(tmp_path):
    """A mount that fails for another reason stops the mirror as before; nothing is formatted."""
    code, _, state = run_damaged_storage(tmp_path, e2fsck_rc=1)
    assert code == 1
    assert not (state / "mkfs").exists()


def test_storage_rescue_copies_the_newest_backups_off_a_broken_partition(tmp_path):
    """The first superblock zeroed (no mount, no plain debugfs): the newest automatic and hand-made backups
    still come out whole, through a copy of the superblock; nothing else does."""
    import shutil

    import pytest
    sbin = f"/usr/sbin:/sbin:{os.environ['PATH']}"
    if not shutil.which("debugfs", path=sbin):
        pytest.skip("needs e2fsprogs")
    env = {**os.environ, "PATH": sbin}
    disk, out = tmp_path / "disk.img", tmp_path / "out"
    out.mkdir()
    subprocess.run(["truncate", "-s", "256M", str(disk)], check=True)  # two block groups: a superblock copy
    subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", str(disk)], check=True, env=env)
    files = {"mirrordash_autobackup_20261101_033000.mirror": os.urandom(3000),
             "mirrordash_autobackup_20261108_033000.mirror": os.urandom(70000),
             "mirrordash_backup_20261102_120000.mirror": os.urandom(5000),
             "mirrordash_backup_20260901_120000.mirror": os.urandom(5000),
             "tmp_upload.mirror": os.urandom(100)}
    steps = ["mkdir mirrordash", "mkdir mirrordash/data", "mkdir mirrordash/data/backups"]
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        steps.append(f"write {tmp_path / name} mirrordash/data/backups/{name}")
    (tmp_path / "steps").write_text("\n".join(steps) + "\n")
    subprocess.run(["debugfs", "-w", "-f", str(tmp_path / "steps"), str(disk)], check=True, env=env, capture_output=True)
    with open(disk, "r+b") as f:  # the primary superblock: 1024 bytes at 1024
        f.seek(1024)
        f.write(bytes(1024))

    script = tmp_path / "storage-rescue"
    script.write_text(unit("/usr/local/lib/mirrordash/storage-rescue"))
    result = subprocess.run(["bash", str(script), str(disk), str(out)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    newest = ["mirrordash_autobackup_20261108_033000.mirror", "mirrordash_backup_20261102_120000.mirror"]
    assert result.stdout.split() == newest
    assert sorted(p.name for p in out.iterdir()) == sorted(newest)
    for name in newest:
        assert (out / name).read_bytes() == files[name]

    empty = tmp_path / "empty.img"  # nothing readable: says so, saves nothing
    subprocess.run(["truncate", "-s", "8M", str(empty)], check=True)
    assert subprocess.run(["bash", str(script), str(empty), str(out)], env=env, capture_output=True).returncode == 1
