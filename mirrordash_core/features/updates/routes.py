# Licensed under the PolyForm Noncommercial License 1.0.0.

import html
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import job_response, notify, require_api_key, start_job
from mirrordash_core.features.updates.service import check_core_update, check_os_update, update_core, update_os, rebuild_venv

logger = logging.getLogger("mirrordash.core.updates")
router = APIRouter(prefix="/admin")


@router.get("/panels/system/update-check", dependencies=[Depends(require_api_key)])
async def get_system_update_check():
    try:
        data = await check_core_update()
    except Exception as e:
        return HTMLResponse(content=f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for updates: {str(e)}</div>')

    current = data.get("current_version", "—")
    latest = data.get("latest_version", "—")
    avail = data.get("update_available", False)

    if avail:
        return HTMLResponse(content=f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(160, 255, 186, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #a0ffba;"><strong>Update available!</strong> New version v{latest} is available (currently installed: v{current}).</p>
                <button type="button" 
                        class="btn primary btn-sm" 
                        style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Are you sure you want to upgrade MirrorDash Core to v{latest}? MirrorDash will restart afterwards."
                        hx-disabled-elt="this">
                    Upgrade to v{latest} Now
                </button>
            </div>
        """ + await os_update_html())
    else:
        return HTMLResponse(content=f'<div style="margin-top: 10px; color: var(--text-muted);">Your system is up-to-date (v{current}).</div>'
                            + await os_update_html())


async def os_update_html() -> str:
    """The OS part of the update check (A/B OS updates, see service.check_os_update). Empty on
    mirrors without OS updates. Shown after the app's own result."""
    try:
        data = await check_os_update()
    except Exception as e:
        return f'<div class="status-msg error" style="margin-top: 10px;">Failed to check for OS updates: {html.escape(str(e))}</div>'
    if not data["supported"]:
        return ""
    parts = []
    if data["failed_build"]:
        parts.append(f"""
            <div style="margin-top: 10px; padding: 10px; border: 1px solid var(--color-status-warning); border-radius: 6px;">
                <strong>The OS update (build {html.escape(data["failed_build"])}) didn't start properly.</strong>
                The mirror went back to the version it had (build {html.escape(data["installed_build"])}). Nothing was lost.
                Its log is under Logs → Saved Logs.
            </div>""")
    bundle = data["bundle"]
    if data["update_available"]:
        tag = html.escape(bundle["tag"])
        parts.append(f"""
            <div style="margin-top: 10px; padding: 10px; background: rgba(160, 255, 186, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #a0ffba;"><strong>OS update available!</strong> {tag} (build {html.escape(bundle["build"])}) updates the
                mirror's system: Linux, drivers and settings underneath MirrorDash. About {bundle["size"] // 1048576} MB to download.</p>
                <button type="button" class="btn primary btn-sm" style="margin-top: 10px;"
                        hx-post="/admin/panels/system/os-update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Install the OS update {tag}? It takes 10-30 minutes and the mirror restarts. If the new version doesn't work, the mirror goes back to the current one by itself."
                        hx-disabled-elt="this">
                    Install OS Update
                </button>
            </div>""")
    else:
        parts.append(f'<div style="margin-top: 10px; color: var(--text-muted);">The OS is up-to-date (build {html.escape(data["installed_build"])}).</div>')
    return "".join(parts)


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update():
    job_id = start_job(update_core)
    return job_response(job_id, "Updating MirrorDash", "Installing the new version. This can take a few minutes...",
                        "MirrorDash was updated successfully.")


@router.post("/panels/system/os-update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_os_update():
    # Asked again here rather than taken from the page: only the newest bundle from GitHub is installed
    try:
        data = await check_os_update()
    except Exception as e:
        return notify(f"Failed to check for OS updates: {e}", "error")
    if not data.get("update_available"):
        return notify("There is no newer OS version to install.", "error")
    bundle = data["bundle"]
    job_id = start_job(lambda: update_os(bundle))
    return job_response(job_id, "Updating the OS",
                        "Downloading and installing the new OS version, then restarting. This takes 10-30 minutes; "
                        "keep the mirror plugged in.",
                        "The mirror started the new OS version. If it doesn't work with it, it goes back to the old one "
                        "by itself within a few minutes.",
                        max_seconds=3600)


@router.post("/panels/system/rebuild-venv", dependencies=[Depends(require_api_key)])
async def trigger_rebuild_venv():
    job_id = start_job(rebuild_venv)
    return job_response(job_id, "Rebuilding Environment", "Reinstalling MirrorDash and its modules. This can take several minutes...",
                        "Environment rebuilt successfully.")
