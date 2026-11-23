"""The clock module is always there: a fresh venv (rebuild, new Python) gets it again (updates/clock.py)."""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from conftest import call

from mirrordash_core import notifications
from mirrordash_core.features.updates import clock, service

PYTHON = "/storage/mirrordash/venv_b/bin/python"
CLOCK_SOURCE = "git+https://github.com/Menturan/mirrordash-clock.git@v1.2.3"


def venv_with(tmp_path, *packages):
    site = tmp_path / "venv_b" / "lib" / "python3.14" / "site-packages"
    for name in packages:
        (site / f"{name.replace('-', '_')}-1.0.0.dist-info").mkdir(parents=True)
        (site / f"{name.replace('-', '_')}-1.0.0.dist-info" / "METADATA").write_text(f"Name: {name}\nVersion: 1.0.0\n")
    site.mkdir(parents=True, exist_ok=True)
    return str(tmp_path / "venv_b" / "bin" / "python")


@pytest.fixture
def installs():
    """Every uv pip install of the clock's helper, with the release lookup answered."""
    done = []

    async def fake_uv_pip(python, command, *args, timeout=None):
        done.append((python, args[-1]))
        return 0, "", ""

    with patch.object(clock, "_latest_release", return_value="v1.2.3"), patch.object(clock, "uv_pip", fake_uv_pip):
        yield done


def test_missing_clock_is_installed(tmp_path, installs):
    python = venv_with(tmp_path, "mirrordash")
    call(lambda: clock.ensure_clock(python))
    assert installs == [(python, CLOCK_SOURCE)]


def test_clock_already_there_is_not_installed_again(tmp_path, installs):
    call(lambda: clock.ensure_clock(venv_with(tmp_path, "mirrordash", "mirrordash-clock")))
    assert installs == []


def test_failed_install_is_logged_and_told_but_does_not_raise(tmp_path, caplog):
    async def failing(python, command, *args, timeout=None):
        return 1, "", "no network"

    with patch.object(clock, "_latest_release", return_value="v1.2.3"), patch.object(clock, "uv_pip", failing):
        call(lambda: clock.ensure_clock(venv_with(tmp_path, "mirrordash")))
    assert "no network" in caplog.text
    assert any("clock" in item["text"] for item in notifications._load()["items"])


def test_no_release_found_is_told_too(tmp_path):
    with patch.object(clock, "_latest_release", side_effect=OSError("offline")):
        call(lambda: clock.ensure_clock(venv_with(tmp_path, "mirrordash")))
    assert any("clock" in item["text"] for item in notifications._load()["items"])


@pytest.fixture
def swap(monkeypatch):
    """venv_swap's steps without a mirror: the next venv is PYTHON's folder."""
    monkeypatch.setattr("mirrordash_core.venv.prepare_venv_next",
                        AsyncMock(return_value=(Path("/storage/mirrordash/venv_a"), Path("/storage/mirrordash/venv_b"))))
    monkeypatch.setattr("mirrordash_core.venv.commit_venv_next", AsyncMock())
    monkeypatch.setattr("mirrordash_core.venv.revert_venv_next", AsyncMock())
    monkeypatch.setattr(service, "get_venv_paths", lambda: True)
    monkeypatch.setattr(service, "get_core_version", lambda: "1.0.0")
    monkeypatch.setattr(service, "run_in_background", lambda coro: coro.close())
    monkeypatch.setattr(service, "run_restart", AsyncMock())


def test_rebuild_without_clock_installs_it(swap, installs):
    async def core_only(python, command, *args, timeout=None):
        return 0, "", ""

    with patch.object(service, "uv_pip", core_only):
        response = call(service.rebuild_venv)
    assert response.status_code == 200
    assert installs == [(PYTHON, CLOCK_SOURCE)]


def test_rebuild_survives_a_failed_clock_install(swap, caplog):
    async def uv(python, command, *args, timeout=None):
        return (1, "", "no network") if "mirrordash-clock" in args[-1] else (0, "", "")

    with patch.object(service, "uv_pip", uv), patch.object(clock, "uv_pip", uv), \
         patch.object(clock, "_latest_release", return_value="v1.2.3"):
        response = call(service.rebuild_venv)
    assert response.status_code == 200 and "no network" in caplog.text
    assert any("clock" in item["text"] for item in notifications._load()["items"])


def test_new_python_path_installs_the_clock(swap, installs, module_site):
    async def uv(python, command, *args, timeout=None):
        return 0, "", ""

    check = {"update_available": True, "needs_python": "3.15", "latest_version": "1.0.0"}
    with patch.object(service, "check_core_update", AsyncMock(return_value=check)), \
         patch.object(service, "run", AsyncMock(return_value=(0, "", ""))), patch.object(service, "uv_pip", uv), \
         patch.object(service, "load_config", return_value={}):
        response = call(lambda: service.update_core())
    assert response.status_code == 200
    assert installs == [(PYTHON, CLOCK_SOURCE)]
