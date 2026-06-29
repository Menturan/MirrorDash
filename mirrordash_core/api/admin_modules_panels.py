# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import importlib.metadata
import json
import logging
import urllib.request
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mirrordash_core.api.admin_shared import require_api_key, templates
from mirrordash_core.config import find_module_config, load_config, save_config
from mirrordash_core.module_loader import module_loader
from mirrordash_core.system import remount_ro, remount_rw

# Import endpoints and helper functions from admin_modules and admin_system
from mirrordash_core.api.admin_modules import (
    list_community_modules,
    list_modules,
    install_module,
    uninstall_module,
    update_module,
)
from mirrordash_core.api.admin_system import get_disk_usage
from mirrordash_core.api.admin_config import get_module_schema

logger = logging.getLogger("mirrordash.core.api.admin_modules_panels")

router = APIRouter()


@router.get("/panels/modules", dependencies=[Depends(require_api_key)])
async def get_panel_modules(request: Request):
    installed = await list_modules()
    installed_modules = installed.get("modules", {})

    query = request.query_params.get("query", "").strip().lower()
    if query:
        filtered_installed = {}
        for name, meta in installed_modules.items():
            title = meta.get("schema", {}).get("title", name).lower()
            if query in name.lower() or query in title:
                filtered_installed[name] = meta
        installed_modules = filtered_installed

    community = await list_community_modules()

    discoverable = []
    for m in community:
        name = m.get("name")
        if name not in installed_modules:
            title = m.get("title", "")
            description = m.get("description", "")
            if not query or (query in name.lower() or query in title.lower() or query in description.lower()):
                discoverable.append(m)

    disk_usage = await get_disk_usage()

    import mirrordash_core.api.admin_modules as adm_mods
    last_scan = adm_mods.LAST_SCAN_TIMESTAMP

    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "installed_modules": installed_modules,
            "discoverable_modules": discoverable,
            "disk_usage": disk_usage,
            "query": query,
            "last_scan": last_scan or "Never"
        }
    )


@router.get("/panels/modules/config/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_config_form(module_name: str):
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep

    ep = eps_dict.get(module_name)
    if not ep:
        raise HTTPException(status_code=404, detail="Module not found")

    schema = None
    try:
        plugin_class = ep.load()
        schema = get_module_schema(plugin_class)
    except Exception as e:
        logger.warning(f"Could not load schema for '{module_name}': {e}")

    if not schema:
        schema = {
            "title": module_name.replace("mirrordash-", "").replace("mirrordash_", "").title(),
            "properties": {
                "enabled": {"type": "boolean", "default": True, "title": "Enabled"},
                "position": {
                    "type": "string",
                    "default": "middle_center",
                    "enum": ["top_left", "top_right", "middle_center", "bottom_left", "bottom_right"],
                    "title": "Screen Position"
                }
            }
        }

    if "properties" not in schema:
        schema["properties"] = {}
    if "enabled" not in schema["properties"]:
        schema["properties"]["enabled"] = {"type": "boolean", "default": True, "title": "Enabled", "description": "Enable or disable this module."}
    if "position" not in schema["properties"]:
        schema["properties"]["position"] = {
            "type": "string",
            "default": "middle_center",
            "enum": [
                "top_bar", "top_left", "top_center", "top_right", "upper_third",
                "middle_left", "middle_center", "middle_right", "lower_third",
                "bottom_left", "bottom_center", "bottom_right", "bottom_bar"
            ],
            "title": "Screen Position"
        }

    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key, module_cfg = find_module_config(modules_config, module_name)
    if module_cfg is None:
        module_cfg = {}

    from mirrordash_core.api.form_generator import render_schema_form
    name_prefix = f"modules[{cfg_key or module_name.replace('_', '-')}]"
    form_html = render_schema_form(schema, module_cfg, name_prefix)

    save_url = f"/admin/panels/modules/config/{module_name}/save"
    remove_url = f"/admin/panels/modules/config/{module_name}/remove"

    return HTMLResponse(content=f"""
        <form hx-post="{save_url}" hx-target="#global-status" hx-swap="innerHTML" style="background: rgba(255,255,255,0.02); padding: 1.25rem; border-radius: 6px; border: 1px solid #27272a;">
            <h4 style="margin: 0 0 15px 0; color: white; font-size: 1rem;"><i class="fas fa-sliders-h" style="margin-right: 6px; color: var(--accent-color);"></i>Configuration Parameters</h4>
            {form_html}
            
            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
                <button type="button" class="btn danger btn-sm"
                        hx-post="{remove_url}"
                        hx-target="#global-status"
                        hx-confirm="Are you sure you want to deactivate and remove this module from the mirror screen?">
                    <i class="fas fa-times"></i> Remove from Mirror
                </button>
                <button type="submit" class="btn primary btn-sm">
                    <i class="fas fa-save"></i> Save Configuration
                </button>
            </div>
        </form>
        <script>triggerLucide();</script>
    """)


@router.post("/panels/modules/config/{module_name}/save", dependencies=[Depends(require_api_key)])
async def save_module_config_route(module_name: str, request: Request):
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

    modules_dict = parsed.get("modules", {})
    if not modules_dict:
        raise HTTPException(status_code=400, detail="Invalid form data structure")

    cfg_key = list(modules_dict.keys())[0]
    module_cfg = modules_dict[cfg_key]

    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep
    ep = eps_dict.get(module_name)
    schema = None
    if ep:
        try:
            plugin_class = ep.load()
            schema = get_module_schema(plugin_class)
        except Exception:
            pass

    if schema:
        module_cfg = cast_values_by_schema(module_cfg, schema)

    config = load_config()
    if "modules" not in config:
        config["modules"] = {}

    config["modules"][cfg_key] = module_cfg

    from mirrordash_core.api.admin_config import validate_config
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
        <div class="alert alert--success">Module configuration saved successfully.</div>
        <script>
            showGlobal('Module configuration saved successfully.', 'success');
            htmx.trigger("#installed-modules-container", "refreshModules");
        </script>
    """)
    return response


@router.post("/panels/modules/config/{module_name}/remove", dependencies=[Depends(require_api_key)])
async def remove_module_config_route(module_name: str):
    config = load_config()
    modules_config = config.get("modules", {})
    cfg_key, _ = find_module_config(modules_config, module_name)

    if cfg_key in modules_config:
        del modules_config[cfg_key]

    await remount_rw()
    try:
        save_config(config)
    finally:
        await remount_ro()

    await module_loader.reload_modules()

    response = HTMLResponse(content=f"""
        <div class="alert alert--success">Module removed from mirror display.</div>
        <script>
            showGlobal('Module removed from mirror display.', 'success');
            htmx.trigger("#installed-modules-container", "refreshModules");
        </script>
    """)
    return response


@router.get("/panels/modules/check-update/{module_name}", dependencies=[Depends(require_api_key)])
async def check_module_update_route(module_name: str):
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep

    ep = eps_dict.get(module_name)
    if not ep:
        return HTMLResponse(content="")

    package_name = ep.dist.name if ep.dist else module_name
    current_version = ep.dist.version if ep.dist else "0.0.0"

    def _fetch_pypi_info() -> dict | None:
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    pypi_data = await asyncio.to_thread(_fetch_pypi_info)
    if not pypi_data:
        return HTMLResponse(content="")

    latest_version = pypi_data.get("info", {}).get("version", current_version)

    def _parse_version(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0,)

    is_newer = _parse_version(latest_version) > _parse_version(current_version)

    if is_newer:
        return HTMLResponse(content=f"""
            <div id="update-badge-{module_name}" hx-swap-oob="true">
                <span class="status-badge update-avail" style="margin-left: 8px;">Update Available (v{latest_version})</span>
            </div>
            <div id="update-actions-{module_name}" hx-swap-oob="true" style="display: flex; gap: 8px; align-items: center;">
                <button class="btn secondary btn-sm"
                        hx-get="/admin/panels/modules/notes/{module_name}"
                        hx-target="#notes-modal-content-container"
                        onclick="document.getElementById('notes-modal').style.display='flex'; document.getElementById('notes-modal').classList.add('open');">
                    <i class="fas fa-file-alt"></i> Notes
                </button>
                <button class="btn primary btn-sm"
                        hx-post="/admin/panels/modules/upgrade"
                        hx-vals='{{"package_name": "{package_name}"}}'
                        hx-target="#global-status"
                        hx-confirm="Are you sure you want to upgrade {package_name} to v{latest_version}?">
                    <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
                </button>
            </div>
        """)
    else:
        return HTMLResponse(content="")


@router.get("/panels/modules/notes/{module_name}", dependencies=[Depends(require_api_key)])
async def get_module_notes(module_name: str):
    eps_dict = {}
    for ep in importlib.metadata.entry_points(group='mymm.modules'):
        eps_dict[ep.name] = ep
    for ep in importlib.metadata.entry_points(group='mirrordash.modules'):
        eps_dict[ep.name] = ep

    ep = eps_dict.get(module_name)
    if not ep:
        return HTMLResponse(content="Module not found.")

    package_name = ep.dist.name if ep.dist else module_name

    def _fetch_pypi_info() -> dict | None:
        url = f"https://pypi.org/pypi/{package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    pypi_data = await asyncio.to_thread(_fetch_pypi_info)
    if not pypi_data:
        return HTMLResponse(content="Failed to fetch release notes from PyPI.")

    info = pypi_data.get("info", {})
    description = info.get("description", "No release notes available.")
    latest_version = info.get("version", "0.0.0")

    return HTMLResponse(content=f"""
        <header class="modal-header">
            <div>
                <h2 id="modal-title"><i class="fas fa-file-alt"></i> {info.get('summary', module_name)} Release Notes</h2>
                <span id="modal-subtitle" class="modal-subtitle">{package_name} v{latest_version}</span>
            </div>
            <button id="modal-close-btn" class="modal-close-btn" aria-label="Close modal" onclick="closeReleaseNotesModal()">
                <i class="fas fa-times"></i>
            </button>
        </header>
        <div id="modal-body" class="modal-body">
            <textarea id="notes-markdown-source" style="display:none;">{description}</textarea>
            <div id="notes-rendered-content">Rendering...</div>
        </div>
        <footer class="modal-footer">
            <button id="modal-update-btn" class="btn primary"
                    hx-post="/admin/panels/modules/upgrade"
                    hx-vals='{{"package_name": "{package_name}"}}'
                    hx-target="#global-status"
                    hx-confirm="Are you sure you want to upgrade {package_name} to v{latest_version}?"
                    onclick="closeReleaseNotesModal()">
                <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
            </button>
            <button class="btn secondary" onclick="closeReleaseNotesModal()">Close</button>
        </footer>
        <script>
            renderNotesMarkdown();
        </script>
    """)


@router.post("/panels/modules/install", dependencies=[Depends(require_api_key)])
async def install_panel_module(package_name: str = Form(...)):
    try:
        res = await install_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully installed {package_name}! System is restarting...</div>
            <script>
                if (window.pollRestartAndReload) {{
                    window.pollRestartAndReload({{
                        targetPanel: 'modules',
                        successMsg: 'Successfully installed {package_name}!',
                        title: 'Installing Module',
                        message: 'Restarting MirrorDash to load {package_name}...'
                    }});
                }} else {{
                    showGlobal('Successfully installed {package_name}. Restarting...', 'success');
                    setTimeout(() => {{ window.location.reload(); }}, 5000);
                }}
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Installation failed: {err_detail}</div>')


@router.post("/panels/modules/uninstall", dependencies=[Depends(require_api_key)])
async def uninstall_panel_module(package_name: str = Form(...)):
    try:
        res = await uninstall_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully uninstalled {package_name}! System is restarting...</div>
            <script>
                if (window.pollRestartAndReload) {{
                    window.pollRestartAndReload({{
                        targetPanel: 'modules',
                        successMsg: 'Successfully uninstalled {package_name}!',
                        title: 'Uninstalling Module',
                        message: 'Restarting MirrorDash to complete uninstallation of {package_name}...'
                    }});
                }} else {{
                    showGlobal('Successfully uninstalled {package_name}. Restarting...', 'success');
                    setTimeout(() => {{ window.location.reload(); }}, 5000);
                }}
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Uninstall failed: {err_detail}</div>')


@router.post("/panels/modules/upgrade", dependencies=[Depends(require_api_key)])
async def upgrade_panel_module(package_name: str = Form(...)):
    try:
        res = await update_module(package_name=package_name)
        return HTMLResponse(content=f"""
            <div class="alert alert--success">Successfully upgraded {package_name}! System is restarting...</div>
            <script>
                if (window.pollRestartAndReload) {{
                    window.pollRestartAndReload({{
                        targetPanel: 'modules',
                        successMsg: 'Successfully upgraded {package_name}!',
                        title: 'Upgrading Module',
                        message: 'Restarting MirrorDash to complete upgrade of {package_name}...'
                    }});
                }} else {{
                    showGlobal('Successfully upgraded {package_name}. Restarting...', 'success');
                    setTimeout(() => {{ window.location.reload(); }}, 5000);
                }}
            </script>
        """)
    except Exception as e:
        err_detail = e.detail if hasattr(e, "detail") else str(e)
        return HTMLResponse(content=f'<div class="alert alert--error">Upgrade failed: {err_detail}</div>')
