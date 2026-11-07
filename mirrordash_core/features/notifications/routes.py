# SPDX-License-Identifier: AGPL-3.0-only
"""The bell on the admin page: the unread count, the list of notifications (mirrordash_core/notifications.py)
and marking them read. Every change fires md-notifications, so the count follows."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request

from mirrordash_core import notifications
from mirrordash_core.admin import events_header, require_api_key, templates
from mirrordash_core.features.logs.saved import saved_logs

router = APIRouter(prefix="/admin/notifications", dependencies=[Depends(require_api_key)])


def when(iso_time: str, now: datetime | None = None) -> str:
    """"Today 07:12", "Yesterday 03:44" or "Nov 5 22:10", in the mirror's time zone."""
    try:
        time = datetime.fromisoformat(iso_time).astimezone()
    except ValueError:
        return ""
    today = (now or datetime.now().astimezone()).date()
    day = {today: "Today", today - timedelta(days=1): "Yesterday"}.get(time.date(), f"{time:%b} {time.day}")
    return f"{day} {time:%H:%M}"


def _list(request: Request, **headers):
    logs = {log["filename"] for log in saved_logs()}  # a link only to a log that is still there
    items = [{**item, "when": when(item["time"]), "log": item.get("log") if item.get("log") in logs else None}
             for item in notifications.items()]
    return templates.TemplateResponse(request=request, name="admin_notifications.html",
                                      context={"items": items}, headers=headers)


@router.get("")
async def get_notifications(request: Request):
    return _list(request)


@router.get("/count")
async def get_count(request: Request):
    return templates.TemplateResponse(request=request, name="admin_notifications_count.html",
                                      context={"count": notifications.unread_count()})


@router.post("/read-all")
async def read_all(request: Request):
    notifications.mark_read()
    return _list(request, **events_header(**{"md-notifications": True}))


@router.post("/{notification_id}/read")
async def read_one(request: Request, notification_id: str):
    notifications.mark_read(notification_id)
    return _list(request, **events_header(**{"md-notifications": True}))
