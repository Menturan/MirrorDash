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


def test_installed_modules_of_a_venv_that_doesnt_run(tmp_path):
    """Safe mode (and a new Python) read the modules from a venv's files: its Python can't start here."""
    from mirrordash_core.features.backup.service import installed_modules, venv_distributions
    venv = fake_venv(tmp_path / "venv_b")
    modules = sorted(installed_modules(venv), key=lambda m: m["name"])
    assert [(m["package_name"], m["version"], m.get("source")) for m in modules] == [
        ("mirrordash-calendar", "0.3.0", "git+https://github.com/someone/mirrordash-calendar@abc123"),
        ("mirrordash-news", "1.2.0", None)]
    assert next(iter(venv_distributions(venv, name="mirrordash"))).version == "0.9.0"
    assert installed_modules(tmp_path / "missing") == []  # never this process's own packages instead
