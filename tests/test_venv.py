"""The A/B swap on a fake /storage: kept when the change works, undone when it raises."""
import asyncio
import os

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
