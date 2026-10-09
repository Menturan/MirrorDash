# Licensed under the PolyForm Noncommercial License 1.0.0.

import json
import logging
import os

logger = logging.getLogger("mirrordash.core.settings")


VALID_POSITIONS = {
    "top_left", "top_center", "top_right",
    "middle_left", "middle_center", "middle_right",
    "bottom_left", "bottom_center", "bottom_right"
}


def get_module_schema(plugin_class) -> dict | None:
    """Resolve module config schema from the plugin class variable or a standalone json file next to it."""
    import sys

    # Try to find the directory where the plugin file is located
    plugin_dir = None
    try:
        module_name = plugin_class.__module__
        module_obj = sys.modules.get(module_name)
        if module_obj and getattr(module_obj, "__file__", None):
            plugin_dir = os.path.dirname(os.path.abspath(module_obj.__file__))
    except Exception:
        pass

    schema = None
    # 1. Check for class variable config_schema
    class_schema = getattr(plugin_class, "config_schema", None)
    if callable(class_schema):
        class_schema = class_schema()
    if class_schema and isinstance(class_schema, dict):
        schema = class_schema.copy()

    # 2. Check for standalone config_schema.json or schema.json next to the module file
    if not schema and plugin_dir:
        for filename in ("config_schema.json", "schema.json"):
            filepath = os.path.join(plugin_dir, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            schema = data
                            break
                except Exception as e:
                    logger.warning(f"Error loading standalone schema {filepath}: {e}")

    # 3. If we resolved a schema and have a plugin_dir, check for icon.svg next to it
    if schema and plugin_dir:
        svg_path = os.path.join(plugin_dir, "icon.svg")
        if os.path.isfile(svg_path):
            try:
                with open(svg_path, "r", encoding="utf-8") as svg_f:
                    svg_content = svg_f.read().strip()
                    if svg_content.startswith("<svg"):
                        schema["icon"] = svg_content
            except Exception as svg_err:
                logger.warning(f"Error loading icon.svg next to schema: {svg_err}")

    return schema


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
        eps = list(importlib.metadata.entry_points(group='mirrordash.modules'))

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
            module_type = cfg.get("module", name)
            norm_name = module_type.replace('-', '_')
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


async def get_globals_schema() -> dict:
    """Return the JSON schema defining global configuration settings."""
    try:
        # ponytail: babel isn't a dependency (too big for one list); only when a module brought it along
        # do all languages show, otherwise the short list below. Upgrade: ship the list as a JSON file.
        import babel
        babel_locale = babel.Locale('en')
        lang_list = sorted(
            [(code, name) for code, name in babel_locale.languages.items() if len(code) == 2],
            key=lambda x: x[1]
        )
        enum_codes = [code for code, name in lang_list]
        enum_titles = [name for code, name in lang_list]
    except Exception as e:
        logger.info(f"Short language list (babel not installed): {e}")
        enum_codes = ["en", "sv", "de", "fr", "nl"]
        enum_titles = ["English", "Swedish", "German", "French", "Dutch"]

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
