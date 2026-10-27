# SPDX-License-Identifier: AGPL-3.0-only

import html
import logging
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

from mirrordash_core.admin import require_api_key, templates
from mirrordash_core.features.logs.diagnostics import host_state, report
from mirrordash_core.features.logs.saved import (
    delete_saved_logs,
    saved_log_file,
    saved_logs,
)
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.logs")
router = APIRouter(prefix="/admin")


LOG_KINDS = ("system", "modules", "raspberry")  # the app's own, its modules', the whole system's journal
MAX_LOG_LINES = 1000
# The start of an entry in the app's log (main.py's format): "2026-10-19T08:15:02.123Z [INFO] logger.name: message".
# A line that doesn't start like this (a traceback's) belongs to the entry above it.
LOG_ENTRY_START = re.compile(r"^\d{4}-\d\d-\d\dT\S+ \[\w+\] (\S+): ")


def _entries_of_app_log(log_lines: list[str]) -> list[tuple[str, list[str]]]:
    """The log as entries: (the logger's name, the entry's lines), oldest first."""
    entries = []
    for line in log_lines:
        if entry_start := LOG_ENTRY_START.match(line):
            entries.append((entry_start.group(1), [line]))
        elif entries:
            entries[-1][1].append(line)
        else:
            entries.append(("", [line]))  # the end of an entry the log rotation cut off
    return entries


def _logger_is_wanted(logger_name: str, log_kind: str, module: str | None) -> bool:
    """Whether an entry belongs in the log asked for. Modules log as mirrordash.modules.<package>, and
    `module` may be named with - or _, with or without the "mirrordash_" in front."""
    from_a_module = logger_name.startswith("mirrordash.modules")
    if log_kind == "system":
        return not from_a_module
    if not from_a_module:
        return False
    if not module:
        return True
    package = module.replace("-", "_").lower()
    logger_package = logger_name.replace("-", "_").lower().removeprefix("mirrordash.modules.")
    return logger_package.split(".")[0] in (package, package.removeprefix("mirrordash_"))


def _read_app_log(log_kind: str, lines: int, module: str | None) -> str:
    """The last `lines` lines of the app's own log entries ("system") or its modules' ("modules")."""
    from mirrordash_core.config import get_base_dir
    log_file = os.path.join(get_base_dir(), "logs", "mirrordash.log")
    if not os.path.exists(log_file):
        return f"Log file not found at {log_file}."
    # ponytail: the whole file is read for each view; RotatingFileHandler keeps it under 2 MB
    with open(log_file, encoding="utf-8", errors="ignore") as f:
        entries = _entries_of_app_log(f.readlines())
    wanted_lines = [line for logger_name, entry_lines in entries
                    if _logger_is_wanted(logger_name, log_kind, module) for line in entry_lines]
    return "".join(wanted_lines[-lines:])


async def _read_system_journal(lines: int) -> str:
    """The last `lines` lines of the whole system's log: the journal, or /var/log/syslog without systemd."""
    # -m: with developer logging on, every earlier start is its own folder (its own machine-id)
    # --utc -o short-iso: the same time format as the app's log (main.py) and the saved logs
    exit_code, output, _ = await run_command(
        "journalctl", "-m", "-n", str(lines), "--no-pager", "--utc", "-o", "short-iso", timeout=30)
    if exit_code == 0:
        return output
    try:
        with open("/var/log/syslog", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return "System log fetching failed. Systemd journalctl is not available, and /var/log/syslog is unreadable."


async def get_logs(log_kind: str = "system", lines: int = 100, module: str | None = None) -> dict:
    """{"logs": the last lines of one of LOG_KINDS}; `module` narrows "modules" to one module's."""
    logger.info(f"Admin requested logs: type={log_kind}, lines={lines}, module={module}")
    if log_kind not in LOG_KINDS:
        raise HTTPException(status_code=400, detail="Invalid log type")
    lines = min(max(1, lines), MAX_LOG_LINES)
    if log_kind == "raspberry":
        return {"logs": await _read_system_journal(lines)}
    try:
        return {"logs": _read_app_log(log_kind, lines, module)}
    except Exception as e:
        logger.error(f"Failed to read logs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")


@router.get("/panels/logs", dependencies=[Depends(require_api_key)])
async def get_panel_logs(request: Request):
    log_data = await get_logs("system", lines=100)
    logs_content = log_data.get("logs", "No logs found.")
    modules_list = list(module_host.running)
    return templates.TemplateResponse(
        request=request,
        name="admin_logs.html",
        context={
            "logs_content": logs_content,
            "modules_list": modules_list,
            "saved_logs": saved_logs(),
        }
    )


@router.get("/panels/logs/viewer", dependencies=[Depends(require_api_key)])
async def get_logs_viewer(log_kind: Annotated[str, Query(alias="type")] = "system", lines: int = 100,
                          module: str | None = None):
    log_data = await get_logs(log_kind, lines=lines, module=module)
    logs_content = log_data.get("logs", "No logs found.")
    return HTMLResponse(content=html.escape(logs_content))  # log lines can contain anything, e.g. request paths


# --- Diagnostics (diagnostics.py) ---

@router.get("/panels/logs/diagnostics", dependencies=[Depends(require_api_key)])
async def get_diagnostics(request: Request):
    return templates.TemplateResponse(request=request, name="admin_logs_diagnostics.html",
                                      context={"state": await host_state()})


@router.get("/logs/diagnostics.txt", dependencies=[Depends(require_api_key)])
async def download_diagnostics():
    text = await report((await get_logs("system", 300))["logs"], (await get_logs("modules", 300))["logs"])
    return PlainTextResponse(text, headers={"Content-Disposition": "attachment; filename=mirrordash-diagnostics.txt"})


# --- Logs saved when an update failed (saved.py) ---

@router.get("/logs/saved/{filename}", dependencies=[Depends(require_api_key)])
async def download_saved_log(filename: str):
    return FileResponse(saved_log_file(filename), filename=filename, media_type="text/plain")


@router.post("/panels/logs/saved/delete/{filename}", dependencies=[Depends(require_api_key)])
async def delete_saved_log(filename: str):
    delete_saved_logs(filename)
    return HTMLResponse(content="")


@router.post("/panels/logs/saved/delete-all", dependencies=[Depends(require_api_key)])
async def delete_all_saved_logs(request: Request):
    delete_saved_logs()
    return templates.TemplateResponse(request=request, name="admin_logs_saved_rows.html", context={"saved_logs": []})
