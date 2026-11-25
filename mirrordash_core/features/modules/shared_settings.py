# SPDX-License-Identifier: AGPL-3.0-only
"""Settings a module's author marks "shared": true (ARCHITECTURE #62): one value for every instance of the
module (a server's address, an account's token), unless an instance has an own value.

Where they are: config["module_settings"][<module>][<key>] holds the shared value, with the module named as
an instance names it in its "module". An instance's own value is its key in config["modules"][<instance>]:
the key being there is what "own" means. with_shared is the one place the two meet; every place that hands
an instance's settings to a module goes through it, so the module never sees the difference."""

from urllib.parse import urlparse

from mirrordash_core.forms import (
    CHOICE_FORMATS,
    STANDARD_FIELDS,
    cast_values_by_schema,
    render_schema_form,
)

FORM_PREFIX = "shared"  # the shared fields' input names: shared[<key>]

# A single value: not a list, an object or files
SINGLE_VALUE_TYPES = ("string", "integer", "number", "boolean")


def is_shareable(key: str, prop) -> bool:
    """Whether a top-level property of a schema may be shared (and marked "shared": true): a single value
    that is not one of the core's standard fields, not a list or search setting and not files."""
    return (isinstance(prop, dict) and prop.get("shared") is True and key not in STANDARD_FIELDS
            and prop.get("type") in SINGLE_VALUE_TYPES and prop.get("format") not in (*CHOICE_FORMATS, "files"))


def shared_keys(schema: dict | None) -> list[str]:
    """The settings of a module's schema that are shared, in the schema's order."""
    properties = (schema or {}).get("properties")
    return [key for key, prop in properties.items() if is_shareable(key, prop)] if isinstance(properties, dict) else []


def default_shared_value(prop: dict):
    """What a shared setting is before anyone set it: the schema's default, but a lan-url's is only a hint
    in the form (defaults.py), never an address."""
    return "" if prop.get("format") == "lan-url" else prop.get("default", "")


def shared_values(config: dict, module: str) -> dict:
    """The shared values saved for a module ({} when none)."""
    values = (config.get("module_settings") or {}).get(module)
    return values if isinstance(values, dict) else {}


def with_shared(module_cfg: dict, config: dict, schema: dict | None) -> dict:
    """An instance's settings as its module gets them: its own value where it has one, else the shared
    value, else nothing here (with_defaults fills in the schema's default after this). A copy."""
    merged = dict(module_cfg)
    saved = shared_values(config, str(module_cfg.get("module") or ""))
    for key in shared_keys(schema):
        if key not in merged and key in saved:
            merged[key] = saved[key]
    return merged


def merged_instances(config: dict) -> dict[str, dict]:
    """Every instance of config["modules"] with with_shared applied (those that aren't a dict are left out)."""
    from mirrordash_core.features.modules.schema import _installed_module_schemas
    schemas = _installed_module_schemas()
    return {instance_id: with_shared(cfg, config, schemas.get(str(cfg.get("module", instance_id)).replace("-", "_")))
            for instance_id, cfg in config.get("modules", {}).items() if isinstance(cfg, dict)}


def is_secret(prop: dict) -> bool:
    """A shared setting that is a password: the forms never print its saved value."""
    return prop.get("format") == "password"


def drop_unowned_shared(instance_config: dict, own: set[str], schema: dict | None) -> None:
    """A saved instance form: a shared setting the instance has no own value for (not in own) isn't the
    instance's, so it is taken out of its settings (changed in place). Shared Settings is where it is saved."""
    for key in shared_keys(schema):
        if key not in own:
            instance_config.pop(key, None)


def apply_posted_shared(config: dict, module_name: str, posted, schema: dict | None) -> None:
    """The sheet's shared fields as the module's shared values, in config only (the caller saves it). A secret
    left empty keeps the saved one; a key the schema no longer marks shared is dropped."""
    keys = shared_keys(schema)
    if not keys or not isinstance(posted, dict):
        return
    posted = cast_values_by_schema(posted, schema)
    values = {key: value for key, value in shared_values(config, module_name).items() if key in keys}
    for key in keys:
        if key in posted and not (is_secret(schema["properties"][key]) and posted[key] == ""):
            values[key] = posted[key]
    module_settings = config.setdefault("module_settings", {})
    if values:
        module_settings[module_name] = values
    else:
        module_settings.pop(module_name, None)


def _shown_shared_values(config: dict, module_name: str, schema: dict) -> dict:
    """What the sheet shows for each shared setting: the saved value, else the schema's default; never a secret."""
    saved = shared_values(config, module_name)
    return {key: "" if is_secret(schema["properties"][key]) else saved[key] if key in saved else default_shared_value(schema["properties"][key])
            for key in shared_keys(schema)}


def _short(value) -> str:
    """A shared value as a few words: an address as its host."""
    text = str(value)
    return (urlparse(text).netloc if "://" in text else text)[:40]


def shared_block(config: dict, module_name: str, schema: dict, instance_count: int) -> dict | None:
    """The sheet's shared block: {"html": its fields, "summary": the first value and "used by N", "open": a
    required shared field is empty}; None when the module has no shared settings."""
    keys = shared_keys(schema)
    if not keys:
        return None
    shown = _shown_shared_values(config, module_name, schema)
    properties = {key: schema["properties"][key] for key in keys}
    saved = shared_values(config, module_name)
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    first = next((_short(value) for value in shown.values() if value not in ("", None)), "Not set")
    return {
        "html": render_schema_form({"properties": properties}, shown, FORM_PREFIX, module_name),
        "summary": f"{first} \u00b7 used by {instance_count}",
        # A secret's emptiness is about the saved one: the form never prints it
        "open": any(key in required and (saved.get(key) if is_secret(properties[key]) else shown[key]) in ("", None) for key in keys),
    }
