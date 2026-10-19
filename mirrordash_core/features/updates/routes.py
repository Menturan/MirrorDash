# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import html
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import job_response, notify, require_api_key, start_job
from mirrordash_core.features.updates.service import (check_core_update, check_os_update, format_build, incompatible,
                                                      rebuild_venv, update_core, update_os)

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
        detail = getattr(data, "detail", None) or str(data)
        return f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for MirrorDash updates: {html.escape(detail)}</div>'
    current = html.escape(data.get("current_version", "—"))
    latest = html.escape(data.get("latest_version", "—"))
    if not data.get("update_available", False):
        return f'<div style="margin-top: 10px; color: var(--text-muted);">MirrorDash is up-to-date (v{current}).</div>'
    if system_first:
        button = """<button type="button" class="btn primary btn-sm" style="margin-top: 10px;" disabled>
                    Install the system update first
                </button>"""
    elif incompatible.get("version") == data.get("latest_version"):
        # The last try stopped because some modules don't work with the newer Python it needs
        names = ", ".join(incompatible["modules"])
        reasons = "".join(f"<li><strong>{html.escape(name)}</strong>: {html.escape(reason)}</li>"
                          for name, reason in incompatible["modules"].items())
        button = f"""<p style="margin: 8px 0 0 0;">v{latest} needs Python {html.escape(incompatible["python"])}. These modules
                    don't work with it yet:</p><ul style="margin: 4px 0 0 18px;">{reasons}</ul>
                <p style="margin: 6px 0 0 0; color: var(--text-muted);">Installing anyway turns them off (their settings
                    are kept); install them again from Modules once their makers have updated them. Or wait.</p>
                <button type="button" class="btn primary btn-sm" style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger?skip={html.escape(",".join(incompatible["modules"]))}"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Install MirrorDash v{latest} and turn off {html.escape(names)}?"
                        hx-disabled-elt="this">
                    Install Anyway
                </button>"""
    else:
        python_note = (f'<p style="margin: 6px 0 0 0; color: var(--text-muted);">It needs Python {html.escape(data["needs_python"])}: '
                       "the mirror installs it and first checks that every module works with it.</p>"
                       if data.get("needs_python") else "")
        button = python_note + f"""<button type="button" class="btn primary btn-sm" style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Install MirrorDash v{latest}? It takes a minute or two, and MirrorDash restarts afterwards."
                        hx-disabled-elt="this">
                    Install MirrorDash v{latest}
                </button>"""
    return f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(160, 255, 186, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #a0ffba;"><strong>MirrorDash update available!</strong> Version v{latest} is ready to install (installed now: v{current}).</p>
                {'<p style="margin: 6px 0 0 0; color: var(--text-muted);">There is a system update too: install that first, then this one.</p>' if system_first else ''}
                {button}
            </div>"""


def _os_update_html(data: dict | BaseException) -> str:
    """The system's part of the check (A/B OS updates, see service.check_os_update)."""
    if isinstance(data, BaseException):
        detail = getattr(data, "detail", None) or str(data)
        return f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for system updates: {html.escape(detail)}</div>'
    installed = html.escape(format_build(data["installed_build"]))
    parts = []
    if data["failed_build"]:
        parts.append(f"""
            <div style="margin-top: 10px; padding: 10px; border: 1px solid var(--color-status-warning); border-radius: 6px;">
                <strong>The system update (build {html.escape(format_build(data["failed_build"]))}) didn't start properly.</strong>
                The mirror went back to the system it had (build {installed}). Nothing was lost.
                The log is under Logs → Saved Logs.
            </div>""")
    bundle = data["bundle"]
    if data["update_available"]:
        tag = html.escape(bundle["tag"])
        parts.append(f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(160, 255, 186, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #a0ffba;"><strong>System update available!</strong> {tag} (build {html.escape(format_build(bundle["build"]))}).
                About {bundle["size"] // 1048576} MB to download.</p>
                <button type="button" class="btn primary btn-sm" style="margin-top: 10px;"
                        hx-post="/admin/panels/system/os-update-trigger"
                        hx-target="#os-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Install the system update {tag}? It takes 10-30 minutes and the mirror restarts. If the new system doesn't work, the mirror goes back to the current one by itself."
                        hx-disabled-elt="this">
                    Install System Update
                </button>
            </div>""")
    else:
        parts.append(f'<div style="margin-top: 10px; color: var(--text-muted);">The system is up-to-date (build {installed}).</div>')
    return "".join(parts)


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
