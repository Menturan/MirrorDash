# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import json
import logging
import os
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import find_module_config, load_config, save_config
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
    # 1. Check for class variable config_schema
    schema = getattr(plugin_class, "config_schema", None)
    if callable(schema):
        schema = schema()
    if schema and isinstance(schema, dict):
        return schema

    # 2. Check for standalone config_schema.json or schema.json next to the module file
    import sys
    try:
        module_name = plugin_class.__module__
        module_obj = sys.modules.get(module_name)
        if module_obj and getattr(module_obj, "__file__", None):
            plugin_dir = os.path.dirname(os.path.abspath(module_obj.__file__))
            for filename in ("config_schema.json", "schema.json"):
                filepath = os.path.join(plugin_dir, filename)
                if os.path.isfile(filepath):
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
    except Exception as e:
        logger.warning(f"Error loading standalone schema for {plugin_class}: {e}")
    return None


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
        eps_dict = {}
        for ep in importlib.metadata.entry_points(group='mymm.modules'):
            eps_dict[ep.name] = ep
        for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
            eps_dict[ep.name] = ep
        eps = list(eps_dict.values())

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
            norm_name = name.replace('-', '_')
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
    return {
        "title": "Global Settings",
        "description": "System-wide preferences inherited by all modules.",
        "properties": {
            "language": {
                "type": "string",
                "default": "en",
                "title": "System Language",
                "description": "Language for translations (e.g. en, sv, de, fr, nl)."
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

    return templates.TemplateResponse(
        request=request,
        name="admin_config.html",
        context={
            "visual_form_html": visual_form_html,
            "raw_json_str": raw_json_str
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

    raw_json_str = json.dumps(globals_data, indent=2).replace("`", "\\`").replace("${", "\\${")

    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Global settings saved successfully.</div>
        <script>
            showGlobal('Global settings saved successfully.', 'success');
            const editor = document.getElementById('config-editor');
            if (editor) editor.value = `{raw_json_str}`;
        </script>
    """)
    return response


@router.post("/panels/config/save-raw", dependencies=[Depends(require_api_key)])
async def save_panel_config_raw(request: Request):
    form_data = await request.form()
    raw_json = form_data.get("raw_json", "").strip()

    try:
        globals_data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return HTMLResponse(content=f'<div class="alert alert--error">Invalid JSON: {str(e)}</div>')

    config = load_config()
    config["globals"] = globals_data

    try:
        validate_config(config)
    except ValueError as e:
        return HTMLResponse(content=f'<div class="alert alert--error">Validation failed: {str(e)}</div>')

    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()

    await module_loader.reload_modules()

    globals_schema = await get_globals_schema()
    from mirrordash_core.api.form_generator import render_schema_form
    visual_form_html = render_schema_form(globals_schema, globals_data, "globals")

    escaped_html = visual_form_html.replace("`", "\\`").replace("${", "\\${")

    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Global settings saved successfully.</div>
        <script>
            showGlobal('Global settings saved successfully.', 'success');
            const container = document.getElementById('visual-form-container');
            if (container) container.innerHTML = `{escaped_html}`;
            triggerLucide();
        </script>
    """)
    return response


@router.get("/panels/config/add-array-item", dependencies=[Depends(require_api_key)])
async def add_array_item_route(
    name_prefix: str,
    array_key: str,
    index: int,
    item_title: str
):
    sub_properties = {}
    if name_prefix == "globals":
        schema = await get_globals_schema()
        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
    elif name_prefix.startswith("modules["):
        match = re.match(r"^modules\[([^\]]+)\]", name_prefix)
        if match:
            module_name = match.group(1)
            import importlib.metadata
            eps_dict = {}
            for ep in importlib.metadata.entry_points(group='mymm.modules'):
                eps_dict[ep.name] = ep
            for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
                eps_dict[ep.name] = ep
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
