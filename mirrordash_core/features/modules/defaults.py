# SPDX-License-Identifier: AGPL-3.0-only
"""A module instance's settings with its schema's defaults filled in (ARCHITECTURE #52): a setting the owner
never saved (one a module update added, or an instance from before it) arrives as the schema's "default",
so the module reads config["city"] and writes each default once, in config_schema.json.

A lan-url never saved arrives empty: its default is a hint in the form, never an address the module may
reach (#41). A list or search choice never saved arrives as {} (none picked), as a saved "none" does."""

import copy

from mirrordash_core.forms import CHOICE_FORMATS


def with_defaults(saved: dict, schema: dict) -> dict:
    """saved, with each missing setting of schema["properties"] at its default; nested objects too. A
    setting without a default stays missing."""
    filled = dict(saved)
    for key, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            continue
        if prop.get("format") in CHOICE_FORMATS:
            filled.setdefault(key, {})
        elif prop.get("type") == "object" and isinstance(prop.get("properties"), dict):
            filled[key] = with_defaults(filled[key] if isinstance(filled.get(key), dict) else {}, prop)
        elif key not in filled and "default" in prop:
            filled[key] = "" if prop.get("format") == "lan-url" else copy.deepcopy(prop["default"])
    return filled
