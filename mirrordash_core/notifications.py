# SPDX-License-Identifier: AGPL-3.0-only
"""Notifications for the mirror's owner: what happened to their mirror, in plain words (an update, a
module that kept crashing, a power cut), for the bell on the admin page (features/notifications/).

Kept in notifications.json in the data folder, the newest first, at most KEEP of them, read or not. Three
levels: info, warning and error. While an error is unread, the mirror's screen shows a hint to read it
on the admin page ({"type": "notices"} over the websocket, and in the screen's "hello").

Shared by the features that have something to tell (updates, modules, the saved logs' watch), so it
sits here at the package root."""

import json
import logging
import uuid
from datetime import UTC, datetime

from mirrordash_core.config import get_base_dir, write_atomic

logger = logging.getLogger("mirrordash.core.notifications")

KEEP = 50
LEVELS = ("info", "warning", "error")


def _path():
    return get_base_dir() / "notifications.json"


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError):
        pass  # none yet, or a broken file: start over (it's only history)
    return {"items": []}


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        write_atomic(_path(), json.dumps(data, indent=1))
    except OSError as e:
        logger.warning(f"Could not save the notifications: {e}")


def items() -> list[dict]:
    """All notifications, the newest first."""
    return _load()["items"]


def unread_count() -> int:
    return sum(not item.get("read") for item in items())


def has_unread_error(data: dict | None = None) -> bool:
    return any(item.get("level") == "error" and not item.get("read") for item in (data or _load())["items"])


def add(level: str, text: str, log: str | None = None, time: datetime | None = None, once: bool = False) -> None:
    """A new notification; log is the name of a saved log that tells more (features/logs/saved.py). once: not
    when one with the same text is still kept (what the nightly update says every night it waits).
    ponytail: "once" lasts while that notification is among the KEEP newest."""
    if level not in LEVELS:
        level = "info"
    data = _load()
    if once and any(item.get("text") == text for item in data["items"]):
        return
    error_before = has_unread_error(data)
    data["items"].insert(0, {"id": uuid.uuid4().hex[:12], "time": (time or datetime.now(UTC)).isoformat(timespec="seconds"),
                             "level": level, "text": text, "log": log, "read": False})
    data["items"].sort(key=lambda item: item["time"], reverse=True)  # a saved log's own time may be older
    del data["items"][KEEP:]
    _save(data)
    logger.info(f"Notification ({level}): {text}")
    _tell_screen(error_before, data)


def mark_read(notification_id: str | None = None) -> None:
    """Mark one notification read, or all of them (None)."""
    data = _load()
    error_before = has_unread_error(data)
    for item in data["items"]:
        if notification_id is None or item.get("id") == notification_id:
            item["read"] = True
    _save(data)
    _tell_screen(error_before, data)


def seen_logs() -> set[str] | None:
    """The saved logs already turned into notifications (features/notifications/watch.py); None before the first look."""
    seen = _load().get("seen_logs")
    return set(seen) if isinstance(seen, list) else None


def set_seen_logs(names: set[str]) -> None:
    data = _load()
    data["seen_logs"] = sorted(names)
    _save(data)


def last_versions() -> dict | None:
    """The versions that ran at the last start (features/notifications/versions.py); None before the first."""
    versions = _load().get("versions")
    return versions if isinstance(versions, dict) else None


def set_last_versions(versions: dict) -> None:
    data = _load()
    data["versions"] = versions
    _save(data)


def _tell_screen(error_before: bool, data: dict) -> None:
    """The mirror's screen shows its hint while an error is unread: tell it when that changes."""
    error = has_unread_error(data)
    if error == error_before:
        return
    from mirrordash_core.features.kiosk.ws import manager
    from mirrordash_core.host import run_in_background
    message = manager.broadcast({"type": "notices", "error": error})
    try:
        run_in_background(message, name="notices")
    except RuntimeError:  # no event loop (a script or a test): the screen hears it in its next "hello"
        message.close()
