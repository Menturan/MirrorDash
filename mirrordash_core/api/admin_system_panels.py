# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import load_config, get_core_version
from mirrordash_core.api.admin_system import (
    get_system_settings,
    update_system_settings,
    check_core_update,
    update_core,
)

logger = logging.getLogger("mirrordash.core.api.admin_system_panels")

router = APIRouter()


@router.get("/panels/system", dependencies=[Depends(require_api_key)])
async def get_panel_system(request: Request):
    config = load_config()
    globals_cfg = config.get("globals", {})
    time_format = globals_cfg.get("time_format", "24h")

    settings_data = await get_system_settings()
    settings = settings_data.get("settings", {})
    resolutions = settings_data.get("resolutions", [])

    # Parse current active times
    display_control = settings.get("display_control", {})
    interval = display_control.get("interval", {"start": "07:00", "end": "22:00"})
    start_time_str = interval.get("start", "07:00")
    end_time_str = interval.get("end", "22:00")

    # Helper to parse 24h string to (hour, minute, ampm)
    def parse_time_to_format(time_str: str, fmt: str):
        try:
            h_str, m_str = time_str.split(":")
            h = int(h_str)
            m = int(m_str)
        except Exception:
            h, m = 7, 0

        if fmt == "12h":
            ampm = "PM" if h >= 12 else "AM"
            h_12 = h % 12
            if h_12 == 0:
                h_12 = 12
            return h_12, m, ampm
        else:
            return h, m, None

    start_h, start_m, start_ampm = parse_time_to_format(start_time_str, time_format)
    end_h, end_m, end_ampm = parse_time_to_format(end_time_str, time_format)

    # Hours list
    if time_format == "12h":
        hours_list = list(range(1, 13))
    else:
        hours_list = list(range(0, 24))

    minutes_list = list(range(0, 60))

    current_version = get_core_version()

    return templates.TemplateResponse(
        request=request,
        name="admin_system.html",
        context={
            "settings": settings,
            "resolutions": resolutions,
            "current_version": current_version,
            "time_format": time_format,
            "start_h": start_h,
            "start_m": start_m,
            "start_ampm": start_ampm,
            "end_h": end_h,
            "end_m": end_m,
            "end_ampm": end_ampm,
            "hours_list": hours_list,
            "minutes_list": minutes_list
        }
    )


@router.post("/panels/system/save", dependencies=[Depends(require_api_key)])
async def save_system_settings_route(request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v

    from mirrordash_core.api.form_generator import parse_flat_form_data
    parsed = parse_flat_form_data(flat_data)

    # Format times back to HH:MM strings expected by update_system_settings
    display_control = parsed.get("display_control", {})
    interval = display_control.get("interval", {})
    if "start_h" in interval and "start_m" in interval:
        h = int(interval["start_h"])
        m = interval["start_m"]
        ampm = interval.get("start_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        display_control["interval"] = {
            "start": f"{h:02d}:{m}"
        }
    if "end_h" in interval and "end_m" in interval:
        h = int(interval["end_h"])
        m = interval["end_m"]
        ampm = interval.get("end_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        if "interval" not in display_control:
            display_control["interval"] = {}
        display_control["interval"]["end"] = f"{h:02d}:{m}"

    try:
        parsed["brightness"] = int(parsed.get("brightness", 100))
        parsed["volume"] = int(parsed.get("volume", 80))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Brightness and volume must be integers")

    if "pir" in display_control:
        pir = display_control["pir"]
        try:
            pir["pin"] = int(pir.get("pin", 18))
            pir["timeout_minutes"] = int(pir.get("timeout_minutes", 5))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="PIR pin and timeout must be integers")
    if "button" in display_control:
        btn = display_control["button"]
        try:
            btn["pin"] = int(btn.get("pin", 23))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Button pin must be an integer")

    res = await update_system_settings(settings=parsed)

    return HTMLResponse(content=f"""
        <div class="alert alert--success">System settings applied successfully.</div>
        <script>
            showGlobal('System settings applied successfully.', 'success');
        </script>
    """)


@router.post("/panels/system/screen", dependencies=[Depends(require_api_key)])
async def post_panel_screen(request: Request):
    form_data = await request.form()
    state = form_data.get("state")
    if state not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid state")

    from mirrordash_core.display_power import display_power_manager
    asyncio.create_task(display_power_manager.set_state(state == "on"))

    return HTMLResponse(content=f"""
        <div class="alert alert--success">Screen turned {state.upper()} successfully.</div>
        <script>
            showGlobal('Screen turned {state.upper()} successfully.', 'success');
        </script>
    """)


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
            <div style="margin-top: 10px; padding: 10px; background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16,185,129,0.2); border-radius: 6px;">
                <p style="margin: 0; color: #10b981;"><strong>Update available!</strong> New version v{latest} is available (currently installed: v{current}).</p>
                <button type="button" 
                        class="btn primary btn-sm" 
                        style="margin-top: 10px;"
                        hx-post="/admin/panels/system/update-trigger"
                        hx-target="#core-update-result"
                        hx-swap="innerHTML"
                        hx-confirm="Are you sure you want to upgrade MirrorDash Core to v{latest}? The system will reboot afterwards."
                        onclick="this.disabled=true; this.innerHTML='<i class=&quot;fas fa-spinner fa-spin&quot;></i> Upgrading...';">
                    Upgrade to v{latest} Now
                </button>
            </div>
        """)
    else:
        return HTMLResponse(content=f'<div style="margin-top: 10px; color: var(--text-muted);">Your system is up-to-date (v{current}).</div>')


@router.post("/panels/system/update-trigger", dependencies=[Depends(require_api_key)])
async def trigger_system_update():
    try:
        res = await update_core()
        return HTMLResponse(content=f"""
            <div class="alert alert--success" style="margin-top: 10px;">Upgrade initiated successfully. System is restarting. Please wait...</div>
            <script>
                showGlobal('Upgrade initiated. Restarting system...', 'success');
                setTimeout(() => {{
                    const pollStart = Date.now();
                    const poll = setInterval(async () => {{
                        if (Date.now() - pollStart > 60000) {{
                            clearInterval(poll);
                            showGlobal('Server did not respond after 60s.', 'error');
                            return;
                        }}
                        try {{
                            const r = await fetch('/health');
                            if (r.ok) {{
                                clearInterval(poll);
                                window.location.reload();
                            }}
                        }} catch (_) {{}}
                    }}, 2000);
                }}, 3000);
            </script>
        """)
    except Exception as e:
        return HTMLResponse(content=f'<div class="status-msg error" style="margin-top: 10px;">Upgrade failed: {str(e)}</div>')
