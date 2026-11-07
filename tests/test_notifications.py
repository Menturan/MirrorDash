"""Notifications (mirrordash_core/notifications.py): the store, saved logs and new versions turned into
notifications, the bell's routes, and power-watch's marker."""

import os
import subprocess
from datetime import datetime

from fastapi.testclient import TestClient

from mirrordash_core import notifications
from mirrordash_core.features.logs import saved
from mirrordash_core.features.notifications import routes, versions, watch
from tests.test_configure_image import unit


def test_kept_newest_first_and_capped():
    for n in range(notifications.KEEP + 5):
        notifications.add("info", f"n{n}", time=datetime.fromisoformat(f"2026-11-07T10:{n // 60:02}:{n % 60:02}+00:00"))
    texts = [item["text"] for item in notifications.items()]
    assert len(texts) == notifications.KEEP and texts[0] == f"n{notifications.KEEP + 4}" and "n4" not in texts


def test_unread_errors_and_marking_read():
    notifications.add("warning", "w")
    notifications.add("error", "e")
    assert notifications.unread_count() == 2 and notifications.has_unread_error()
    error_id = notifications.items()[0]["id"]
    notifications.mark_read(error_id)
    assert not notifications.has_unread_error() and notifications.unread_count() == 1
    notifications.mark_read()
    assert notifications.unread_count() == 0


def test_once_is_said_once():
    notifications.add("error", "X 1.0 failed three nights", once=True)
    notifications.add("error", "X 1.0 failed three nights", once=True)
    assert len(notifications.items()) == 1


def test_saved_logs_become_notifications(tmp_path, monkeypatch):
    monkeypatch.setattr(saved, "SAVED_LOG_DIR", tmp_path / "logs")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "wifi-lost-20261101-010000.log").write_text("old")
    watch.sync_saved_logs()
    assert notifications.items() == []  # the first look only remembers what is there
    (tmp_path / "logs" / "app-safe-mode-20261107-020000.log").write_text("x")
    (tmp_path / "logs" / "power-lost-20261107-030000.log").write_text("x")
    watch.sync_saved_logs()
    watch.sync_saved_logs()  # nothing new: nothing added
    by_log = {item["log"]: item for item in notifications.items()}
    assert len(by_log) == 2
    assert by_log["app-safe-mode-20261107-020000.log"]["level"] == "error"
    assert by_log["power-lost-20261107-030000.log"]["level"] == "warning"
    assert by_log["power-lost-20261107-030000.log"]["text"] == saved.KINDS["power-lost"]


def test_only_a_higher_version_is_news():
    versions.note_new_versions({"MirrorDash": "0.15.1", "System": "20261018-2237", "module:Clock": "2.0.0"})
    assert notifications.items() == []  # the first start only remembers
    versions.note_new_versions({"MirrorDash": "0.16.0", "System": "20261018-2237", "module:Clock": "1.9.0",
                                "module:Weather": "1.0.0"})  # a rollback and a new install aren't news
    assert [item["text"] for item in notifications.items()] == ["MirrorDash updated to 0.16.0"]


def test_when_is_said_as_people_do():
    now = datetime(2026, 11, 7, 12, 0).astimezone()
    today = now.replace(hour=7, minute=12)
    assert routes.when(today.isoformat(), now) == "Today 07:12"
    assert routes.when(today.replace(day=6).isoformat(), now) == "Yesterday 07:12"
    assert routes.when(today.replace(day=5).isoformat(), now) == "Nov 5 07:12"


def test_the_bell_lists_and_marks_read(monkeypatch):
    from mirrordash_core.admin import require_api_key
    from mirrordash_core.app import app
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        notifications.add("error", "Clock kept crashing")
        client = TestClient(app)
        assert ">1<" in client.get("/admin/notifications/count").text
        listed = client.get("/admin/notifications").text
        assert "Clock kept crashing" in listed and "Mark all read" in listed
        response = client.post(f"/admin/notifications/{notifications.items()[0]['id']}/read")
        assert "md-notifications" in response.headers["HX-Trigger-After-Swap"] and "Mark all read" not in response.text
        assert "hidden" in client.get("/admin/notifications/count").text
    finally:
        app.dependency_overrides.clear()


def test_power_watch_tells_a_start_without_a_shutdown(tmp_path):
    script = tmp_path / "power-watch"
    script.write_text(unit("/usr/local/lib/mirrordash/power-watch"))
    save_log = tmp_path / "save-log"
    save_log.write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/saved\n')
    save_log.chmod(0o755)
    marker = tmp_path / "running"
    env = {"PATH": "/usr/bin:/bin", "MIRRORDASH_RUNNING_FILE": str(marker), "MIRRORDASH_SAVE_LOG": str(save_log)}

    def run(arg):
        subprocess.run(["bash", str(script), arg], env=env, check=True, capture_output=True)

    run("start")
    run("stop")
    run("start")  # shut down properly before: nothing to tell
    assert not (tmp_path / "saved").exists() and marker.exists()
    run("start")  # no stop in between: the power went
    assert (tmp_path / "saved").read_text() == "power-lost -b\n"
    os.remove(marker)
