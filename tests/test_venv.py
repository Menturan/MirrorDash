"""The A/B swap on a fake /storage: kept when the change works, undone when it raises."""
import asyncio
import json
import os
from pathlib import Path

import pytest

import mirrordash_core.venv as venv


def test_venv_swap_keeps_a_working_change_and_undoes_a_failed_one(tmp_path, monkeypatch):
    monkeypatch.setattr(venv, "STORAGE_DIR", tmp_path)
    (tmp_path / "venv_a" / "bin").mkdir(parents=True)
    (tmp_path / "venv_a" / "marker").write_text("a")
    os.symlink("venv_a", tmp_path / "venv")

    async def works():
        async with venv.venv_swap() as python:
            assert python == str(tmp_path / "venv_b" / "bin" / "python")
    asyncio.run(works())
    assert os.readlink(tmp_path / "venv") == "venv_b"
    assert (tmp_path / "venv_old" / "marker").exists()  # the launcher's fallback

    async def fails():
        async with venv.venv_swap():
            raise RuntimeError("uv failed")
    with pytest.raises(RuntimeError):
        asyncio.run(fails())
    assert os.readlink(tmp_path / "venv") == "venv_b"  # still the working one
    assert not (tmp_path / "venv_a").exists()


def fake_venv(root: Path, py: str = "3.15") -> Path:
    """A venv on disk whose Python never runs: only the files importlib.metadata reads."""
    sp = root / "lib" / f"python{py}" / "site-packages"

    def dist(name, version, entry=None, direct_url=None):
        d = sp / f"{name.replace('-', '_')}-{version}.dist-info"
        d.mkdir(parents=True)
        (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        if entry:
            (d / "entry_points.txt").write_text(f"[mirrordash.modules]\n{entry} = {entry}:Module\n")
        if direct_url:
            (d / "direct_url.json").write_text(json.dumps(direct_url))

    dist("mirrordash", "0.9.0")
    dist("mirrordash-news", "1.2.0", "mirrordash_news")
    dist("mirrordash-calendar", "0.3.0", "mirrordash_calendar",
         {"url": "https://github.com/someone/mirrordash-calendar", "vcs_info": {"vcs": "git", "commit_id": "abc123"}})
    dist("requests", "2.32.0")
    return root


def test_installed_modules_are_read_from_the_module_site(tmp_path, monkeypatch):
    """The modules live in the module host's site, never on the core's sys.path: the core reads their
    files (ARCHITECTURE #41). A venv that doesn't run (Safe mode's) is read from its files too."""
    from mirrordash_core.features.backup.service import (
        installed_modules,
        venv_distributions,
    )
    venv = fake_venv(tmp_path / "venv_b")
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path / "modules"))
    (tmp_path / "modules").mkdir()
    (tmp_path / "modules" / "site").symlink_to(venv / "lib" / "python3.15" / "site-packages")
    modules = sorted(installed_modules(), key=lambda m: m["name"])
    assert [(m["package_name"], m["version"], m.get("source")) for m in modules] == [
        ("mirrordash-calendar", "0.3.0", "git+https://github.com/someone/mirrordash-calendar@abc123"),
        ("mirrordash-news", "1.2.0", None)]
    assert next(iter(venv_distributions(venv, name="mirrordash"))).version == "0.9.0"


def test_pythons_no_startable_venv_uses_are_removed(tmp_path, monkeypatch):
    """After an upgrade to 3.15 and the next swap, 3.14 is used by no venv the launcher can start:
    it goes. venv_old's Python stays (the fallback must start), venv_failed's doesn't count."""
    py = tmp_path / "python"
    for full, minor in (("cpython-3.14.8-linux-aarch64-gnu", "cpython-3.14-linux-aarch64-gnu"),
                        ("cpython-3.15.1-linux-aarch64-gnu", "cpython-3.15-linux-aarch64-gnu"),
                        ("cpython-3.15.0-linux-aarch64-gnu", None)):  # an older patch uv moved the link off
        (py / full / "bin").mkdir(parents=True)
        if minor:
            (py / minor).symlink_to(full)
    (py / ".lock").write_text("")
    md = tmp_path / "storage"

    def venv_on(name, minor):
        (md / name).mkdir(parents=True, exist_ok=True)
        (md / name / "pyvenv.cfg").write_text(f"home = {py}/cpython-{minor}-linux-aarch64-gnu/bin\nversion_info = {minor}\n")

    monkeypatch.setattr(venv, "STORAGE_DIR", md)
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(py))
    venv_on("venv_b", "3.15")
    venv_on("venv_old", "3.14")  # the fallback, still on 3.14
    venv_on("venv_failed", "3.13")
    venv.remove_unused_pythons()
    assert sorted(p.name for p in py.iterdir()) == [".lock", "cpython-3.14-linux-aarch64-gnu", "cpython-3.14.8-linux-aarch64-gnu",
                                                     "cpython-3.15-linux-aarch64-gnu", "cpython-3.15.1-linux-aarch64-gnu"]
    venv_on("venv_old", "3.15")  # the next swap: nothing on 3.14 any more
    venv.remove_unused_pythons()
    assert sorted(p.name for p in py.iterdir()) == [".lock", "cpython-3.15-linux-aarch64-gnu", "cpython-3.15.1-linux-aarch64-gnu"]
    # Not on a mirror (no UV_PYTHON_INSTALL_DIR): a developer's Pythons are never touched
    monkeypatch.delenv("UV_PYTHON_INSTALL_DIR")
    venv_on("venv_b", "3.99")
    venv.remove_unused_pythons()
    assert len(list(py.iterdir())) == 3


def test_a_package_change_needs_free_space_first(tmp_path, monkeypatch):
    """A full card mid-install leaves a half-built venv: venv_swap, which every package change goes
    through, refuses before touching anything."""
    from collections import namedtuple

    from fastapi import HTTPException
    monkeypatch.setattr(venv, "STORAGE_DIR", tmp_path)
    (tmp_path / "venv_a").mkdir()
    os.symlink("venv_a", tmp_path / "venv")
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(venv.shutil, "disk_usage", lambda p: usage(1, 1, 100 * 1024 * 1024))

    async def change():
        async with venv.venv_swap():
            pass
    with pytest.raises(HTTPException) as e:
        asyncio.run(change())
    assert e.value.status_code == 507 and "100 MB free" in e.value.detail
    assert os.readlink(tmp_path / "venv") == "venv_a" and not (tmp_path / "venv_b").exists()
