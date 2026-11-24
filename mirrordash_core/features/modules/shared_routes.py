# SPDX-License-Identifier: AGPL-3.0-only
"""The Shared Settings section of a module's settings (ARCHITECTURE #62): the settings its author marked
"shared": true, set once for every instance of the module, and where they are saved."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import notify, require_api_key, templates
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.module_files import find_entry_point
from mirrordash_core.features.modules.schema import get_module_schema, validate_config
from mirrordash_core.features.modules.shared_settings import (
    default_shared_value,
    is_secret,
    shared_keys,
    shared_values,
)
from mirrordash_core.forms import cast_values_by_schema, read_form, render_schema_form

router = APIRouter(prefix="/admin")
FORM_PREFIX = "shared"


def _shared_schema(module_name: str) -> tuple[dict, list[str]]:
    """(the module's schema, its shared keys); 404 when the module isn't installed."""
    ep = find_entry_point(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")
    schema = get_module_schema(ep) or {}
    return schema, shared_keys(schema)


@router.get("/panels/modules/shared/{module_name}", dependencies=[Depends(require_api_key)])
async def get_shared_settings(request: Request, module_name: str):
    """The section's HTML, or nothing for a module without shared settings. A saved secret is never printed."""
    schema, keys = _shared_schema(module_name)
    if not keys:
        return HTMLResponse("")
    saved = shared_values(load_config(), module_name)
    properties = {key: schema["properties"][key] for key in keys}
    values = {key: "" if is_secret(prop) else saved[key] if key in saved else default_shared_value(prop)
              for key, prop in properties.items()}
    return templates.TemplateResponse(request=request, name="shared_settings.html", context={
        "module_name": module_name,
        "fields": render_schema_form({"properties": properties}, values, FORM_PREFIX, module_name),
        "has_secret": any(is_secret(prop) and key in saved for key, prop in properties.items()),
    })


@router.post("/panels/modules/shared/{module_name}", dependencies=[Depends(require_api_key)])
async def save_shared_settings(module_name: str, request: Request):
    """Save the section's fields as the module's shared values. A secret left empty keeps the saved one;
    instances with an own value are not touched."""
    schema, keys = _shared_schema(module_name)
    if not keys:
        raise HTTPException(status_code=400, detail="This module has no shared settings.")
    posted = (await read_form(request)).get(FORM_PREFIX)
    posted = cast_values_by_schema(posted if isinstance(posted, dict) else {}, schema)
    config = load_config()
    # Only keys the schema marks shared stay: the ones it no longer marks are dropped
    values = {key: value for key, value in shared_values(config, module_name).items() if key in keys}
    for key in keys:
        if key in posted and not (is_secret(schema["properties"][key]) and posted[key] == ""):
            values[key] = posted[key]
    module_settings = config.setdefault("module_settings", {})
    if values:
        module_settings[module_name] = values
    else:
        module_settings.pop(module_name, None)
    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_config(config)
    await module_host.reload_modules()
    return notify("Shared settings saved.")
