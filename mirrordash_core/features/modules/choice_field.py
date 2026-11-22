# SPDX-License-Identifier: AGPL-3.0-only
"""Which list, search or fields setting the settings drawer asks the module about (ARCHITECTURE #48, #63): from
the field's form name to what the module host's choices.py needs. The form name comes from the browser, so it
is checked against the module's own schema; only a setting that schema marks as one of these passes."""

import re

from mirrordash_core.forms import CHOICE_FORMATS, FIELDS_FORMAT

MAX_QUERY = 100  # characters of what the owner typed into a search setting
# modules[<instance>][<key>] or, in a list setting, modules[<instance>][<list>][<index>][<key>]
FIELD_NAME = re.compile(r"modules\[[A-Za-z0-9_-]{1,64}\]\[([a-z0-9_]{1,64})\](?:\[(\d{1,3})\]\[([a-z0-9_]{1,64})\])?")


def locate_field(field_name: str, schema: dict | None, module_cfg: dict) -> tuple[str, dict, dict]:
    """(key, its property in the schema, the dict it sits in) for the field field_name of a module's form:
    its list row, or the whole form for a field outside a list. ValueError when the name isn't one."""
    match = FIELD_NAME.fullmatch(field_name or "")
    if not match:
        raise ValueError(f"not a setting's form name: {field_name!r}")
    outer, index, inner = match.groups()
    properties = (schema or {}).get("properties", {})
    if inner is None:
        return outer, properties.get(outer, {}), module_cfg
    prop = ((properties.get(outer, {}).get("items") or {}).get("properties") or {}).get(inner, {})
    rows = module_cfg.get(outer)
    row = rows[int(index)] if isinstance(rows, list) and int(index) < len(rows) else None
    return inner, prop, row if isinstance(row, dict) else {}


def choice_request(field_name: str, schema: dict | None, module_cfg: dict, query: str = "") -> dict:
    """{kind, key, item, query} for the field field_name of a module's form: item is its list row, or the
    whole form for a field outside a list; for a fields setting without the setting itself (its boxes are
    what is asked for). ValueError when it isn't a list, search or fields setting of the schema."""
    key, prop, item = locate_field(field_name, schema, module_cfg)
    if not isinstance(prop, dict) or prop.get("format") not in (*CHOICE_FORMATS, FIELDS_FORMAT):
        raise ValueError(f"{key!r} isn't a list, search or fields setting")
    if prop["format"] == FIELDS_FORMAT:
        item = {k: v for k, v in item.items() if k != key}
    return {"kind": prop["format"], "key": key, "item": item, "query": str(query or "")[:MAX_QUERY]}
