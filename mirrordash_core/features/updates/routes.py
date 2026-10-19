# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import (
    job_response,
    notify,
    require_api_key,
    start_job,
    templates,
)
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
    app, os_update = await asyncio.gather(check_core_update(), check_os_update(), return_exceptions=True)
    system_first = isinstance(os_update, dict) and os_update.get("update_available", False)
    content = _app_update_html(app, system_first)
    if not (isinstance(os_update, dict) and not os_update.get("supported")):
        content += f'<div id="os-update-result" hx-swap-oob="innerHTML">{_os_update_html(os_update)}</div>'
    return HTMLResponse(content=content)


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
    """The OS image's nightly timer (mirrordash-auto-update.timer) calls this. Only from the mirror
    itself, so it needs no password; what it may do is what Settings → Updates already allows."""
    if request.client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(status_code=403, detail="Only from the mirror itself")
    started = await auto_update()
    logger.info(f"Automatic update: {started}")
    return {"started": started}
