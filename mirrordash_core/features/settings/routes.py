# Licensed under the PolyForm Noncommercial License 1.0.0.

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import events_header, notify, require_api_key, templates
from mirrordash_core.config import get_core_version, load_config, save_config
from mirrordash_core.features.modules.loader import find_entry_point, module_loader
from mirrordash_core.features.settings.dev_logging import (
    clear_saved_journal,
    dev_logging_available,
    dev_logging_until,
    saved_journal_mb,
    set_dev_logging,
)
from mirrordash_core.features.settings.schema import (
    get_globals_schema,
    get_module_schema,
    validate_config,
)
from mirrordash_core.features.settings.ssh import get_ssh_status
from mirrordash_core.features.updates.service import format_build, installed_os_build
from mirrordash_core.forms import cast_values_by_schema, read_form

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
            # None where there are no system updates (an image from before A/B, a development machine)
            "os_build": format_build(build) if (build := installed_os_build()) else None,
            **dev_logging_context(),
        }
    )


# --- Developer logging (dev_logging.py): its own small form, re-rendered after every change ---

def dev_logging_context() -> dict:
    return {"dev_logging_available": dev_logging_available(), "dev_logging_until": dev_logging_until(),
            "saved_journal_mb": saved_journal_mb()}


def _dev_logging_response(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="admin_dev_logging.html", context=dev_logging_context(),
                                      headers=events_header(**{"md-notify": {"message": message, "kind": "success"}}))


@router.post("/panels/system/dev-logging", dependencies=[Depends(require_api_key)])
async def toggle_dev_logging(request: Request):
    on = (await read_form(request)).get("on") in (True, "true")
    await set_dev_logging(on)
    return _dev_logging_response(request, "Developer logging is on for 7 days." if on else "Developer logging is off.")


@router.post("/panels/system/dev-logging/clear", dependencies=[Depends(require_api_key)])
async def clear_dev_logging(request: Request):
    await clear_saved_journal()
    return _dev_logging_response(request, "The saved system log is deleted.")


@router.post("/panels/config/save-visual", dependencies=[Depends(require_api_key)])
async def save_panel_config_visual(request: Request):
    parsed = await read_form(request)

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
