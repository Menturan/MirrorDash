# Licensed under the PolyForm Noncommercial License 1.0.0.

import html
import logging
import os
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from mirrordash_core.admin import (
    job_response,
    notify,
    require_api_key,
    start_job,
    templates,
)
from mirrordash_core.features.backup.service import (
    backup_file,
    create_backup,
    delete_backup,
    list_backups,
    read_manifest,
    restore_backup,
    temp_upload,
)

logger = logging.getLogger("mirrordash.core.backup")
router = APIRouter(prefix="/admin")


def _error(message: str) -> HTMLResponse:
    """A one-line error in the upload card; the message may hold a file name, so it is escaped."""
    return HTMLResponse(content=f'<div class="alert alert--error">{html.escape(message)}</div>')


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
        return _error("Invalid or corrupt backup archive.")
    if manifest is None:
        return HTMLResponse(content=templates.get_template("backup_password.html").render(
            filename=filename, is_local=is_local, wrong=bool(password)))
    return HTMLResponse(content=templates.get_template("backup_validation.html").render(
        filename=filename, manifest=manifest, password=password or "", is_local=is_local))


@router.post("/panels/backup/upload", dependencies=[Depends(require_api_key)])
async def upload_panel_backup(file: UploadFile = File(...)):
    if not file.filename.endswith(".mirror"):
        return _error("Invalid file type. File must have .mirror extension.")
    with open(temp_upload(), "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return _summary_or_password_prompt(file.filename, is_local=False)


@router.post("/panels/backup/validate-local", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_local(filename: str = Form(...)):
    try:
        shutil.copy(backup_file(filename), temp_upload())
    except HTTPException as e:
        return _error(f"{e.detail}.")
    return _summary_or_password_prompt(filename, is_local=True)


@router.post("/panels/backup/validate-password", dependencies=[Depends(require_api_key)])
async def validate_panel_backup_password(filename: str = Form(...), password: str = Form(...), is_local: bool = Form(...)):
    if not os.path.exists(temp_upload()):
        return _error("No uploaded backup found to validate.")
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
