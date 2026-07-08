# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import json
import logging
import os
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import find_module_config, load_config, save_config, get_core_version
from mirrordash_core.module_loader import module_loader
from mirrordash_core.system import remount_ro, remount_rw

logger = logging.getLogger("mirrordash.core.api.admin_config")

router = APIRouter()

VALID_POSITIONS = {
    "top_left", "top_center", "top_right",
    "middle_left", "middle_center", "middle_right",
    "bottom_left", "bottom_center", "bottom_right"
}


def get_module_schema(plugin_class) -> dict | None:
    """Resolve module config schema from the plugin class variable or a standalone json file next to it."""
    import sys

    # Try to find the directory where the plugin file is located
    plugin_dir = None
    try:
        module_name = plugin_class.__module__
        module_obj = sys.modules.get(module_name)
        if module_obj and getattr(module_obj, "__file__", None):
            plugin_dir = os.path.dirname(os.path.abspath(module_obj.__file__))
    except Exception:
        pass

    schema = None
    # 1. Check for class variable config_schema
    class_schema = getattr(plugin_class, "config_schema", None)
    if callable(class_schema):
        class_schema = class_schema()
    if class_schema and isinstance(class_schema, dict):
        schema = class_schema.copy()

    # 2. Check for standalone config_schema.json or schema.json next to the module file
    if not schema and plugin_dir:
        for filename in ("config_schema.json", "schema.json"):
            filepath = os.path.join(plugin_dir, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            schema = data
                            break
                except Exception as e:
                    logger.warning(f"Error loading standalone schema {filepath}: {e}")

    # 3. If we resolved a schema and have a plugin_dir, check for icon.svg next to it
    if schema and plugin_dir:
        svg_path = os.path.join(plugin_dir, "icon.svg")
        if os.path.isfile(svg_path):
            try:
                with open(svg_path, "r", encoding="utf-8") as svg_f:
                    svg_content = svg_f.read().strip()
                    if svg_content.startswith("<svg"):
                        schema["icon"] = svg_content
            except Exception as svg_err:
                logger.warning(f"Error loading icon.svg next to schema: {svg_err}")

    return schema


def validate_config(config: dict) -> None:
    """Basic structural validation of the config dict. Raises ValueError on bad data."""
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object.")
    modules = config.get("modules")
    if modules is not None and not isinstance(modules, dict):
        raise ValueError("'modules' must be a JSON object.")

    if isinstance(modules, dict):
        # Discover entry point classes to validate against their config_schema
        import importlib.metadata
        eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))

        schemas = {}
        for ep in eps:
            try:
                plugin_class = ep.load()
                schema = get_module_schema(plugin_class)
                if schema and "properties" in schema:
                    schemas[ep.name] = schema
            except Exception:
                pass

        for name, cfg in modules.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"Module '{name}' config must be a JSON object.")

            pos = cfg.get("position")
            if pos is not None and pos not in VALID_POSITIONS:
                raise ValueError(
                    f"Module '{name}' has invalid position '{pos}'. "
                    f"Valid positions: {sorted(VALID_POSITIONS)}"
                )

            # Perform schema-based property type and enum validation with normalized matching
            schema = None
            module_type = cfg.get("module", name)
            norm_name = module_type.replace('-', '_')
            for s_name, s_val in schemas.items():
                if s_name.replace('-', '_') == norm_name:
                    schema = s_val
                    break
            if schema and "properties" in schema:
                properties = schema["properties"]
                for key, val in cfg.items():
                    if key == "position":
                        continue
                    prop_schema = properties.get(key)
                    if not prop_schema:
                        continue

                    expected_type = prop_schema.get("type")
                    title = prop_schema.get("title", key)

                    if expected_type == "boolean":
                        if not isinstance(val, bool):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a boolean.")
                    elif expected_type == "integer":
                        if isinstance(val, bool) or not isinstance(val, int):
                            raise ValueError(f"Module '{name}' setting '{title}' must be an integer.")
                    elif expected_type == "number":
                        if isinstance(val, bool) or not isinstance(val, (int, float)):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a number.")
                    elif expected_type == "string":
                        if not isinstance(val, str):
                            raise ValueError(f"Module '{name}' setting '{title}' must be a string.")

                    enum_list = prop_schema.get("enum")
                    if enum_list is not None:
                        if val not in enum_list:
                            raise ValueError(f"Module '{name}' setting '{title}' must be one of: {enum_list}")


@router.get("/config", dependencies=[Depends(require_api_key)])
async def get_config() -> dict:
    return load_config()


@router.post("/config", dependencies=[Depends(require_api_key)])
async def update_config(config: dict = Body(...)) -> dict:
    logger.info("Admin requested configuration update.")
    try:
        validate_config(config)
    except ValueError as e:
        logger.warning(f"Configuration validation failed: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    await remount_rw()
    try:
        save_config(config)
        logger.info("Configuration saved successfully. Reloading modules.")

        # Apply system timezone if configured
        globals_cfg = config.get("globals", {})
        timezone = globals_cfg.get("timezone")
        if timezone:
            from mirrordash_core.system import apply_system_timezone
            asyncio.create_task(apply_system_timezone(timezone))

        asyncio.create_task(module_loader.reload_modules())
        return {"status": "success", "message": "Configuration updated"}
    finally:
        await remount_ro()


@router.get("/globals-schema", dependencies=[Depends(require_api_key)])
async def get_globals_schema() -> dict:
    """Return the JSON schema defining global configuration settings."""
    import babel
    try:
        babel_locale = babel.Locale('en')
        lang_list = sorted(
            [(code, name) for code, name in babel_locale.languages.items() if len(code) == 2],
            key=lambda x: x[1]
        )
        enum_codes = [code for code, name in lang_list]
        enum_titles = [name for code, name in lang_list]
    except Exception as e:
        logger.error(f"Error loading languages from babel: {e}")
        enum_codes = ["en", "sv", "de", "fr", "nl"]
        enum_titles = ["English", "Swedish", "German", "French", "Dutch"]

    return {
        "title": "Global Settings",
        "description": "System-wide preferences inherited by all modules.",
        "properties": {
            "language": {
                "type": "string",
                "default": "en",
                "enum": enum_codes,
                "enum_titles": enum_titles,
                "title": "System Language",
                "description": "System translation language. Note: English is always used as a fallback if translations are missing."
            },
            "timezone": {
                "type": "string",
                "default": "Europe/Stockholm",
                "title": "Timezone",
                "description": "System timezone (e.g. Europe/Stockholm, America/New_York)."
            },
            "time_format": {
                "type": "string",
                "default": "24h",
                "enum": ["24h", "12h"],
                "title": "Clock Time Format",
                "description": "Global standard for clocks and times."
            },
            "temperature_unit": {
                "type": "string",
                "default": "C",
                "enum": ["C", "F"],
                "title": "Temperature Unit",
                "description": "Unit for thermometer and weather readouts."
            },
            "distance_unit": {
                "type": "string",
                "default": "km",
                "enum": ["km", "miles"],
                "title": "Distance Unit",
                "description": "Unit for travel, range, and maps."
            },
            "latitude": {
                "type": "number",
                "default": 59.3293,
                "title": "Decimal Latitude",
                "description": "Latitude coordinates for weather/astronomy."
            },
            "longitude": {
                "type": "number",
                "default": 18.0686,
                "title": "Decimal Longitude",
                "description": "Longitude coordinates for weather/astronomy."
            },
            "safe_margin": {
                "type": "object",
                "title": "Screen Padding (Safe Margins)",
                "description": "Safe margin padding (in px) from physical screen edges.",
                "properties": {
                    "top": {
                        "type": "integer",
                        "default": 60,
                        "title": "Top Margin (px)",
                        "description": "Top padding in pixels."
                    },
                    "bottom": {
                        "type": "integer",
                        "default": 60,
                        "title": "Bottom Margin (px)",
                        "description": "Bottom padding in pixels."
                    },
                    "left": {
                        "type": "integer",
                        "default": 60,
                        "title": "Left Margin (px)",
                        "description": "Left padding in pixels."
                    },
                    "right": {
                        "type": "integer",
                        "default": 60,
                        "title": "Right Margin (px)",
                        "description": "Right padding in pixels."
                    }
                }
            }
        }
    }


@router.get("/panels/config", dependencies=[Depends(require_api_key)])
async def get_panel_config(request: Request):
    config = load_config()
    globals_schema = await get_globals_schema()
    globals_data = config.get("globals", {})

    from mirrordash_core.api.form_generator import render_schema_form
    visual_form_html = render_schema_form(globals_schema, globals_data, "globals")
    raw_json_str = json.dumps(globals_data, indent=2)

    current_version = get_core_version()

    return templates.TemplateResponse(
        request=request,
        name="admin_config.html",
        context={
            "visual_form_html": visual_form_html,
            "raw_json_str": raw_json_str,
            "current_version": current_version
        }
    )


@router.post("/panels/config/save-visual", dependencies=[Depends(require_api_key)])
async def save_panel_config_visual(request: Request):
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

    from mirrordash_core.api.form_generator import parse_flat_form_data, cast_values_by_schema
    parsed = parse_flat_form_data(flat_data)

    globals_data = parsed.get("globals", {})
    globals_schema = await get_globals_schema()
    globals_data = cast_values_by_schema(globals_data, globals_schema)

    config = load_config()
    config["globals"] = globals_data

    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()

    await module_loader.reload_modules()

    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Global settings saved successfully.</div>
        <script>
            showGlobal('Global settings saved successfully.', 'success');
        </script>
    """)
    return response


@router.get("/panels/config/add-array-item", dependencies=[Depends(require_api_key)])
async def add_array_item_route(
    name_prefix: str,
    array_key: str,
    index: int,
    item_title: str,
    module_name: str = None
):
    sub_properties = {}
    if name_prefix == "globals":
        schema = await get_globals_schema()
        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
    elif name_prefix.startswith("modules["):
        match = re.match(r"^modules\[([^\]]+)\]", name_prefix)
        if match:
            instance_id = match.group(1)
            if not module_name:
                from mirrordash_core.config import load_config
                config = load_config()
                inst_cfg = config.get("modules", {}).get(instance_id, {})
                module_name = inst_cfg.get("module", instance_id)
            import importlib.metadata
            eps_dict = {ep.name: ep for ep in importlib.metadata.entry_points(group='mirrordash.modules')}
            ep = eps_dict.get(module_name) or eps_dict.get(module_name.replace("-", "_"))
            if ep:
                try:
                    plugin_class = ep.load()
                    schema = get_module_schema(plugin_class)
                    if schema:
                        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
                except Exception:
                    pass

    from mirrordash_core.api.form_generator import render_array_item
    html = render_array_item(
        name_prefix=name_prefix,
        array_key=array_key,
        sub_properties=sub_properties,
        index=index,
        item_val={},
        item_title=item_title
    )
    return HTMLResponse(content=html)


@router.get("/panels/dashboard", dependencies=[Depends(require_api_key)])
async def get_panel_dashboard(request: Request):
    from mirrordash_core.api.admin_system import get_disk_usage
    from mirrordash_core.system.telemetry import (
        get_uptime_string,
        get_ram_usage,
        get_ntp_status,
        get_wifi_info,
        get_undervoltage_detected
    )
    import socket
    
    disk_usage = await get_disk_usage()
    
    # Get active modules from module_loader
    active_instances = []
    for name, instance in module_loader.instances.items():
        inst_cfg = getattr(instance, "config", {})
        active_instances.append({
            "id": name,
            "module": inst_cfg.get("module", name),
            "position": inst_cfg.get("position", "middle_center")
        })
        
    # Get CPU Temperature (e.g. Raspberry Pi)
    cpu_temp = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            cpu_temp = round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        pass
        
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
    ntp_synchronized = await get_ntp_status()
    network_info = await get_wifi_info()
    undervoltage_detected = await get_undervoltage_detected()
        
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "active_instances": active_instances,
            "disk_usage": disk_usage,
            "cpu_temp": cpu_temp,
            "local_ip": local_ip,
            "active_count": len(module_loader.tasks),
            "uptime_str": uptime_str,
            "ram_usage": ram_usage,
            "ntp_synchronized": ntp_synchronized,
            "network_info": network_info,
            "undervoltage_detected": undervoltage_detected
        }
    )


@router.get("/panels/dashboard/updates", dependencies=[Depends(require_api_key)])
async def get_dashboard_updates(request: Request):
    from mirrordash_core.system.telemetry import check_all_updates
    try:
        updates = await check_all_updates()
    except Exception:
        updates = {"core": {"update_available": False}, "modules": []}
        
    has_updates = updates["core"]["update_available"] or len(updates["modules"]) > 0
    
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard_updates.html",
        context={
            "updates": updates,
            "has_updates": has_updates
        }
    )
