# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import re
from fastapi import APIRouter, Depends, Request
from mirrordash_core.admin import require_api_key, templates
from mirrordash_core.config import load_config
from mirrordash_core.features.modules.loader import module_loader

logger = logging.getLogger("mirrordash.core.dashboard")
router = APIRouter(prefix="/admin")


@router.get("/panels/dashboard", dependencies=[Depends(require_api_key)])
async def get_panel_dashboard(request: Request):
    from mirrordash_core.features.updates.service import get_disk_usage
    from mirrordash_core.features.dashboard.telemetry import get_cpu_temperature, get_uptime_string, get_ram_usage, get_ntp_status, get_wifi_info, get_undervoltage_detected
    import socket

    # Each of these spawns a system command (df, timedatectl, nmcli, vcgencmd); run them
    # concurrently instead of one after another, which added up on a Pi 3.
    disk_usage, ntp_synchronized, network_info, undervoltage_detected = await asyncio.gather(
        get_disk_usage(), get_ntp_status(), get_wifi_info(), get_undervoltage_detected()
    )
    
    # What is placed where on the screen (from the config, so stopped or disabled modules show too)
    screen_layout = {}
    for inst_id, inst_cfg in load_config().get("modules", {}).items():
        if isinstance(inst_cfg, dict):
            name = re.sub(r"^mirrordash[-_]", "", inst_id).replace("_", " ").replace("-", " ").title()
            screen_layout.setdefault(inst_cfg.get("position", "middle_center"), []).append(
                {"name": name, "enabled": inst_cfg.get("enabled", True)})
        
    cpu_temp = get_cpu_temperature()

    # Get local routing IP address
    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    uptime_str = get_uptime_string()
    ram_usage = get_ram_usage()
        
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "screen_layout": screen_layout,
            "disk_usage": disk_usage,
            "cpu_temp": cpu_temp,
            "local_ip": local_ip,
            "active_count": len(module_loader.tasks),
            "uptime_str": uptime_str,
            "ram_usage": ram_usage,
            "ntp_synchronized": ntp_synchronized,
            "network_info": network_info,
            "undervoltage_detected": undervoltage_detected,
            "sensors_configured": any(d.get("type") in ("dht11", "light", "fan", "pwm_fan")
                                      for d in load_config().get("system", {}).get("devices", [])),
        }
    )


@router.get("/panels/dashboard/updates", dependencies=[Depends(require_api_key)])
async def get_dashboard_updates(request: Request):
    from mirrordash_core.features.dashboard.telemetry import check_all_updates
    try:
        updates = await check_all_updates()
    except Exception:
        updates = {"core": {"update_available": False}}
        
    has_updates = updates["core"]["update_available"]
    
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard_updates.html",
        context={
            "updates": updates,
            "has_updates": has_updates
        }
    )
