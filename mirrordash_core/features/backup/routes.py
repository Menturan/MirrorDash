# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import os
import shutil
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from mirrordash_core.admin import job_response, notify, require_api_key, start_job, templates
from mirrordash_core.features.backup.service import (backup_file, create_backup, delete_backup, list_backups,
                                                     read_manifest, restore_backup, temp_upload)

logger = logging.getLogger("mirrordash.core.backup")
router = APIRouter(prefix="/admin")


def render_validation_summary(filename: str, manifest: dict, password: str = "", is_local: bool = False) -> str:
    modules = manifest.get("modules", [])
    modules_list_items = []
    for mod in modules:
        package_name = mod.get("package_name")
        version = mod.get("version")
        mod_type = mod.get("type", "pypi")
        type_badge = '<span style="color: #66ff66;">[Local Source]</span>' if mod_type == "local" else '<span style="color: #66b3ff;">[PyPI Package]</span>'
        modules_list_items.append(f"<li><strong>{package_name}</strong> (v{version}) - {type_badge}</li>")

    modules_list_html = "\n".join(modules_list_items)
    password_input = f'<input type="hidden" name="password" value="{password}">' if password else ''

    return f"""
    <section class="card" id="backup-validation-panel">
        <h2><i class="fas fa-clipboard-check"></i> Verify Import Contents</h2>
        <div class="validation-summary">
            <div class="validation-stat">
                <span class="label">Manifest Version:</span>
                <span class="value">{manifest.get('backup_version', '1.0')}</span>
            </div>
            <div class="validation-stat">
                <span class="label">Backup Timestamp:</span>
                <span class="value">{manifest.get('timestamp', '-')}</span>
            </div>
            <div class="validation-stat">
                <span class="label">Modules Included:</span>
                <span class="value">{len(modules)}</span>
            </div>
        </div>
        <div class="validation-modules-list" style="margin-top: 1rem;">
            <h4>Restored Modules Listing:</h4>
            <ul>
                {modules_list_html}
            </ul>
        </div>
        <div class="validation-actions" style="margin-top: 1.5rem; display: flex; gap: 10px;">
            <form hx-post="/admin/panels/backup/restore" 
                  hx-target="#backup-validation-panel" 
                  hx-swap="outerHTML"
                  onclick="this.querySelector('button').disabled = true; this.querySelector('span').innerText = 'Restoring...';">
                <input type="hidden" name="filename" value="{filename}">
                <input type="hidden" name="is_local" value="{"true" if is_local else "false"}">
                {password_input}
                <button type="submit" class="btn primary">
                    <i class="fas fa-exclamation-triangle"></i> <span>Start Restoration</span>
                </button>
            </form>
            <button type="button" class="btn secondary" onclick="document.getElementById('backup-validation-panel').remove()">
                Cancel
            </button>
        </div>
    </section>
    """


def render_password_prompt(filename: str, is_local: bool) -> str:
    action_url = "/admin/panels/backup/validate-password"
    is_local_input = f'<input type="hidden" name="is_local" value="{"true" if is_local else "false"}">'
    return f"""
    <section class="card" id="backup-password-prompt-panel" style="margin-top: 1rem;">
        <h2><i class="fas fa-lock"></i> Encrypted Backup</h2>
        <p>This backup file is encrypted. Please enter the password to decrypt and validate it:</p>
        <form hx-post="{action_url}" hx-target="#backup-upload-target" hx-swap="innerHTML" style="margin-top: 1rem;">
            <input type="hidden" name="filename" value="{filename}">
            {is_local_input}
            <div class="form-group">
                <div class="pw-field"><input type="password" id="backup-restore-password" name="password" class="form-control" placeholder="Enter password" required><button type="button" class="pw-reveal" data-reveal="backup-restore-password" aria-controls="backup-restore-password" aria-pressed="false">Show</button></div>
            </div>
            <div style="margin-top: 1rem; display: flex; gap: 10px;">
                <button type="submit" class="btn primary">Verify Password</button>
                <button type="button" class="btn secondary" onclick="document.getElementById('backup-password-prompt-panel').remove()">Cancel</button>
            </div>
        </form>
    </section>
    """


async def _list_backups() -> list[dict]:
    from datetime import datetime
    backups = []
    for backup in (await list_backups()).get("backups", []):
        created = backup["created_at"]
        try:
            created = datetime.fromisoformat(created).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
        backups.append({**backup, "created_at_formatted": created,
                        "size_formatted": f"{backup['size_bytes'] / 1024:.1f} KB"})
    return backups


@router.get("/panels/backup", dependencies=[Depends(require_api_key)])
async def get_panel_backup(request: Request):
    return templates.TemplateResponse(request=request, name="admin_backup.html",
                                      context={"backups": await _list_backups()})


@router.get("/panels/backup/list", dependencies=[Depends(require_api_key)])
async def get_panel_backups_list(request: Request):
    return templates.TemplateResponse(request=request, name="admin_backup_rows.html",
                                      context={"backups": await _list_backups()})


@router.get("/backup/download/{filename}", dependencies=[Depends(require_api_key)])
async def download_backup(filename: str):
    return FileResponse(backup_file(filename), filename=filename, media_type="application/octet-stream")


@router.post("/panels/backup/delete/{filename}", dependencies=[Depends(require_api_key)])
async def delete_panel_backup_route(filename: str):
    await delete_backup(filename)
    return HTMLResponse(content="")


def _summary_or_password_prompt(filename: str, is_local: bool, password: str | None = None) -> HTMLResponse:
    """What the backup in temp_upload() holds, or a password prompt when it's encrypted."""
    try:
        manifest = read_manifest(temp_upload(), password)
    except Exception as e:
        logger.error(f"Not a backup file: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Invalid or corrupt backup archive.</div>')
    if manifest is None:
        wrong = '<div class="alert alert--error" style="margin-bottom: 1rem;">Invalid backup password.</div>' if password else ""
        return HTMLResponse(content=wrong + render_password_prompt(filename, is_local))
    return HTMLResponse(content=render_validation_summary(filename, manifest, password or "", is_local))


@router.post("/panels/backup/upload", dependencies=[Depends(require_api_key)])
async def upload_panel_backup(file: UploadFile = File(...)):
    if not file.filename.endswith(".mirror"):
        return HTMLResponse(content='<div class="alert alert--error">Invalid file type. File must have .mirror extension.</div>')
    with open(temp_upload(), "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return _summary_or_password_prompt(file.filename, is_local=False)


@router.post("/panels/backup/validate-local", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_local(filename: str = Form(...)):
    try:
        shutil.copy(backup_file(filename), temp_upload())
    except HTTPException as e:
        return HTMLResponse(content=f'<div class="alert alert--error">{e.detail}.</div>')
    return _summary_or_password_prompt(filename, is_local=True)


@router.post("/panels/backup/validate-password", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_password(filename: str = Form(...), password: str = Form(...), is_local: bool = Form(...)):
    if not os.path.exists(temp_upload()):
        return HTMLResponse(content='<div class="alert alert--error">No uploaded backup found to validate.</div>')
    return _summary_or_password_prompt(filename, is_local, password)


@router.post("/panels/backup/restore", dependencies=[Depends(require_api_key)])
async def restore_panel_backup(
    filename: str = Form(...),
    password: str | None = Form(default=None),
    is_local: bool = Form(default=False)
):
    job_id = start_job(lambda: restore_backup(password=password))
    return job_response(job_id, "Restoring Backup", "Restoring settings and modules. This can take a few minutes...",
                        "Backup restored successfully.")


@router.post("/panels/backup/create", dependencies=[Depends(require_api_key)])
async def create_panel_backup(request: Request):
    form_data = await request.form()
    encrypt = form_data.get("encrypt") == "true"
    password = form_data.get("password")

    if encrypt and (not password or len(password) < 4):
        return notify("Password must be at least 4 characters for encryption.", "error")

    res = await create_backup(password if encrypt else None)
    filename = res.get("filename")

    return notify(f"Backup {filename} created.", refreshBackups=True, **{"backup-created": True})
