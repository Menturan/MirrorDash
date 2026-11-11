# SPDX-License-Identifier: AGPL-3.0-only
"""The rules a module's schema sets on what the owner types (ARCHITECTURE #49): required, pattern (with its
pattern_message), minimum and maximum, minLength and maxLength; in a list setting's items too. The browser
checks the same rules in the form (schema_form.html rules); this is the check that counts, made when one
instance's settings are saved, never on the rest of the config, so an old value elsewhere blocks no save.

As the browser does: an empty value ("", or {} for a list or search setting) breaks only required; false and
[] are values. A key the form didn't send isn't checked (the module uses its default). pattern matches the
whole value, as an HTML pattern does (JSON Schema's would match anywhere in it; ^…$ works in both)."""

import re


def _is_empty(value) -> bool:
    return value is None or value == "" or value == {}


def _broken_rule(value, prop: dict) -> str | None:
    """What value breaks of prop's rules, in words for the owner; None when it keeps them all."""
    if isinstance(value, str):
        if "minLength" in prop and len(value) < prop["minLength"]:
            return f"needs at least {prop['minLength']} characters"
        if "maxLength" in prop and len(value) > prop["maxLength"]:
            return f"takes at most {prop['maxLength']} characters"
        if isinstance(prop.get("pattern"), str):
            try:
                matches = re.fullmatch(prop["pattern"], value) is not None
            except re.error:
                matches = True  # ponytail: a pattern Python can't read is the module's mistake, not the owner's
            if not matches:
                return str(prop.get("pattern_message") or "doesn't have the right format")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in prop and value < prop["minimum"]:
            return f"must be at least {prop['minimum']}"
        if "maximum" in prop and value > prop["maximum"]:
            return f"must be at most {prop['maximum']}"
    return None


def check_setting_rules(settings: dict, schema: dict, where: str = "") -> None:
    """Raise ValueError naming the first setting of settings (one instance's, as the form typed them) that
    breaks a rule of schema; where is the list item it sits in, for the message."""
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    for key, prop in properties.items():
        if key not in settings or not isinstance(prop, dict):
            continue
        value, title = settings[key], f"{where}{prop.get('title', key)}"
        if _is_empty(value):
            if key in required:
                raise ValueError(f"{title} is required.")
            continue
        if problem := _broken_rule(value, prop):
            raise ValueError(f"{title} {problem}.")
        items = prop.get("items")
        if isinstance(value, list) and isinstance(items, dict) and items.get("type") == "object":
            for number, item in enumerate(value, 1):
                if isinstance(item, dict):
                    check_setting_rules(item, items, f"{prop.get('title', key)} #{number}: ")
