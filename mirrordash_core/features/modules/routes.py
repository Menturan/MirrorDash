# SPDX-License-Identifier: AGPL-3.0-only
"""The Modules tab: the installed modules and where they sit, each instance's settings form, installing,
updating and removing (service.py does the work), and the community modules to discover."""

import itertools
import logging
import re
import secrets
from pathlib import Path

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
from mirrordash_core.features.kiosk import guard
from mirrordash_core.features.kiosk.ws import manager
from mirrordash_core.features.modules import service as modules_service
from mirrordash_core.features.modules.choice_field import choice_request
from mirrordash_core.features.modules.client import module_host
from mirrordash_core.features.modules.module_files import (
    SAFE_NAME,
    find_entry_point,
    scripts_allowed,
    site_name,
)
from mirrordash_core.features.modules.service import (
    allow_scripts,
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
    cast_standard_fields,
    cast_values_by_schema,
    read_form,
    render_schema_form,
)

logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")
MODULES_CSS = Path(__file__).parents[2] / "static" / "modules.css"  # the cards' own styles, in the preview too


@router.get("/panels/modules", dependencies=[Depends(require_api_key)])
async def get_panel_modules(request: Request):
    modules = (await list_modules()).get("modules", {})
    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "installed_modules": dict(sorted(modules.items(), key=lambda name_and_module: (
                name_and_module[1]["schema"].get("title") or name_and_module[0]).lower())),
            "screen_layout": screen_layout(),
            "disk_usage": await get_disk_usage(),
        }
    )


@router.get("/panels/modules/discover", dependencies=[Depends(require_api_key)])
async def get_discover_modules(request: Request):
    # Entry points are named mirrordash_clock, the community list mirrordash-clock: compare package names
    # normalized as pip does (PEP 503)
    def normalized(package_name: str) -> str:
        return re.sub(r"[-_.]+", "-", package_name).lower()
    installed = {normalized(module["package_name"]) for module in (await list_modules()).get("modules", {}).values()}
    return templates.TemplateResponse(
        request=request,
        name="admin_discover_modules.html",
        context={
            "discoverable_modules": [module for module in await list_community_modules()
                                     if normalized(module.get("name", "")) not in installed],
            "last_scan": modules_service.LAST_SCAN_TIMESTAMP or "Never"
        }
    )


@router.get("/panels/modules/config/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_config_form(request: Request, module_name: str, instance_id: str | None = None):
    ep = find_entry_point(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")
    if instance_id is not None and not SAFE_NAME.match(instance_id):
        raise HTTPException(status_code=400, detail="Invalid instance name")
    schema = get_module_schema(ep) or {}
    # The core's standard fields are shown separately, even if a module declared them too
    module_own_properties = {key: prop for key, prop in schema.get("properties", {}).items() if key not in STANDARD_FIELDS}
    modules_config = load_config().get("modules", {})
    instance_id = instance_id or _new_instance_id(module_name, modules_config)
    instance_config = modules_config.get(instance_id) or {}
    form_name_prefix = f"modules[{instance_id}]"
    standard = STANDARD_SCHEMA["properties"]
    standard_without_position = {key: prop for key, prop in standard.items() if key not in ("position", "schedule")}

    def form(properties: dict, values: dict = instance_config, prefix: str = form_name_prefix) -> str:
        return render_schema_form({"properties": properties}, values, prefix, module_name)

    return templates.TemplateResponse(request=request, name="admin_module_config.html", context={
        "module_name": module_name, "instance_id": instance_id, "is_new": instance_id not in modules_config,
        "instance_title": modules_service.instance_title(instance_id, instance_config),
        "position_form": form({"position": standard["position"]}),
        "standard_form": form(standard_without_position),
        # Its own collapsible section beside the other standard settings
        "schedule": standard["schedule"],
        "schedule_form": form(standard["schedule"]["properties"],
                              instance_config.get("schedule") if isinstance(instance_config.get("schedule"), dict) else {},
                              f"{form_name_prefix}[schedule]"),
        "module_form": form(module_own_properties),
        "site_name": site_name(ep), "scripts_allowed": scripts_allowed(ep, load_config()),
    })


@router.post("/panels/modules/scripts/{module_name}", dependencies=[Depends(require_api_key)])
async def set_module_scripts(module_name: str, request: Request):
    """The owner's yes or no to a module's card running its scripts on the screen (scripts_allowed)."""
    ep = find_entry_point(module_name)
    if not (name := site_name(ep) if ep else None):
        raise HTTPException(status_code=404, detail="Module not found")
    allowed = (await read_form(request)).get("on") in (True, "true")
    allow_scripts(name, allowed)
    await module_host.reload_modules()  # the screen reloads: a script that ran keeps its timers until then
    return notify(f"{module_name}'s scripts {'run' if allowed else 'no longer run'} on the mirror.")


def _new_instance_id(module_name: str, modules_config: dict) -> str:
    """The module's own name for its first instance, then name-2, name-3, …
    - and _ count as the same: the key mirrordash-clock is an instance of the entry point mirrordash_clock."""
    if find_module_config(modules_config, module_name)[0] is None and module_name not in modules_config:
        return module_name
    taken = {key.replace("-", "_") for key in modules_config}
    return next(f"{module_name}-{number}" for number in itertools.count(2)
                if f"{module_name}_{number}".replace("-", "_") not in taken)


async def _config_from_form(module_name: str, request: Request, instance_id: str | None) -> tuple[dict, str]:
    """One instance's settings form as the whole config would be with it: its values as the module's
    schema types them, then checked whole by validate_config (the trust boundary). (config, instance_id);
    HTTPException 404/400 when the module or the form is wrong."""
    ep = find_entry_point(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")
    forms_by_instance = (await read_form(request)).get("modules", {})
    if not forms_by_instance or not isinstance(forms_by_instance, dict):
        raise HTTPException(status_code=400, detail="Invalid form data structure")
    instance_id = instance_id or next(iter(forms_by_instance))
    if not SAFE_NAME.match(str(instance_id)):
        raise HTTPException(status_code=400, detail="Invalid instance name")
    instance_config = forms_by_instance.get(instance_id, {})
    if schema := get_module_schema(ep):
        instance_config = cast_values_by_schema(instance_config, schema)
    instance_config = cast_standard_fields(instance_config)
    instance_config["module"] = module_name

    config = load_config()
    config.setdefault("modules", {})[instance_id] = instance_config
    from mirrordash_core.features.settings.schema import validate_config
    try:
        validate_config(config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return config, instance_id


@router.post("/panels/modules/preview/{module_name}", dependencies=[Depends(require_api_key)])
async def preview_module_route(module_name: str, request: Request, instance_id: str | None = None):
    """The drawer's preview of the unsaved form (ARCHITECTURE #43), a page for a sandboxed iframe's srcdoc:
    the card the module renders from its sample answers, else the card the mirror shows now, else a word
    why there is none. X-Preview says which: sample, saved or none."""
    html, source, message = None, "none", "Not on the mirror yet. Save to see it here."
    try:
        config, instance_id = await _config_from_form(module_name, request, instance_id)
    except HTTPException as e:
        if e.status_code == 404:
            raise
        config, message = None, f"The preview waits for valid settings: {e.detail}"
    if config is not None:
        try:
            html, source = await module_host.preview(instance_id, config["modules"][instance_id]), "sample"
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug(f"{instance_id}: no preview from the module ({e!r})")
        if html is None and isinstance(saved := manager.latest_messages.get(instance_id, {}).get("html"), str):
            html, source = saved, "saved"
    card = config["modules"][instance_id] if config is not None else {}
    return templates.TemplateResponse(
        request=request, name="module_preview.html", headers={"X-Preview": source if html is not None else "none"},
        context={"html": html, "message": message, "instance_id": instance_id, "nonce": secrets.token_urlsafe(16),
                 "max_width": card.get("max_width") or "", "opacity": card.get("opacity"),
                 "scripts": config is not None and scripts_allowed(find_entry_point(module_name), config),
                 "modules_css": MODULES_CSS.read_text(encoding="utf-8")})


@router.post("/panels/modules/choices/{module_name}", dependencies=[Depends(require_api_key)])
async def module_choices_route(module_name: str, request: Request, instance_id: str | None = None,
                               field: str = "", query: str = "") -> dict:
    """The choices of a list or search setting of the unsaved form (ARCHITECTURE #48), from the module:
    {"choices": [{"id", "name", "detail"}, ...]} or {"error": text shown under the field}."""
    try:
        config, instance_id = await _config_from_form(module_name, request, instance_id)
    except HTTPException as e:
        if e.status_code == 404:
            raise
        return {"error": f"Fix the settings first: {e.detail}"}
    module_cfg = config["modules"][instance_id]
    try:
        ask = choice_request(field, get_module_schema(find_entry_point(module_name)), module_cfg, query)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        return await module_host.choices(instance_id, module_cfg, ask)
    except TimeoutError:
        return {"error": "The module didn't answer in time."}
    except (ConnectionError, OSError):
        return {"error": "The modules aren't running right now."}


@router.post("/panels/modules/config/{module_name}/save", dependencies=[Depends(require_api_key)])
async def save_module_config_route(module_name: str, request: Request, instance_id: str | None = None):
    """Save one instance's settings form (_config_from_form checks it first)."""
    is_new = instance_id not in load_config().get("modules", {})
    config, instance_id = await _config_from_form(module_name, request, instance_id)
    save_config(config)
    guard.note("changed", instance_id)  # the crash guard's step 1 pauses the module changed last
    guard.release(instance_id)  # saved again: back on the screen if the guard had paused it
    await module_host.reload_modules()
    # The settings stay open; a new instance's turn into its own, so a second Save doesn't add it again
    return notify("Added to the mirror." if is_new else "Settings saved.", refreshModules=True,
                  configSaved={"module": module_name, "instance": instance_id, "isNew": is_new})


@router.post("/panels/modules/config/{module_name}/remove", dependencies=[Depends(require_api_key)])
async def remove_module_config_route(module_name: str, instance_id: str | None = None):
    config = load_config()
    modules_config = config.get("modules", {})
    instance_id = instance_id or find_module_config(modules_config, module_name)[0]
    if instance_id not in modules_config:
        raise HTTPException(status_code=404, detail="Instance not found")
    del modules_config[instance_id]
    config.get("mqtt", {}).get("modules", {}).pop(instance_id, None)  # a new instance of that name starts unshared
    save_config(config)

    await module_host.reload_modules()

    return notify("Module removed from mirror display.", refreshModules=True, closeConfig=True)


async def set_instances_enabled(changes: dict[str, bool]) -> None:
    """Turn module instances on or off (the admin page's toggle, MQTT's switches): one save and one reload
    for all of them; one that isn't there is left out."""
    config = load_config()
    instances = config.get("modules", {})
    changed = [instance_id for instance_id, enabled in changes.items()
               if isinstance(instances.get(instance_id), dict) and instances[instance_id].get("enabled", True) != enabled]
    for instance_id in changed:
        instances[instance_id]["enabled"] = changes[instance_id]
    if changed:
        save_config(config)
        await module_host.reload_modules()


@router.post("/panels/modules/config/{module_name}/toggle", dependencies=[Depends(require_api_key)])
async def toggle_module_instance(module_name: str, instance_id: str | None = None):
    modules_config = load_config().get("modules", {})
    instance_id = instance_id or find_module_config(modules_config, module_name)[0]
    if instance_id not in modules_config:
        raise HTTPException(status_code=404, detail="Instance not found")
    new_state = not modules_config[instance_id].get("enabled", True)
    await set_instances_enabled({instance_id: new_state})
    # No refreshModules here: the toggle's own row is right already
    return notify(f"Module instance {'enabled' if new_state else 'disabled'}.")


@router.get("/panels/modules/check-update/{module_name}", dependencies=[Depends(require_api_key)])
async def check_module_update_route(module_name: str):
    update = await check_module_update(module_name)
    if not (update and update["latest_version"]):
        return HTMLResponse(content="")
    return HTMLResponse(content=templates.get_template("module_update.html").render(**update))


@router.get("/panels/modules/notes/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_notes(module_name: str):
    """The module's release notes: its PyPI description."""
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


async def _noted(work, *packages: str):
    """An install or update, then each package noted for the crash guard (its step 1: the module changed last)."""
    result = await work
    for package in packages:
        guard.note("installed", package)
    return result


@router.post("/panels/modules/install", dependencies=[Depends(require_api_key)])
async def install_panel_module(package_name: str = Form(...)):
    job_id = start_job(lambda: _noted(install_module(package_name=package_name), package_name))
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
    job_id = start_job(lambda: _noted(update_modules(package_name), package_name))
    shown = _shown_name(package_name)
    return job_response(job_id, "Upgrading Module", f"Upgrading {shown}. This can take a few minutes...",
                        f"Upgraded {shown}.", "modules")


@router.post("/panels/modules/upgrade-all", dependencies=[Depends(require_api_key)])
async def upgrade_all_modules():
    # Asked again here rather than taken from the page: only what GitHub or PyPI offers right now is installed
    updates, _ = await module_updates()
    if not updates:
        return notify("Every module is up to date.")
    names = ", ".join(update["package_name"] for update in updates)
    targets = [update["target"] for update in updates]
    job_id = start_job(lambda: _noted(update_modules(*targets), *targets))
    return job_response(job_id, "Updating Modules", f"Updating {names}. This can take a few minutes...",
                        f"Updated {names}.", "config")
