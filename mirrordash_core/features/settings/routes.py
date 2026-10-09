# Licensed under the PolyForm Noncommercial License 1.0.0.

import json
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import notify, require_api_key, templates
from mirrordash_core.config import load_config, save_config, get_core_version
from mirrordash_core.features.modules.loader import module_loader, find_entry_point
from mirrordash_core.features.settings.ssh import get_ssh_status

from mirrordash_core.features.settings.schema import get_globals_schema, get_module_schema, validate_config

logger = logging.getLogger("mirrordash.core.settings")
router = APIRouter(prefix="/admin")


@router.get("/panels/config", dependencies=[Depends(require_api_key)])
async def get_panel_config(request: Request):
    config = load_config()
    globals_schema = await get_globals_schema()
    globals_data = config.get("globals", {})

    from mirrordash_core.forms import render_schema_form
    visual_form_html = render_schema_form(globals_schema, globals_data, "globals")
    raw_json_str = json.dumps(globals_data, indent=2)

    current_version = get_core_version()

    return templates.TemplateResponse(
        request=request,
        name="admin_config.html",
        context={
            "visual_form_html": visual_form_html,
            "raw_json_str": raw_json_str,
            "current_version": current_version,
            "prerelease": config.get("system", {}).get("prerelease", False),
            "ssh": await get_ssh_status(),
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

    from mirrordash_core.forms import parse_flat_form_data, cast_values_by_schema
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

    save_config(config)

    await module_loader.reload_modules()

    return notify("Saved.")


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
            ep = find_entry_point(module_name)
            if ep:
                try:
                    plugin_class = ep.load()
                    schema = get_module_schema(plugin_class)
                    if schema:
                        sub_properties = schema.get("properties", {}).get(array_key, {}).get("items", {}).get("properties", {})
                except Exception:
                    pass

    from mirrordash_core.forms import render_array_item
    html = render_array_item(
        name_prefix=name_prefix,
        array_key=array_key,
        sub_properties=sub_properties,
        index=index,
        item_val={},
        item_title=item_title
    )
    return HTMLResponse(content=html)
