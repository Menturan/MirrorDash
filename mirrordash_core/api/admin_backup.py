# Licensed under the PolyForm Noncommercial License 1.0.0.

import json
import logging
import os
import shutil
import zipfile
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import job_response, notify, require_api_key, start_job, templates

logger = logging.getLogger("mirrordash.core.api.admin_backup")

router = APIRouter()


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
    from mirrordash_core.api.backup import list_backups
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


@router.post("/panels/backup/delete/{filename}", dependencies=[Depends(require_api_key)])
async def delete_panel_backup_route(filename: str):
    from mirrordash_core.api.backup import delete_backup
    try:
        await delete_backup(filename=filename)
        return HTMLResponse(content="")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/panels/backup/upload", dependencies=[Depends(require_api_key)])
async def upload_panel_backup(request: Request, file: UploadFile = File(...)):
    from mirrordash_core.api.backup import BACKUPS_DIR

    if not file.filename.endswith(".mirror"):
        return HTMLResponse(content='<div class="alert alert--error">Invalid file type. File must have .mirror extension.</div>')

    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    with open(temp_upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    is_encrypted = False
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.read("backup_manifest.json")
    except RuntimeError as e:
        if "encrypted" in str(e).lower():
            is_encrypted = True
        else:
            raise
    except Exception as e:
        logger.error(f"Failed to read uploaded file: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Invalid or corrupt backup archive.</div>')

    if is_encrypted:
        return HTMLResponse(content=render_password_prompt(file.filename, is_local=False))

    with zipfile.ZipFile(temp_upload_path) as zf:
        manifest_bytes = zf.read("backup_manifest.json")
        manifest = json.loads(manifest_bytes.decode('utf-8'))

    return HTMLResponse(content=render_validation_summary(file.filename, manifest, is_local=False))


@router.post("/panels/backup/validate-local", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_local(filename: str = Form(...)):
    from mirrordash_core.api.backup import BACKUPS_DIR

    if ".." in filename or "/" in filename or "\\" in filename:
        return HTMLResponse(content='<div class="alert alert--error">Invalid filename.</div>')

    file_path = os.path.join(BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        return HTMLResponse(content='<div class="alert alert--error">Backup file not found.</div>')

    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    shutil.copy(file_path, temp_upload_path)

    is_encrypted = False
    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.read("backup_manifest.json")
    except RuntimeError as e:
        if "encrypted" in str(e).lower():
            is_encrypted = True
        else:
            raise

    if is_encrypted:
        return HTMLResponse(content=render_password_prompt(filename, is_local=True))

    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            manifest_bytes = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_bytes.decode('utf-8'))
        return HTMLResponse(content=render_validation_summary(filename, manifest, is_local=True))
    except Exception as e:
        logger.error(f"Error validating local backup: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Corrupt backup file.</div>')


@router.post("/panels/backup/validate-password", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_password(
    filename: str = Form(...),
    password: str = Form(...),
    is_local: bool = Form(...)
):
    from mirrordash_core.api.backup import BACKUPS_DIR

    temp_upload_path = os.path.join(BACKUPS_DIR, "tmp_upload.mirror")
    if not os.path.exists(temp_upload_path):
        return HTMLResponse(content='<div class="alert alert--error">No uploaded backup found to validate.</div>')

    try:
        with zipfile.ZipFile(temp_upload_path) as zf:
            zf.setpassword(password.encode('utf-8'))
            manifest_bytes = zf.read("backup_manifest.json")
            manifest = json.loads(manifest_bytes.decode('utf-8'))

        return HTMLResponse(content=render_validation_summary(filename, manifest, password, is_local))
    except RuntimeError:
        prompt_html = render_password_prompt(filename, is_local)
        error_msg = '<div class="alert alert--error" style="margin-bottom: 1rem;">Invalid backup password.</div>'
        return HTMLResponse(content=error_msg + prompt_html)
    except Exception as e:
        logger.error(f"Error validating password: {e}")
        return HTMLResponse(content='<div class="alert alert--error">Corrupt backup file.</div>')


@router.post("/panels/backup/restore", dependencies=[Depends(require_api_key)])
async def restore_panel_backup(
    filename: str = Form(...),
    password: str | None = Form(default=None),
    is_local: bool = Form(default=False)
):
    from mirrordash_core.api.backup import restore_backup

    job_id = start_job(lambda: restore_backup(password=password))
    return job_response(job_id, "Restoring Backup", "Restoring settings and modules. This can take a few minutes...",
                        "Backup restored successfully.")


@router.post("/panels/backup/create", dependencies=[Depends(require_api_key)])
async def create_panel_backup(request: Request):
    from mirrordash_core.api.backup import create_backup
    form_data = await request.form()
    encrypt = form_data.get("encrypt") == "true"
    password = form_data.get("password")

    if encrypt and (not password or len(password) < 4):
        return notify("Password must be at least 4 characters for encryption.", "error")

    payload = {}
    if encrypt:
        payload["password"] = password

    res = await create_backup(payload=payload)
    filename = res.get("filename")

    return notify(f"Backup {filename} created.", refreshBackups=True, **{"backup-created": True})
