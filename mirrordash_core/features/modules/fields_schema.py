# SPDX-License-Identifier: AGPL-3.0-only
"""The answer of a fields setting's settings_fields_<key>(item) (ARCHITECTURE #63): schema properties the
module builds itself, kept only where the form can draw them with its own boxes and the save can read them
back. The answer is input from a module, so it is checked here: what isn't allowed is left out, texts are cut."""

from mirrordash_core.forms import FIELDS_FORMAT, SAFE_KEY

MAX_FIELDS = 100  # more boxes than this isn't a form an owner fills in
MAX_TEXT = 200  # characters of a title, description, option or other text (as choices.py cuts a choice's)
MAX_OPTIONS = 2000  # tick boxes or dropdown options in one field (as choices.py's MAX_CHOICES)
FIELD_TYPES = ("string", "integer", "number", "boolean", "array")  # no objects: a field holds no fields
# Formats that draw a box of their own kind: a field of another setting kind, or one that opens an address
NOT_IN_FIELDS = ("list", "search", "files", FIELDS_FORMAT, "lan-url")


def _cut(value):
    """A copy of value with every text cut to MAX_TEXT and every list to MAX_OPTIONS."""
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, dict):
        return {str(k)[:MAX_TEXT]: _cut(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_cut(v) for v in value[:MAX_OPTIONS]]
    return value if isinstance(value, (bool, int, float)) or value is None else str(value)[:MAX_TEXT]


def _is_allowed(key, prop) -> bool:
    """A key the form can name (and not one of the form's own, which start with __) and a property it can draw."""
    if not (SAFE_KEY.match(str(key)) and not str(key).startswith("__") and isinstance(prop, dict)):
        return False
    if prop.get("type") not in FIELD_TYPES or prop.get("format") in NOT_IN_FIELDS:
        return False
    if prop["type"] == "array":  # only tick boxes: strings from an enum
        items = prop.get("items")
        return isinstance(items, dict) and items.get("type") == "string" and bool(items.get("enum")) \
            and isinstance(items["enum"], list)
    return True


def clean_fields(raw) -> dict:
    """The properties of a module's answer that a fields setting may have, as copies. TypeError when the
    answer isn't a dict (a module's mistake, logged by the caller)."""
    if not isinstance(raw, dict):
        raise TypeError(f"gave {type(raw).__name__}, not a dict of fields")
    allowed = [(str(key), prop) for key, prop in raw.items() if _is_allowed(key, prop)]
    return {key: _cut(prop) for key, prop in allowed[:MAX_FIELDS]}
