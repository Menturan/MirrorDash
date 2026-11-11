# SPDX-License-Identifier: AGPL-3.0-only
"""A module's settings schema, and the check of every saved config (the trust boundary).

A module describes its settings in a JSON schema (config_schema.json); get_module_schema reads it, without
running the module. validate_config checks a config before it is saved: its shape, where each module
sits, and each module's settings against its schema."""

import base64
import json
import logging
import re

from mirrordash_core.forms import CHOICE_FORMATS, DAYS, POSITIONS, STANDARD_SCHEMA

logger = logging.getLogger("mirrordash.core.modules")


VALID_POSITIONS = set(POSITIONS)
MAX_CHOICE_TEXT = 200  # characters of a choice's id or name, as the module host cuts them (choices.py MAX_TEXT)


def get_module_schema(ep) -> dict | None:
    """A module's config_schema.json (or schema.json) next to its plugin file, with its icon.svg as
    "icon" (a data: URL, shown as a mask so its scripts never run). Read as files: the core never
    imports a module (ARCHITECTURE #41)."""
    from mirrordash_core.features.modules.module_files import module_file, package_dir
    plugin_dir = package_dir(ep) if ep else None

    schema = None
    if plugin_dir:
        for filename in ("config_schema.json", "schema.json"):
            if (raw := module_file(plugin_dir, filename)) is not None:
                try:
                    data = json.loads(raw.decode("utf-8"))
                    if isinstance(data, dict):
                        schema = data
                        break
                except ValueError as e:
                    logger.warning(f"Error loading standalone schema {plugin_dir / filename}: {e}")

    # If we resolved a schema and have a plugin_dir, check for icon.svg next to it
    if schema and (svg := module_file(plugin_dir, "icon.svg")) is not None:
        svg_content = svg.strip()
        if svg_content.startswith(b"<svg"):
            schema["icon"] = "data:image/svg+xml;base64," + base64.b64encode(svg_content).decode()

    return schema


# A module setting's JSON-schema type and the Python check for it (bool is an int in Python)
TYPE_CHECKS = {
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
}


def _installed_module_schemas() -> dict:
    """config_schema of every installed module, keyed by its name with '-' as '_'."""
    from mirrordash_core.features.modules.module_files import module_entry_points
    schemas = {}
    for ep in module_entry_points():
        schema = get_module_schema(ep)
        if schema and isinstance(schema.get("properties"), dict):
            schemas[ep.name.replace("-", "_")] = schema
    return schemas


def _check_module_settings(name: str, cfg: dict, properties: dict) -> None:
    for key, value in cfg.items():
        prop = properties.get(key)
        if key == "position" or not prop:
            continue
        title = prop.get("title", key)
        expected = prop.get("type")
        if expected in TYPE_CHECKS and not TYPE_CHECKS[expected](value):
            raise ValueError(f"Module '{name}' setting '{title}' must be {'an' if expected == 'integer' else 'a'} {expected}.")
        if prop.get("enum") is not None and value not in prop["enum"]:
            raise ValueError(f"Module '{name}' setting '{title}' must be one of: {prop['enum']}")
        if prop.get("format") in CHOICE_FORMATS and not _is_choice(value):
            raise ValueError(f"Module '{name}' setting '{title}' must be a choice: {{\"id\": text, \"name\": text}} or {{}}.")


def _is_choice(value) -> bool:
    """A list or search setting's value (ARCHITECTURE #48): {"id", "name"} as text, or {} for none."""
    return isinstance(value, dict) and (value == {} or (
        set(value) == {"id", "name"} and all(isinstance(v, str) and len(v) <= MAX_CHOICE_TEXT for v in value.values())))


TIME_OR_BLANK = re.compile(r"(([01]\d|2[0-3]):[0-5]\d)?")  # system_settings.py's HHMM, or blank


def _is_schedule(schedule) -> bool:
    """A module's standard "schedule" (forms.py): days of DAYS, times HH:MM or blank."""
    if not isinstance(schedule, dict):
        return False
    days, times = schedule.get("days", []), [schedule.get("from", ""), schedule.get("to", "")]
    return (isinstance(days, list) and all(isinstance(d, str) and d in DAYS for d in days)
            and all(isinstance(t, str) and TIME_OR_BLANK.fullmatch(t) for t in times))


def validate_config(config: dict) -> None:
    """Trust boundary for a saved config: its shape, the module positions and each module's settings
    against that module's config_schema. Raises ValueError on bad data."""
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object.")
    from mirrordash_core.host import is_known_timezone
    timezone = (config.get("globals") or {}).get("timezone")
    if timezone is not None and not is_known_timezone(timezone):
        raise ValueError(f"Unknown time zone {timezone!r}: use a name like Europe/Stockholm.")
    modules = config.get("modules")
    if modules is None:
        return
    if not isinstance(modules, dict):
        raise ValueError("'modules' must be a JSON object.")
    from mirrordash_core.features.modules.module_files import SAFE_NAME
    schemas = _installed_module_schemas()
    for name, cfg in modules.items():
        if not isinstance(name, str) or not SAFE_NAME.match(name):
            raise ValueError(f"Module instance name {name!r} may only hold letters, digits, - and _ (at most 64).")
        if not isinstance(cfg, dict):
            raise ValueError(f"Module '{name}' config must be a JSON object.")
        pos = cfg.get("position")
        if pos is not None and pos not in VALID_POSITIONS:
            raise ValueError(f"Module '{name}' has invalid position '{pos}'. Valid positions: {sorted(VALID_POSITIONS)}")
        name_limit = STANDARD_SCHEMA["properties"]["instance_name"]["maxLength"]
        if not isinstance(cfg.get("instance_name", ""), str) or len(cfg.get("instance_name", "")) > name_limit:
            raise ValueError(f"Module '{name}' has a name that isn't text or is longer than {name_limit} characters.")
        if "schedule" in cfg and not _is_schedule(cfg["schedule"]):
            raise ValueError(f"Module '{name}' has an invalid schedule: days from {DAYS[0]} to {DAYS[-1]}, times as HH:MM or blank.")
        module = cfg.get("module", name)
        if not isinstance(module, str):
            raise ValueError(f"Module '{name}' must name its module as text.")
        schema = schemas.get(module.replace("-", "_"))
        if schema:
            _check_module_settings(name, cfg, schema["properties"])
