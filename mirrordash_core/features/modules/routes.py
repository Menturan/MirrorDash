# Licensed under the PolyForm Noncommercial License 1.0.0.

import itertools
import json
import logging
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from mirrordash_core.admin import job_response, notify, require_api_key, start_job, templates
from mirrordash_core.config import find_module_config, load_config, save_config, version_key
from mirrordash_core.features.modules.loader import module_loader, find_entry_point
from mirrordash_core.fetch import fetch_json_cached
from mirrordash_core.features.modules.service import list_community_modules, list_modules, install_module, uninstall_module, update_module
from mirrordash_core.features.updates.service import get_disk_usage
from mirrordash_core.features.settings.schema import get_module_schema

from mirrordash_core.forms import cast_values_by_schema, read_form

from mirrordash_core.forms import STANDARD_FIELDS, STANDARD_SCHEMA, render_schema_form
logger = logging.getLogger("mirrordash.core.modules")
router = APIRouter(prefix="/admin")


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

    disk_usage = await get_disk_usage()

    return templates.TemplateResponse(
        request=request,
        name="admin_modules.html",
        context={
            "installed_modules": installed_modules,
            "disk_usage": disk_usage,
            "query": query
        }
    )


@router.get("/panels/modules/discover", dependencies=[Depends(require_api_key)])
async def get_discover_modules(request: Request):
    installed = await list_modules()
    installed_modules = installed.get("modules", {})

    query = request.query_params.get("query", "").strip().lower()
    community = await list_community_modules()

    discoverable = []
    for m in community:
        name = m.get("name")
        if name not in installed_modules:
            title = m.get("title", "")
            description = m.get("description", "")
            if not query or (query in name.lower() or query in title.lower() or query in description.lower()):
                discoverable.append(m)

    import mirrordash_core.features.modules.service as adm_mods
    last_scan = adm_mods.LAST_SCAN_TIMESTAMP

    return templates.TemplateResponse(
        request=request,
        name="admin_discover_modules.html",
        context={
            "discoverable_modules": discoverable,
            "last_scan": last_scan or "Never"
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
        "module_name": module_name, "instance_id": instance_id,
        "position_form": render_schema_form({"properties": {"position": standard["position"]}}, cfg, prefix, module_name),
        "standard_form": render_schema_form({"properties": {k: v for k, v in standard.items() if k != "position"}},
                                            cfg, prefix, module_name),
        "module_form": render_schema_form({"properties": own}, cfg, prefix, module_name),
    })


def _new_instance_id(module_name: str, modules_config: dict) -> str:
    """The module's own name for its first instance, then name-2, name-3, …"""
    taken = any(isinstance(c, dict) and c.get("module") == module_name for c in modules_config.values())
    if not taken and module_name not in modules_config:
        return module_name
    return next(f"{module_name}-{n}" for n in itertools.count(2) if f"{module_name}-{n}" not in modules_config)


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

    ep = find_entry_point(module_name)
    if not ep:
        return HTMLResponse(content="")

    package_name = ep.dist.name if ep.dist else module_name
    current_version = ep.dist.version if ep.dist else "0.0.0"

    # Check PEP 610 direct_url.json for GitHub installation metadata
    direct_url_info = None
    if ep.dist:
        try:
            url_data = ep.dist.read_text('direct_url.json')
            if url_data:
                direct_url_info = json.loads(url_data)
        except Exception:
            pass

    if direct_url_info and "vcs_info" in direct_url_info:
        vcs_info = direct_url_info["vcs_info"]
        url = direct_url_info.get("url", "")
        vcs = vcs_info.get("vcs", "")
        if vcs == "git" and "github.com" in url:
            parts = url.rstrip("/").split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                owner = parts[0]
                repo = parts[1].replace(".git", "")
                
                release_data = await fetch_json_cached(
                    f"https://api.github.com/repos/{owner}/{repo}/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                if release_data and release_data.get("tag_name"):
                    tag_name = release_data["tag_name"]
                    latest_version = tag_name.lstrip("v")

                    # Installed from exactly this release (upgrades install git+url@tag): up to date,
                    # even when the package's own version doesn't match the tag (v1.0.0 shipped 0.1.0).
                    on_latest = vcs_info.get("requested_revision") == tag_name
                    is_newer = not on_latest and version_key(latest_version) > version_key(current_version)
                    if is_newer:
                        git_install_url = f"git+{url}@{tag_name}"
                        return HTMLResponse(content=f"""
                            <div id="update-badge-{module_name}" hx-swap-oob="true">
                                <span class="status-badge update-avail" style="margin-left: 8px;">Update Available (v{latest_version})</span>
                            </div>
                            <div id="update-actions-{module_name}" hx-swap-oob="true" style="display: flex; gap: 8px; align-items: center;">
                                <button class="btn primary btn-sm"
                                        hx-post="/admin/panels/modules/upgrade"
                                        hx-vals='{{"package_name": "{git_install_url}"}}'
                                        hx-target="#global-status"
                                        hx-confirm="Are you sure you want to upgrade {package_name} to v{latest_version}?">
                                    <i class="fas fa-arrow-alt-circle-up"></i> Upgrade
                                </button>
                            </div>
                        """)
        return HTMLResponse(content="")

    pypi_data = await fetch_json_cached(f"https://pypi.org/pypi/{package_name}/json")
    if not pypi_data:
        return HTMLResponse(content="")

    latest_version = pypi_data.get("info", {}).get("version", current_version)
    is_newer = version_key(latest_version) > version_key(current_version)

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
    job_id = start_job(lambda: update_module(package_name=package_name))
    shown = _shown_name(package_name)
    return job_response(job_id, "Upgrading Module", f"Upgrading {shown}. This can take a few minutes...",
                        f"Upgraded {shown}.", "modules")
