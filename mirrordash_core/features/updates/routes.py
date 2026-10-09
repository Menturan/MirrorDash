# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import job_response, require_api_key, start_job
from mirrordash_core.features.updates.service import check_core_update, update_core, rebuild_venv

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
        """)
    else:
        return HTMLResponse(content=f'<div style="margin-top: 10px; color: var(--text-muted);">Your system is up-to-date (v{current}).</div>')


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update():
    job_id = start_job(update_core)
    return job_response(job_id, "Updating MirrorDash", "Installing the new version. This can take a few minutes...",
                        "MirrorDash was updated successfully.")


@router.post("/panels/system/rebuild-venv", dependencies=[Depends(require_api_key)])
async def trigger_rebuild_venv():
    job_id = start_job(rebuild_venv)
    return job_response(job_id, "Rebuilding Environment", "Reinstalling MirrorDash and its modules. This can take several minutes...",
                        "Environment rebuilt successfully.")
