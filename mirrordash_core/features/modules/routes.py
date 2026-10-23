# Licensed under the PolyForm Noncommercial License 1.0.0.

import itertools
import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.admin import (
    job_response,
    notify,
    require_api_key,
    start_job,
    templates,
)
from mirrordash_core.config import (
    find_module_config,
    load_config,
    save_config,
)
from mirrordash_core.features.modules.loader import find_entry_point, module_loader
from mirrordash_core.features.modules.service import (
    check_module_update,
    install_module,
    list_community_modules,
    list_modules,
    module_updates,
    screen_layout,
    uninstall_module,
    update_modules,
)
from mirrordash_core.features.settings.schema import get_module_schema
from mirrordash_core.features.updates.service import get_disk_usage
from mirrordash_core.fetch import fetch_json_cached
from mirrordash_core.forms import (
    STANDARD_FIELDS,
    STANDARD_SCHEMA,
    cast_values_by_schema,
    read_form,
    render_schema_form,
)

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")


@router.get("/panels/modules", dependencies=[Depends(require_api_key)])
async def get_panel_modules(request: Request):
    modules = (await list_modules()).get("modules", {})
    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "installed_modules": dict(sorted(modules.items(), key=lambda kv: (kv[1]["schema"].get("title") or kv[0]).lower())),
            "screen_layout": screen_layout(),
            "disk_usage": await get_disk_usage(),
        }
    )


@router.get("/panels/modules/discover", dependencies=[Depends(require_api_key)])
async def get_discover_modules(request: Request):
    # Entry points are named mirrordash_clock, the community list mirrordash-clock: compare package names
    # normalized as pip does (PEP 503)
    norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
    installed = {norm(meta["package_name"]) for meta in (await list_modules()).get("modules", {}).values()}
    import mirrordash_core.features.modules.service as adm_mods
    return templates.TemplateResponse(
        request=request,
        name="admin_discover_modules.html",
        context={
            "discoverable_modules": [m for m in await list_community_modules() if norm(m.get("name", "")) not in installed],
            "last_scan": adm_mods.LAST_SCAN_TIMESTAMP or "Never"
        }
    )


@router.get("/panels/modules/config/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_config_form(request: Request, module_name: str, instance_id: str = None):
    ep = find_entry_point(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")
    try:
        schema = get_module_schema(ep.load()) or {}
    except Exception as e:
        logger.warning(f"Could not load schema for '{module_name}': {e}")
        schema = {}
    # The core's standard fields are shown separately, even if a module declared them too
    own = {k: v for k, v in schema.get("properties", {}).items() if k not in STANDARD_FIELDS}
    modules_config = load_config().get("modules", {})
    instance_id = instance_id or _new_instance_id(module_name, modules_config)
    cfg = modules_config.get(instance_id) or {}
    prefix = f"modules[{instance_id}]"
    standard = STANDARD_SCHEMA["properties"]
    return templates.TemplateResponse(request=request, name="admin_module_config.html", context={
        "module_name": module_name, "instance_id": instance_id, "is_new": instance_id not in modules_config,
        "position_form": render_schema_form({"properties": {"position": standard["position"]}}, cfg, prefix, module_name),
        "standard_form": render_schema_form({"properties": {k: v for k, v in standard.items() if k != "position"}},
                                            cfg, prefix, module_name),
        "module_form": render_schema_form({"properties": own}, cfg, prefix, module_name),
    })


def _new_instance_id(module_name: str, modules_config: dict) -> str:
    """The module's own name for its first instance, then name-2, name-3, …
    - and _ count as the same: the key mirrordash-clock is an instance of the entry point mirrordash_clock."""
    if find_module_config(modules_config, module_name)[0] is None and module_name not in modules_config:
        return module_name
    taken = {key.replace("-", "_") for key in modules_config}
    return next(f"{module_name}-{n}" for n in itertools.count(2) if f"{module_name}_{n}".replace("-", "_") not in taken)


@router.post("/panels/modules/config/{module_name}/save", dependencies=[Depends(require_api_key)])
async def save_module_config_route(module_name: str, request: Request, instance_id: str = None):
    parsed = await read_form(request)

    modules_dict = parsed.get("modules", {})
    if not modules_dict:
        raise HTTPException(status_code=400, detail="Invalid form data structure")

    cfg_key = instance_id if instance_id else list(modules_dict.keys())[0]
    module_cfg = modules_dict.get(cfg_key, {})

    ep = find_entry_point(module_name)
    schema = None
    if ep:
        try:
            plugin_class = ep.load()
            schema = get_module_schema(plugin_class)
        except Exception:
            pass

    if schema:
        module_cfg = cast_values_by_schema(module_cfg, schema)

    from mirrordash_core.forms import cast_standard_fields
    module_cfg = cast_standard_fields(module_cfg)
    module_cfg["module"] = module_name

    config = load_config()
    if "modules" not in config:
        config["modules"] = {}

    config["modules"][cfg_key] = module_cfg

    from mirrordash_core.features.settings.schema import validate_config
    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    save_config(config)

    await module_loader.reload_modules()

    return notify("Module configuration saved successfully.", refreshModules=True)


@router.post("/panels/modules/config/{module_name}/remove", dependencies=[Depends(require_api_key)])
async def remove_module_config_route(module_name: str, instance_id: str = None):
    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key = instance_id
    if not cfg_key:
        cfg_key, _ = find_module_config(modules_config, module_name)

    if cfg_key in modules_config:
        del modules_config[cfg_key]

    save_config(config)

    await module_loader.reload_modules()

    return notify("Module removed from mirror display.", refreshModules=True)


@router.post("/panels/modules/config/{module_name}/toggle", dependencies=[Depends(require_api_key)])
async def toggle_module_instance(module_name: str, instance_id: str = None):
    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key = instance_id
    if not cfg_key:
        cfg_key, _ = find_module_config(modules_config, module_name)

    if cfg_key in modules_config:
        current_state = modules_config[cfg_key].get("enabled", True)
        new_state = not current_state
        modules_config[cfg_key]["enabled"] = new_state

        save_config(config)

        await module_loader.reload_modules()

        # No refreshModules here: it closes the settings sheet the toggle lives in
        return notify(f"Module instance {'enabled' if new_state else 'disabled'}.")
    else:
        raise HTTPException(status_code=404, detail="Instance not found")


@router.get("/panels/modules/check-update/{module_name}", dependencies=[Depends(require_api_key)])
async def check_module_update_route(module_name: str):
    update = await check_module_update(module_name)
    if not (update and update["latest_version"]):
        return HTMLResponse(content="")
    return HTMLResponse(content=templates.get_template("module_update.html").render(**update))


@router.get("/panels/modules/notes/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_notes(module_name: str):

    ep = find_entry_point(module_name)
    if not ep:
        return HTMLResponse(content="Module not found.")

    package_name = ep.dist.name if ep.dist else module_name

    pypi_data = await fetch_json_cached(f"https://pypi.org/pypi/{package_name}/json")
    if not pypi_data:
        return HTMLResponse(content="Failed to fetch release notes from PyPI.")

    info = pypi_data.get("info", {})
    description = info.get("description", "No release notes available.")
    latest_version = info.get("version", "0.0.0")

    return HTMLResponse(content=templates.get_template("module_notes.html").render(
        summary=info.get("summary") or module_name, package_name=package_name, latest_version=latest_version,
        description=description))


def _shown_name(package_name: str) -> str:
    """A Git URL as people say it: .../mirrordash-weather.git@v1.2.0 -> mirrordash-weather (v1.2.0)."""
    name, _, version = package_name.rstrip("/").rsplit("/", 1)[-1].partition("@")
    name = name.removesuffix(".git")
    return f"{name} ({version})" if version else name


@router.post("/panels/modules/install", dependencies=[Depends(require_api_key)])
async def install_panel_module(package_name: str = Form(...)):
    job_id = start_job(lambda: install_module(package_name=package_name))
    shown = _shown_name(package_name)
    return job_response(job_id, "Installing Module", f"Installing {shown}. This can take a few minutes...",
                        f"Installed {shown}.", "modules")


@router.post("/panels/modules/uninstall", dependencies=[Depends(require_api_key)])
async def uninstall_panel_module(package_name: str = Form(...)):
    job_id = start_job(lambda: uninstall_module(package_name=package_name))
    return job_response(job_id, "Uninstalling Module", f"Removing {package_name}...",
                        f"Successfully uninstalled {package_name}!", "modules")


@router.post("/panels/modules/upgrade", dependencies=[Depends(require_api_key)])
async def upgrade_panel_module(package_name: str = Form(...)):
    job_id = start_job(lambda: update_modules(package_name))
    shown = _shown_name(package_name)
    return job_response(job_id, "Upgrading Module", f"Upgrading {shown}. This can take a few minutes...",
                        f"Upgraded {shown}.", "modules")


@router.post("/panels/modules/upgrade-all", dependencies=[Depends(require_api_key)])
async def upgrade_all_modules():
    # Asked again here rather than taken from the page: only what GitHub or PyPI offers right now is installed
    updates, _ = await module_updates()
    if not updates:
        return notify("Every module is up to date.")
    names = ", ".join(u["package_name"] for u in updates)
    job_id = start_job(lambda: update_modules(*(u["target"] for u in updates)))
    return job_response(job_id, "Updating Modules", f"Updating {names}. This can take a few minutes...",
                        f"Updated {names}.", "config")
