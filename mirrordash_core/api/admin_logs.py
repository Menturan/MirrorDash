# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.module_loader import module_loader

logger = logging.getLogger("mirrordash.core.api.admin_logs")

router = APIRouter()


@router.get("/logs", dependencies=[Depends(require_api_key)])
async def get_logs(type: str = "system", lines: int = 100, module: str | None = None) -> dict:
    logger.info(f"Admin requested logs: type={type}, lines={lines}, module={module}")
    if type not in ("system", "modules", "raspberry"):
        raise HTTPException(status_code=400, detail="Invalid log type")

    lines = min(max(1, lines), 1000)

    if type in ("system", "modules"):
        from mirrordash_core.config import get_base_dir
        log_dir = os.path.join(get_base_dir(), "logs")
        log_file = os.path.join(log_dir, "mirrordash.log")
        if not os.path.exists(log_file):
            log_file = os.path.join(log_dir, "mymagicmirror.log")

        if not os.path.exists(log_file):
            return {"logs": f"Log file not found at {log_file}."}

        try:
            filtered_lines = []
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()

            for line in reversed(all_lines):
                if len(filtered_lines) >= lines:
                    break

                is_module = "mirrordash.modules" in line

                if type == "modules" and is_module:
                    if module:
                        norm_target = module.replace('-', '_').lower()
                        norm_line = line.replace('-', '_').lower()
                        short_target = norm_target.replace('mirrordash_', '')
                        if (f"mirrordash.modules.{norm_target}" in norm_line or
                            f"mirrordash.modules.{short_target}" in norm_line):
                            filtered_lines.append(line)
                    else:
                        filtered_lines.append(line)
                elif type == "system" and not is_module:
                    filtered_lines.append(line)

            filtered_lines.reverse()
            return {"logs": "".join(filtered_lines)}
        except Exception as e:
            logger.error(f"Failed to read logs: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to read log file: {e}")

    elif type == "raspberry":
        # Run journalctl to get system logs
        try:
            proc = await asyncio.create_subprocess_exec(
                "journalctl", "-n", str(lines), "--no-pager",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return {"logs": stdout.decode("utf-8", errors="ignore")}
        except Exception:
            pass

        # Fallback to /var/log/syslog
        try:
            syslog_path = "/var/log/syslog"
            if os.path.exists(syslog_path):
                with open(syslog_path, "r", encoding="utf-8", errors="ignore") as f:
                    all_lines = f.readlines()
                last_lines = all_lines[-lines:]
                return {"logs": "".join(last_lines)}
        except Exception:
            pass

        return {"logs": "System log fetching failed. Systemd journalctl is not available, and /var/log/syslog is unreadable."}


@router.get("/panels/logs", dependencies=[Depends(require_api_key)])
async def get_panel_logs(request: Request):
    log_data = await get_logs(type="system", lines=100)
    logs_content = log_data.get("logs", "No logs found.")

    modules_list = []
    for name in module_loader.instances.keys():
        modules_list.append(name)

    return templates.TemplateResponse(
        request=request,
        name="admin_logs.html",
        context={
            "logs_content": logs_content,
            "modules_list": modules_list
        }
    )


@router.get("/panels/logs/viewer", dependencies=[Depends(require_api_key)])
async def get_logs_viewer(type: str = "system", lines: int = 100, module: str | None = None):
    log_data = await get_logs(type=type, lines=lines, module=module)
    logs_content = log_data.get("logs", "No logs found.")
    return HTMLResponse(content=logs_content)
