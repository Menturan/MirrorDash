# SPDX-License-Identifier: AGPL-3.0-only
"""Which list or search setting the settings drawer asks the choices of (ARCHITECTURE #48): from the field's
form name to what the module host's choices.py needs. The form name comes from the browser, so it is
checked against the module's own schema; only a setting that schema marks as a list or search passes."""

import re

from mirrordash_core.forms import CHOICE_FORMATS

MAX_QUERY = 100  # characters of what the owner typed into a search setting
# modules[<instance>][<key>] or, in a list setting, modules[<instance>][<list>][<index>][<key>]
FIELD_NAME = re.compile(r"modules\[[A-Za-z0-9_-]{1,64}\]\[([a-z0-9_]{1,64})\](?:\[(\d{1,3})\]\[([a-z0-9_]{1,64})\])?")


def choice_request(field_name: str, schema: dict | None, module_cfg: dict, query: str = "") -> dict:
    """{kind, key, item, query} for the field field_name of a module's form: item is its list row, or the
    whole form for a field outside a list. ValueError when it isn't a list or search setting of the schema."""
    match = FIELD_NAME.fullmatch(field_name or "")
    if not match:
        raise ValueError(f"not a setting's form name: {field_name!r}")
    outer, index, inner = match.groups()
    properties = (schema or {}).get("properties", {})
    if inner is None:
        key, prop, item = outer, properties.get(outer, {}), module_cfg
    else:
        key = inner
        prop = ((properties.get(outer, {}).get("items") or {}).get("properties") or {}).get(inner, {})
        rows = module_cfg.get(outer)
        row = rows[int(index)] if isinstance(rows, list) and int(index) < len(rows) else None
        item = row if isinstance(row, dict) else {}
    if not isinstance(prop, dict) or prop.get("format") not in CHOICE_FORMATS:
        raise ValueError(f"{key!r} isn't a list or search setting")
    return {"kind": prop["format"], "key": key, "item": item, "query": str(query or "")[:MAX_QUERY]}
