# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import (
    from_the_mirrors_os,
    job_response,
    notify,
    require_api_key,
    start_job,
    templates,
)
from mirrordash_core.features.modules.service import module_updates
from mirrordash_core.features.updates.service import (
    auto_update,
    check_core_update,
    check_os_update,
    format_build,
    incompatible,
    rebuild_venv,
    update_core,
    update_os,
)

logger = logging.getLogger("mirrordash.core.updates")
router = APIRouter(prefix="/admin")


@router.get("/panels/system/update-check", dependencies=[Depends(require_api_key)])
async def get_system_update_check():
    """One check, two answers: MirrorDash's goes into #core-update-result (the response itself), the
    system's into #os-update-result (an out-of-band swap; that section only exists on A/B images).
    When both have an update, the system goes first: a new MirrorDash can count on something only
    the new system has, while a new system works with the MirrorDash already installed."""
    # ponytail: the modules' answers may be a minute old, so tapping again and again can't use up GitHub's
    # 60 requests an hour (one per module installed from GitHub)
    app, os_update, modules = await asyncio.gather(check_core_update(), check_os_update(), module_updates(max_age=60),
                                                   return_exceptions=True)
    system_first = isinstance(os_update, dict) and os_update.get("update_available", False)
    content = _app_update_html(app, system_first)
    if not (isinstance(os_update, dict) and not os_update.get("supported")):
        content += f'<div id="os-update-result" hx-swap-oob="innerHTML">{_os_update_html(os_update)}</div>'
    if isinstance(modules, BaseException):
        modules_html, checked = _render("update_modules.html", error=str(modules)), time.time()
    else:
        modules_html, checked = _render("update_modules.html", updates=modules[0]), min(time.time(), modules[1] or time.time())
    content += f'<div id="modules-update-result" hx-swap-oob="innerHTML">{modules_html}</div>'
    content += f'<span id="update-checked-at" hx-swap-oob="innerHTML">Last checked {_clock(checked)}</span>'
    return HTMLResponse(content=content)


def _clock(timestamp: float) -> str:
    """A time of day as the mirror shows it (Language & Location → time format)."""
    from mirrordash_core.config import load_config
    twelve_hour = load_config().get("globals", {}).get("time_format") == "12h"
    return datetime.fromtimestamp(timestamp).strftime("%-I:%M %p" if twelve_hour else "%H:%M")


def _app_update_html(data: dict | BaseException, system_first: bool) -> str:
    if isinstance(data, BaseException):
        return _render("update_app.html", error=getattr(data, "detail", None) or str(data))
    blocked = incompatible if incompatible.get("version") == data.get("latest_version") else None
    return _render("update_app.html", data=data, system_first=system_first, blocked=blocked)


def _os_update_html(data: dict | BaseException) -> str:
    """The system's part of the check (A/B OS updates, see service.check_os_update)."""
    if isinstance(data, BaseException):
        return _render("update_os.html", error=getattr(data, "detail", None) or str(data))
    from mirrordash_core.config import load_config
    skip_build = load_config().get("system", {}).get("auto_update_skip_build")
    return _render("update_os.html", data=data, format_build=format_build, skip_build=skip_build)


def _render(name: str, **context) -> str:
    return templates.get_template(name).render(**context)


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update(skip: str = ""):
    """skip: modules (entry point names, comma-separated) to turn off, see "Install Anyway"."""
    modules = tuple(name for name in skip.split(",") if name)
    job_id = start_job(lambda: update_core(modules))
    return job_response(job_id, "Updating MirrorDash", "Installing the new version. This can take a few minutes...",
                        "MirrorDash was updated successfully.")


@router.post("/panels/system/os-update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_os_update():
    # Asked again here rather than taken from the page: only the newest bundle from GitHub is installed
    try:
        data = await check_os_update()
    except Exception as e:
        return notify(f"Failed to check for system updates: {e}", "error")
    if not data.get("update_available"):
        return notify("There is no newer system to install.", "error")
    bundle = data["bundle"]
    job_id = start_job(lambda: update_os(bundle))
    return job_response(job_id, "Updating the System",
                        "Downloading and installing the new system, then restarting. This takes 10-30 minutes; "
                        "keep the mirror plugged in.",
                        "The mirror started the new system. If it doesn't work with it, it goes back to the old one "
                        "by itself within a few minutes.",
                        max_seconds=3600)


@router.post("/panels/system/rebuild-venv", dependencies=[Depends(require_api_key)])
async def trigger_rebuild_venv():
    job_id = start_job(rebuild_venv)
    return job_response(job_id, "Reinstalling MirrorDash", "Installing MirrorDash and its modules again. This can take several minutes...",
                        "MirrorDash and its modules were installed again.")


@router.post("/auto-update")
async def post_auto_update(request: Request) -> dict:
    """The OS image's nightly timer (mirrordash-auto-update.timer) calls this. Only from the mirror's
    OS (the local token), so it needs no password; what it may do is what Settings → Updates already allows."""
    if not from_the_mirrors_os(request):
        raise HTTPException(status_code=403, detail="Only from the mirror itself")
    started = await auto_update()
    logger.info(f"Automatic update: {started}")
    return {"started": started}
