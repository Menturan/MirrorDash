# SPDX-License-Identifier: AGPL-3.0-only

import base64
import json
import logging

from mirrordash_core.forms import POSITIONS

logger = logging.getLogger("mirrordash.core.settings")


VALID_POSITIONS = set(POSITIONS)


def get_module_schema(ep) -> dict | None:
    """A module's config_schema.json (or schema.json) next to its plugin file, with its icon.svg as
    "icon" (a data: URL, shown as a mask so its scripts never run). Read as files: the core never
    imports a module (ARCHITECTURE #41)."""
    from mirrordash_core.features.modules.loader import module_file, package_dir
    plugin_dir = package_dir(ep) if ep else None

    schema = None
    if plugin_dir:
        for filename in ("config_schema.json", "schema.json"):
            if filepath := module_file(plugin_dir, filename):
                try:
                    with open(filepath, encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            schema = data
                            break
                except Exception as e:
                    logger.warning(f"Error loading standalone schema {filepath}: {e}")

    # 3. If we resolved a schema and have a plugin_dir, check for icon.svg next to it
    if schema and (svg_path := module_file(plugin_dir, "icon.svg")):
        try:
            with open(svg_path, "rb") as svg_f:
                svg_content = svg_f.read().strip()
                if svg_content.startswith(b"<svg"):
                    schema["icon"] = "data:image/svg+xml;base64," + base64.b64encode(svg_content).decode()
        except Exception as svg_err:
            logger.warning(f"Error loading icon.svg next to schema: {svg_err}")

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
    from mirrordash_core.features.modules.loader import module_entry_points
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
    from mirrordash_core.features.modules.loader import SAFE_NAME
    schemas = _installed_module_schemas()
    for name, cfg in modules.items():
        if not isinstance(name, str) or not SAFE_NAME.match(name):
            raise ValueError(f"Module instance name {name!r} may only hold letters, digits, - and _ (at most 64).")
        if not isinstance(cfg, dict):
            raise ValueError(f"Module '{name}' config must be a JSON object.")
        pos = cfg.get("position")
        if pos is not None and pos not in VALID_POSITIONS:
            raise ValueError(f"Module '{name}' has invalid position '{pos}'. Valid positions: {sorted(VALID_POSITIONS)}")
        schema = schemas.get(cfg.get("module", name).replace("-", "_"))
        if schema:
            _check_module_settings(name, cfg, schema["properties"])


async def get_globals_schema() -> dict:
    """Return the JSON schema defining global configuration settings."""
    import babel  # here, not at the top: its locale data is only needed by the admin page
    lang_list = sorted(((code, name) for code, name in babel.Locale('en').languages.items() if len(code) == 2),
                       key=lambda x: x[1])
    enum_codes = [code for code, name in lang_list]
    enum_titles = [name for code, name in lang_list]

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
