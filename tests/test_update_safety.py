"""What keeps an update from leaving a mirror worse off: an app update on trial until it has run a while,
a system kept only when the mirror really works, a download that goes on where it broke, and the nightly
update not stuck behind a system install that fails."""
import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from starlette.responses import Response

from mirrordash_core.features.updates import download, service

LAUNCH = (Path(__file__).parent.parent / "scripts" / "launch.sh").read_text()


def run_launch(tmp_path: Path, on_trial: bool) -> list[str]:
    """The real launch.sh with a new version (venv_b) that crashes 20 seconds in, and the version before
    behind it (venv_old); on_trial: the app left its venv-trial file. Returns what was started."""
    md = tmp_path / "mirrordash"
    for name in ("venv_b", "venv_old", "base_venv"):
        (md / name / "bin").mkdir(parents=True)
        (md / name / "bin" / "python").write_text(f"#!/bin/sh\necho {name} >> {tmp_path}/started\nexit 1\n")
        (md / name / "bin" / "python").chmod(0o755)
    (md / "venv").symlink_to("venv_b")
    if on_trial:
        (md / "venv-trial").touch()
    # A clock that moves 20 seconds each time it is read: the crash comes after launch.sh's 10
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "date").write_text(f'#!/bin/sh\nn=$(cat {tmp_path}/clock 2>/dev/null || echo 0)\n'
                                  f'echo $((n + 20)) > {tmp_path}/clock\necho $((n + 20))\n')
    (bin_dir / "date").chmod(0o755)
    script = tmp_path / "launch.sh"
    script.write_text(LAUNCH.replace("/storage/mirrordash", str(md)).replace("/home/pi/mirrordash/base_venv", str(md / "base_venv"))
                      .replace("/home/pi/mirrordash", str(tmp_path)).replace("/usr/local/lib/mirrordash/", str(tmp_path / "none") + "/"))
    subprocess.run(["bash", str(script)], env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, capture_output=True)
    return (tmp_path / "started").read_text().split()


def test_a_new_version_that_crashes_late_is_rolled_back_while_on_trial(tmp_path):
    """A Pi 3 can take longer than launch.sh's 10 seconds to reach the line that fails: without the trial
    file the new version would be started again for ever."""
    assert run_launch(tmp_path / "trial", on_trial=True) == ["venv_b", "venv_old"]
    assert os.readlink(tmp_path / "trial" / "mirrordash" / "venv") == "venv_a"
    assert not (tmp_path / "trial" / "mirrordash" / "venv-trial").exists()  # the version before isn't on trial
    # Confirmed (the file gone): a crash this late is a crash, not a broken update
    assert run_launch(tmp_path / "kept", on_trial=False) == ["venv_b"]
    assert os.readlink(tmp_path / "kept" / "mirrordash" / "venv") == "venv_b"


def test_the_app_confirms_an_update_once_it_has_run_a_while(tmp_path, monkeypatch):
    from mirrordash_core import venv
    monkeypatch.setattr(venv, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(venv, "CONFIRM_AFTER_SECONDS", 0)
    (tmp_path / "venv-trial").touch()
    asyncio.run(venv.confirm_venv())
    assert not (tmp_path / "venv-trial").exists()


@pytest.mark.parametrize("boot_status, screen_on, page_loaded, kept", [
    ("normal", True, True, True),
    ("normal", True, False, False),   # a black screen is no mirror
    ("normal", None, False, False),   # not known to be off yet (the display's first pass)
    ("normal", False, False, True),   # the screen is off: nothing to see
    ("safe_mode", True, True, False),  # the image's copy answering must not get the system kept
])
def test_the_trial_start_asks_for_more_than_an_answer(monkeypatch, boot_status, screen_on, page_loaded, kept):
    from mirrordash_core.features.kiosk import routes
    from mirrordash_core.features.power.display_power import display_power_manager
    monkeypatch.setenv("MIRRORDASH_BOOT_STATUS", boot_status)
    monkeypatch.setattr(display_power_manager, "is_on", screen_on)
    monkeypatch.setattr(routes.manager, "screen_loaded", page_loaded)
    response = Response()
    asyncio.run(routes.health(response, trial=True))
    assert (response.status_code == 200) == kept
    # Everyone else (the screen's start page, the watch) gets an answer either way
    response = Response()
    asyncio.run(routes.health(response))
    assert response.status_code == 200


def test_a_broken_download_goes_on_where_it_stopped(tmp_path, monkeypatch):
    data = bytes(range(256)) * 40
    target, asked = tmp_path / "os-update.raucb", []
    partial = download.partial_file(target, "20261101-1200")
    (tmp_path / "os-update.20261001-0800.part").write_bytes(b"an older build")

    class Answer:
        def __init__(self, body, status, breaks=False):
            self.body, self.status, self.breaks = body, status, breaks

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _size=-1):
            if self.breaks and not self.body:
                raise OSError("connection lost")
            body, self.body = self.body, b""
            return body

    def urlopen(request, timeout):
        start = int(request.get_header("Range", "bytes=0-")[6:-1])
        asked.append(start)
        return Answer(data[start:4000] if len(asked) == 1 else data[start:], 206 if start else 200, breaks=len(asked) == 1)

    monkeypatch.setattr(download.urllib.request, "urlopen", urlopen)
    with pytest.raises(OSError):
        download.download("https://example.com/b", len(data), partial, target)
    assert partial.stat().st_size == 4000 and not target.exists()
    assert not (tmp_path / "os-update.20261001-0800.part").exists()
    download.download("https://example.com/b", len(data), partial, target)
    assert asked == [0, 4000] and target.read_bytes() == data and not partial.exists()
    # A file of another size than GitHub named is never handed to the install
    monkeypatch.setattr(download.urllib.request, "urlopen", lambda request, timeout: Answer(data[:100], 200))
    target.unlink()
    with pytest.raises(OSError, match="got 100 of"):
        download.download("https://example.com/b", len(data), partial, target)
    assert not target.exists()


def test_an_installed_update_about_to_restart_is_no_failed_trial(tmp_path, monkeypatch):
    """After rauc install the old system runs on for some seconds: a check then must not count a failure."""
    pending = tmp_path / "pending"
    pending.write_text("20261101-1200")
    monkeypatch.setattr(service, "OS_PENDING_FILE", pending)
    with patch("mirrordash_core.admin.job_status", return_value={"state": "restarting"}):
        assert service.failed_os_update("20261001-0800") is None
    assert pending.exists()


def test_a_system_install_that_fails_lets_the_rest_go_first_the_next_night():
    bundle = {"build": "20261101-1200", "tag": "v1-os1"}
    os_info = {"update_available": True, "bundle": bundle, "failed_build": None}
    app = {"update_available": True, "latest_version": "1.0.0", "needs_python": None}
    started = []
    service.os_install_failed.clear()
    with patch.object(service, "load_config", return_value={"system": {}}), \
         patch.object(service, "check_os_update", AsyncMock(return_value=os_info)), \
         patch.object(service, "check_core_update", AsyncMock(return_value=app)), \
         patch.object(service, "update_os", AsyncMock(side_effect=RuntimeError("no room"))), \
         patch("mirrordash_core.admin.job_status", return_value={"state": "idle"}), \
         patch("mirrordash_core.admin.start_job", side_effect=lambda work, failure=None: started.append((work, failure))), \
         patch("mirrordash_core.config.save_config"):
        assert asyncio.run(service.auto_update()) == "system"
        assert started[0][1]  # a failure is told on the bell
        with pytest.raises(RuntimeError):
            asyncio.run(started[0][0]())
        assert asyncio.run(service.auto_update()) == "mirrordash"  # the night after
        assert asyncio.run(service.auto_update()) == "system"  # and the system again after that
    service.os_install_failed.clear()


def test_modules_turned_off_for_a_newer_python_run_again_after_a_rollback(monkeypatch):
    config = {"modules": {"a": {"enabled": False, "disabled_reason": "doesn't work with Python 9.99"},
                          "b": {"enabled": False}}}
    saved = SimpleNamespace(config=None)
    monkeypatch.setattr(service, "load_config", lambda: config)
    with patch("mirrordash_core.config.save_config", side_effect=lambda c: setattr(saved, "config", c)):
        service.turn_modules_back_on_after_rollback()  # a normal start: nothing
        assert saved.config is None
        monkeypatch.setenv("MIRRORDASH_BOOT_STATUS", "rollback")
        service.turn_modules_back_on_after_rollback()
    assert saved.config["modules"] == {"a": {"enabled": True}, "b": {"enabled": False}}  # b was the owner's choice


def test_a_mirror_that_was_off_at_night_catches_up_only_when_asked(tmp_path, monkeypatch):
    from mirrordash_core.features.updates import catch_up, routes
    monkeypatch.setattr(catch_up, "_stamp", lambda: tmp_path / "nightly-update-at")
    monkeypatch.setattr(catch_up, "AFTER_START_SECONDS", 0)
    monkeypatch.setattr(service, "TRYBOOT_FLAG", tmp_path / "tryboot")
    system, rounds = {"auto_update_catch_up": False}, []

    async def nightly_round():
        rounds.append(1)
    monkeypatch.setattr(catch_up, "load_config", lambda: {"system": system})
    monkeypatch.setattr(routes, "nightly_round", nightly_round)
    asyncio.run(catch_up.catch_up())
    assert rounds == []  # off unless the owner turned it on
    system["auto_update_catch_up"] = True
    asyncio.run(catch_up.catch_up())
    assert rounds == [1]  # never ran here: it does now
    catch_up.note_round()
    asyncio.run(catch_up.catch_up())
    assert rounds == [1]  # the night's round ran: nothing was missed
    now = float((tmp_path / "nightly-update-at").read_text())
    assert not catch_up.missed(now + 19 * 3600) and catch_up.missed(now + 21 * 3600)
    assert not catch_up.missed(now - 400 * 86400)  # a clock that isn't set yet: no restart on a guess
    (tmp_path / "nightly-update-at").write_text("0")
    (tmp_path / "tryboot").write_bytes(service.TRYBOOT_ON)
    asyncio.run(catch_up.catch_up())
    assert rounds == [1]  # a new system on trial is busy proving itself
    system["auto_update"] = False
    (tmp_path / "tryboot").unlink()
    asyncio.run(catch_up.catch_up())
    assert rounds == [1]  # automatic updates are off


def test_a_module_update_that_fails_on_trial_is_put_back(monkeypatch):
    """Its version before goes back (one link in the site), the others stay, and the nightly update leaves
    that version alone; once the trial is over a module is paused as before."""
    from mirrordash_core import notifications
    from mirrordash_core.features.modules import installed, trial
    from mirrordash_core.features.modules.client import module_host
    config, asked = {"modules": {}, "system": {}}, []

    async def site(op, wait, **fields):
        asked.append((op, fields["name"]))
        return (0, "") if fields["name"] == "mirrordash-news" else (1, "no version before")
    monkeypatch.setattr(trial, "load_config", lambda: config)
    monkeypatch.setattr(trial, "save_config", lambda saved: None)
    monkeypatch.setattr(module_host, "_site", site)
    monkeypatch.setattr(installed, "installed_modules", lambda: [
        {"name": "mirrordash_news", "package_name": "mirrordash-news", "version": "2.0.1", "type": "pypi"}])
    trial.start(["mirrordash-news", "mirrordash-new"], now=1000.0)
    assert trial.on_trial(config, now=1000.0 + trial.WINDOW - 1) == {"mirrordash-news", "mirrordash-new"}
    assert trial.on_trial(config, now=1000.0 + trial.WINDOW + 1) == set()
    assert trial.on_trial(config, now=10.0) == set()  # the clock went back: no trial that lasts for days
    monkeypatch.setattr(trial.time, "time", lambda: 1100.0)
    assert asyncio.run(trial.put_back("mirrordash-other")) == [] and asked == []  # not on trial
    assert asyncio.run(trial.put_back()) == ["mirrordash-news"]  # the new one has no version before: it stays
    assert config["system"]["auto_update_tries"] == {"mirrordash-news": {"version": "2.0.1", "times": service.UPDATE_TRIES}}
    assert trial.on_trial(config) == {"mirrordash-new"}
    assert "News 2.0.1 didn't work and was put back" in notifications.items()[0]["text"]


def test_a_screen_that_crashes_after_a_module_update_gets_the_version_before_not_a_pause(monkeypatch):
    from mirrordash_core.features.modules import guard, trial
    config = {"modules": {"mirrordash-news": {"enabled": True, "position": "top_left"}},
              guard.KEY: {"crashes": [guard.time.time() - 60], "changed": {"mirrordash-news": 5.0}}}
    put_back, saved, restarts = AsyncMock(return_value=["mirrordash-news"]), [], []
    monkeypatch.setattr(guard, "load_config", lambda: config)
    monkeypatch.setattr(guard, "save_config", saved.append)
    monkeypatch.setattr(trial, "put_back", put_back)
    with patch("mirrordash_core.host.run_restart", new=lambda: restarts.append(1)), \
         patch("mirrordash_core.host.run_in_background", new=lambda work: None):
        asyncio.run(guard.page_crashed(set()))
    put_back.assert_awaited_once()
    assert restarts == [1] and not saved[-1][guard.KEY].get("paused") and saved[-1][guard.KEY]["crashes"] == []
    # Nothing on trial: the guard pauses as it always did
    put_back.return_value = []
    config[guard.KEY] = {"crashes": [guard.time.time() - 60], "changed": {"mirrordash-news": 5.0}}
    with patch("mirrordash_core.features.modules.client.module_host.reload_modules", AsyncMock()), \
         patch("mirrordash_core.features.power.privacy.broadcast_screen", AsyncMock()):
        asyncio.run(guard.page_crashed(set()))
    assert saved[-1][guard.KEY]["paused"] == ["mirrordash-news"]
