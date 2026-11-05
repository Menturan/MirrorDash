# SPDX-License-Identifier: AGPL-3.0-only
"""Admin forms built from a JSON schema (a module's config_schema, the global settings), and the
posted form read back into nested data. One macro per kind of field, in templates/schema_form.html."""

import functools
import itertools
import json
import logging
import re
from dataclasses import dataclass

from jinja2 import Environment, PackageLoader
from markupsafe import Markup

logger = logging.getLogger("mirrordash.core.forms")


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # in datetime.weekday()'s order
POSITIONS = ["top_left","top_center", "top_right",
             "middle_left", "middle_center", "middle_right",
             "bottom_left", "bottom_center", "bottom_right",
             "background", "foreground"]  # the full-screen layers behind and in front of the nine (ARCHITECTURE #1)
# Settings the core gives every module (module developers must NOT declare these in config_schema)
STANDARD_SCHEMA = {"properties": {
    "enabled": {"type": "boolean", "default": True, "title": "Enabled",
                "description": "Enable or disable this module on the mirror."},
    "position": {"type": "string", "default": "middle_center", "enum": POSITIONS, "title": "Screen Position",
                 "description": "Which anchor region on the mirror this module floats from. Background and Foreground fill the whole screen, behind and in front of the others."},
    "carousel_group": {"type": "string", "default": "", "title": "Carousel Group",
                       "description": "Assign a group name to rotate this module with others in the same region."},
    "carousel_interval": {"type": "integer", "default": 15, "title": "Carousel Interval (s)",
                          "description": "Seconds between carousel slides."},
    "max_width": {"type": "string", "default": "", "title": "Max Width",
                  "description": "CSS length (e.g. 400px, 30vw). Leave blank for no constraint."},
    "max_height": {"type": "string", "default": "", "title": "Max Height",
                   "description": "CSS length (e.g. 300px, 50vh). Leave blank for no constraint."},
    "z_index": {"type": "integer", "default": "", "title": "Z-Index",
                "description": "Stacking order when modules overlap. Higher = on top."},
    "opacity": {"type": "number", "default": "", "title": "Opacity",
                "description": "Module transparency: 1 = fully visible, 0 = invisible."},
    "show_in_privacy": {"type": "boolean", "default": False, "title": "Shown in Privacy Mode",
                        "description": "Stay on the screen in privacy mode, for guests: the clock, the weather, a guest album. Modules without it are hidden then."},
    # Its own collapsible section in the module's settings (features/power/schedule.py)
    "schedule": {"type": "object", "title": "Schedule",
                 "description": "Show this module only on some days and at some times; outside them it is hidden. A time like 22:00 to 06:00 runs over midnight and belongs to the day it starts.",
                 "properties": {
                     "days": {"type": "array", "items": {"type": "string", "enum": list(DAYS)}, "default": list(DAYS),
                              "title": "Days", "description": "The days it is shown."},
                     "from": {"type": "string", "format": "time", "default": "", "title": "From",
                              "description": "Blank: from midnight."},
                     "to": {"type": "string", "format": "time", "default": "", "title": "To",
                            "description": "Blank: until midnight."},
                 }},
}}
STANDARD_FIELDS = list(STANDARD_SCHEMA["properties"])
# A setting whose choices the module gives (ARCHITECTURE #48): saved as {"id", "name"}
CHOICE_FORMATS = ("list", "search")
# Fields shown as a password or a text box by their name when the schema doesn't say
SECRET_KEYS = ("api_key", "password", "token", "secret")
LONG_TEXT_KEYS = ("description", "text", "message", "preamble")
# The colour and icon pickers in list items (e.g. calendar entries).
# Each swatch: (name, the value saved, the colour shown on the button)
SWATCHES = [
    ("White", "#ffffff", "#ffffff"),
    ("Ice Blue", "var(--color-ice-blue)", "#cceeff"),
    ("Rose Pink", "var(--color-rose-pink)", "#ffccd5"),
    ("Green", "var(--color-status-online)", "#a0ffba"),
    ("Red", "var(--color-status-warning)", "#f87171"),
    ("Gray", "var(--color-standard-gray)", "#999999"),
    ("Charcoal", "var(--color-dimmed-charcoal)", "#666666"),
]
ICONS = ["calendar", "clock", "users", "briefcase", "home", "heart", "gift", "trophy",
         "music", "plane", "shopping-cart", "utensils", "alert-circle", "book-open", "coffee", "film"]


def cast_standard_fields(module_cfg: dict) -> dict:
    """The core's standard fields as their schema types: they aren't in the module's own config_schema,
    so casting by that leaves them as the form's text. Call it after that."""
    return cast_values_by_schema(module_cfg, STANDARD_SCHEMA)


# A schema key or a form name prefix: they end up in ids, names and JS strings in onclick, where HTML
# escaping doesn't help (the browser decodes it before the JS runs), so only these characters pass
SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_PREFIX = re.compile(r"^[A-Za-z0-9_\[\]-]{0,256}$")


def _html_id(*parts) -> str:
    """An HTML id from its parts: "field", "modules[clock]", "show_seconds" -> "field-modules-clock--show-seconds"."""
    return "-".join(str(p) for p in parts).replace("[", "-").replace("]", "-").replace("_", "-")


@functools.cache
def _macros():
    """templates/schema_form.html's macros, autoescaped: everything in a schema comes from a module's author."""
    env = Environment(loader=PackageLoader("mirrordash_core"), autoescape=True)
    env.globals.update(html_id=_html_id, SWATCHES=SWATCHES, ICONS=ICONS)
    return env.get_template("schema_form.html").module


def _safe_properties(properties) -> dict:
    """The schema's properties whose keys are SAFE_KEY; others are left out of the form."""
    if not isinstance(properties, dict):
        return {}
    unsafe_keys = [key for key in properties if not SAFE_KEY.match(str(key)) or not isinstance(properties[key], dict)]
    if unsafe_keys:
        logger.warning(f"Schema properties left out of the form (keys may hold only letters, digits, - and _): {unsafe_keys}")
    return {key: prop for key, prop in properties.items() if key not in unsafe_keys}


@dataclass
class Field:
    """One schema property, ready to render (the template escapes it)."""
    key: str
    schema: dict  # the property's own schema: type, title, enum, default...
    stored_value: object  # what is saved, or the schema's default
    id: str  # the input's HTML id
    name: str  # the input's form name, e.g. "modules[clock][show_seconds]"
    prefix: str  # the form name of what the field sits in
    module_name: str
    required: bool = False  # in its schema's "required": the browser won't send the form empty (setting_rules.py)

    @property
    def value(self) -> str:
        return str(self.stored_value)

    @property
    def title(self) -> str:
        return str(self.schema.get("title", self.key))

    @property
    def description(self) -> str:
        return str(self.schema.get("description", ""))

    @property
    def instance_id(self) -> str | None:
        """The module instance a top-level field of its settings belongs to ("modules[clock-2]" -> "clock-2")."""
        match = re.fullmatch(r"modules\[([A-Za-z0-9_-]+)\]", self.prefix)
        return match[1] if match else None


def _array(field: Field) -> str:
    items_schema = field.schema.get("items", {})
    item_title = str(items_schema.get("title", "Item"))
    # Only objects: a list saved by an older version of the module (["https://..."]) has no fields to show
    items = [item for item in field.stored_value if isinstance(item, dict)] if isinstance(field.stored_value, list) else []
    required = items_schema.get("required") if isinstance(items_schema.get("required"), list) else []
    cards = "\n".join(render_array_item(field.prefix, field.key, items_schema.get("properties", {}), i, item, item_title,
                                         required) for i, item in enumerate(items))
    return _macros().array(field, Markup(cards), item_title)


def _object(field: Field) -> str:
    inner = render_schema_form({"properties": field.schema.get("properties", {})},
                               field.stored_value if isinstance(field.stored_value, dict) else {}, field.name, field.module_name)
    return _macros().accordion(field, Markup(inner))


def _item_schema(prop: dict) -> dict:
    return prop.get("items", {})


def _is_choices(key: str, prop: dict) -> bool:
    """A list of strings from an enum: a box per choice."""
    return prop.get("type") == "array" and _item_schema(prop).get("type") == "string" and bool(_item_schema(prop).get("enum"))


# Which input a schema property gets: the first rule that matches (type, then format, then name); a
# macro's name in templates/schema_form.html, or a function for the fields that hold fields
WIDGET_RULES = [
    (lambda key, p: p.get("format") == "files", "files"),
    (lambda key, p: p.get("type") == "boolean", "toggle"),
    (lambda key, p: bool(p.get("enum")), "select"),
    (_is_choices, "checkboxes"),
    (lambda key, p: p.get("type") == "array" and _item_schema(p).get("type") == "object", _array),
    (lambda key, p: p.get("format") in CHOICE_FORMATS, "choice"),
    (lambda key, p: p.get("type") == "object", _object),
    (lambda key, p: p.get("type") in ("integer", "number") and "minimum" in p and "maximum" in p, "slider"),
    (lambda key, p: p.get("type") in ("integer", "number"), "number"),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "color" or key == "color"), "color"),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "password" or key in SECRET_KEYS), "password"),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "textarea" or key in LONG_TEXT_KEYS), "textarea"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "lan-url", "lan_url"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "icon", "icon"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "time", "time"),
]


def _render(widget, field: Field) -> str:
    return widget(field) if callable(widget) else getattr(_macros(), widget)(field)


def render_schema_form(schema: dict, current_values: dict, name_prefix: str = "", module_name: str = "") -> str:
    """The form for a schema's properties: the core's standard fields first, then the module's own."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_PREFIX.match(module_name):
        raise ValueError("Unsafe form name prefix or module name")
    properties = _safe_properties(schema.get("properties", {}))
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    keys = [k for k in STANDARD_FIELDS if k in properties] + [k for k in properties if k not in STANDARD_FIELDS]
    parts = []
    for key in keys:
        prop = properties[key]
        value = current_values.get(key)
        default = "" if prop.get("format") == "lan-url" else prop.get("default", "")  # lan_url: a hint only
        field = Field(key, prop, default if value is None else value,
                      _html_id("field", name_prefix, key), f"{name_prefix}[{key}]" if name_prefix else key,
                      name_prefix, module_name, key in required)
        widget = next((widget for matches, widget in WIDGET_RULES if matches(key, prop)), "text")
        parts.append(_shown_if(_render(widget, field), prop, properties, current_values, name_prefix))
    return "\n".join(parts)


def _shown_if(html: str, prop: dict, properties: dict, current_values: dict, name_prefix: str) -> str:
    """A field with "show_if": {"source": ["immich"]} is shown only while each named field of its form has
    one of the values (ARCHITECTURE #50): hidden from the start here, so it doesn't flash, then show_if.js
    follows the form. Hidden it is still sent, so its value stays saved."""
    rules = prop.get("show_if")
    if not isinstance(rules, dict):
        return html
    rules = {key: [str(v) for v in (values if isinstance(values, list) else [values])]
             for key, values in rules.items() if key in properties}  # keys from _safe_properties: SAFE_KEY

    def current(key):
        value = current_values.get(key)
        return str(properties[key].get("default", "") if value is None else value)
    shown = all(current(key) in values for key, values in rules.items())
    names = {(f"{name_prefix}[{key}]" if name_prefix else key): values for key, values in rules.items()}
    return Markup('<div class="show-if" data-show-if="{}"{}>{}</div>').format(
        json.dumps(names), "" if shown else Markup(" hidden"), Markup(html))


def _sub_choice(field: Field) -> str:
    return _macros().choice(field, True)


def _sub_widget(key: str, prop: dict):
    """The input of a field in a list item (the colour and icon pickers by their names aside)."""
    if prop.get("format") in CHOICE_FORMATS:
        return _sub_choice
    if prop.get("enum"):
        return "sub_select"
    if _is_choices(key, prop):
        return "sub_checkboxes"
    if prop.get("format") == "icon":
        return "sub_icon"
    if prop.get("format") == "password" or key in SECRET_KEYS:
        return "sub_password"
    return "sub_text"


def render_array_item(name_prefix: str, array_key: str, sub_properties: dict, index: int, item_val: dict, item_title: str,
                      required: list | tuple = ()) -> str:
    """One card in a list setting (e.g. a calendar), with move up/down and remove."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_KEY.match(array_key):
        raise ValueError("Unsafe form name prefix or list name")
    fields = []
    for key, prop in _safe_properties(sub_properties).items():
        value = item_val.get(key)
        field = Field(key, prop, prop.get("default", "") if value is None else value,
                      _html_id("field", name_prefix, array_key, index, key), f"{name_prefix}[{array_key}][{index}][{key}]",
                      name_prefix, "", key in required)
        widget = ({"color": "sub_swatches", "icon": "sub_icons"}.get(key) or _sub_widget(key, prop))
        fields.append(_render(widget, field))
    # A new, empty item opens to be filled in
    return str(_macros().array_item(index, str(item_title), Markup("".join(fields)), not any(item_val.values())))


async def read_form(request) -> dict:
    """The posted form as nested data: `a[b][c]` keys become dicts, repeated keys lists.
    max_files=0 here and in every text form: a posted file is a 400, never an UploadFile where a str goes."""
    flat = {}
    for key, value in (await request.form(max_files=0)).multi_items():
        if key not in flat:
            flat[key] = value
        elif isinstance(flat[key], list):
            flat[key].append(value)
        else:
            flat[key] = [flat[key], value]
    return parse_flat_form_data(flat)


def _merge_repeated_form_values(value):
    """A checkbox with its hidden "false" twin becomes one boolean; other repeated values a list without
    the empty hidden placeholder. A lone "true"/"false" stays text (it may be a text field's value);
    _cast turns it into a boolean where the schema says boolean."""
    if isinstance(value, list):
        if all(isinstance(v, str) and v.lower() in ("true", "false") for v in value):
            return any(v.lower() == "true" for v in value)
        return [v for v in value if v != ""]
    return value


def _get_or_create_child_container(container, token, next_token):
    """The container under `token`, created as a list when the next token is an index."""
    empty_child = [] if isinstance(next_token, int) else {}
    if isinstance(container, dict):
        if not isinstance(container.get(token), type(empty_child)):
            container[token] = empty_child
        return container[token]
    while len(container) <= token:
        container.append(None)
    if not isinstance(container[token], type(empty_child)):
        container[token] = empty_child
    return container[token]


MAX_LIST_INDEX = 999  # items in one list setting (calendars, entities): more is no form's


def parse_flat_form_data(form_data: dict) -> dict:
    """Nest flat form keys: 'a', 'a[b]', 'a[0][b]', 'a[b][0][c]'."""
    result = {}
    for key, value in form_data.items():
        # "a[b][0][c]" -> ["a", "b", 0, "c"]: a name, then each bracket's content, numbers as list indexes
        tokens = [int(part) if part.isascii() and part.isdigit() else part for part in re.split(r"\[|\]", key) if part]
        # An index far past any list a form has would fill the core's memory with placeholders first
        if not tokens or any(isinstance(token, int) and token > MAX_LIST_INDEX for token in tokens):
            continue
        parent = result
        for token, next_token in itertools.pairwise(tokens):
            parent = _get_or_create_child_container(parent, token, next_token)
        last_token = tokens[-1]
        if isinstance(parent, list):
            if not isinstance(last_token, int):
                continue  # 'a[0]' and 'a[0]x' clash: keep the list
            while len(parent) <= last_token:
                parent.append(None)
        parent[last_token] = _merge_repeated_form_values(value)
    return clean_nested_structures(result)


def clean_nested_structures(data):
    """Recursively removes None elements from lists, and cleans empty keys."""
    if isinstance(data, dict):
        return {k: clean_nested_structures(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [clean_nested_structures(item) for item in data if item is not None]
    return data


def _cast_to_schema_type(value, prop: dict):
    """One form value as the type its schema property says."""
    schema_type = prop.get("type")
    if schema_type == "boolean":
        return value.lower() in ("true", "1", "yes", "on") if isinstance(value, str) else bool(value)
    if schema_type in ("integer", "number"):
        try:
            return int(value) if schema_type == "integer" else float(value)
        except (ValueError, TypeError):
            return value
    if schema_type == "array":
        items = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
        item_schema = prop.get("items", {})
        if item_schema.get("type") == "object":
            return [cast_values_by_schema(item, item_schema) if isinstance(item, dict) else item for item in items]
        return items
    if prop.get("format") in CHOICE_FORMATS:  # {"id", "name"} as text, or {} for no choice
        return ({"id": str(value["id"]), "name": str(value.get("name", ""))}
                if isinstance(value, dict) and value.get("id") not in (None, "") else {})
    if isinstance(value, dict):
        return cast_values_by_schema(value, prop)
    return value


def cast_values_by_schema(data: dict, schema: dict) -> dict:
    """Casts string values to their proper types (bool, int, float) based on the schema."""
    if not isinstance(data, dict) or not schema:
        return data
    properties = schema.get("properties", {})
    return {k: _cast_to_schema_type(v, properties[k]) if k in properties else v for k, v in data.items()}
