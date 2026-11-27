# SPDX-License-Identifier: AGPL-3.0-only
"""Admin forms built from a JSON schema (a module's config_schema, the global settings), and the
posted form read back into nested data. One macro per kind of field, in templates/schema_form.html."""

import functools
import itertools
import json
import logging
import re
from dataclasses import dataclass, field

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
    # The owner's own name for this one (features/modules/service.py instance_title); not "title", which a
    # module may well use for a heading of its own. Edited on the instance chip. "enabled" is no form field:
    # saving keeps what is saved (features/modules/routes.py)
    "instance_name": {"type": "string", "default": "", "maxLength": 40, "title": "Name", "section": "name",
                      "description": "What this one is called on the admin page and in its notifications, e.g. Kitchen Clock. Blank: the module's own name."},
    "position": {"type": "string", "default": "middle_center", "enum": POSITIONS, "title": "Position", "section": "placement", "reset": False,
                 "description": "Which anchor region on the mirror this module floats from. Background and Foreground fill the whole screen, behind and in front of the others."},
    "width_percent": {"type": "integer", "format": "slider", "minimum": 0, "maximum": 100, "step": 5, "default": 0,
                      "title": "Width", "section": "placement", "min_label": "Auto", "unit": "%",
                      "description": "Share of the screen width inside the margins. Auto: the module's own preferred width, else at most a third of it."},
    "height_percent": {"type": "integer", "format": "slider", "minimum": 0, "maximum": 100, "step": 5, "default": 0,
                       "title": "Height", "section": "placement", "min_label": "Auto", "unit": "%",
                       "description": "Share of the screen height inside the margins. Auto: at most half of it. What does not fit scrolls or fades out."},
    "opacity_percent": {"type": "integer", "format": "slider", "minimum": 10, "maximum": 100, "step": 5, "default": 100,
                        "title": "Opacity", "section": "placement", "unit": "%",
                        "description": "How solid it is: 100 is fully visible."},
    "overflow": {"type": "string", "default": "scroll", "enum": ["scroll", "fade"], "enum_titles": ["Scroll", "Fade"],
                 "title": "When it doesn't fit", "section": "placement",
                 "description": "Scroll slowly through what doesn't fit, or fade it out at the bottom."},
    "show_header": {"type": "boolean", "default": True, "title": "Show heading", "section": "heading",
                    "description": "Show the module's heading above its content."},
    "heading": {"type": "string", "default": "", "maxLength": 60, "title": "Heading text", "section": "heading",
                "description": "Blank: the module's own title."},
    "show_in_privacy": {"type": "boolean", "default": False, "title": "Show in privacy mode", "section": "when",
                        "description": "Stay on the screen in privacy mode, for guests: the clock, the weather, a guest album. Modules without it are hidden then."},
    # Its own collapsible section in the module's settings (features/power/schedule.py)
    "schedule": {"type": "object", "title": "Schedule", "section": "when",
                 "description": "Show this module only on some days and at some times; outside them it is hidden. A time like 22:00 to 06:00 runs over midnight and belongs to the day it starts.",
                 "properties": {
                     "days": {"type": "array", "items": {"type": "string", "enum": list(DAYS)}, "default": list(DAYS),
                              "title": "Days", "description": "The days it is shown."},
                     "from": {"type": "string", "format": "time", "default": "", "title": "From",
                              "description": "Blank: from midnight."},
                     "to": {"type": "string", "format": "time", "default": "", "title": "To",
                            "description": "Blank: until midnight."},
                 }},
    "carousel_group": {"type": "string", "default": "", "title": "Take turns with", "section": "when",
                       "description": "A group name: modules in the same region with the same name take turns on the screen."},
    "carousel_interval": {"type": "integer", "default": 15, "title": "Switch every", "section": "when", "unit": "s",
                          "show_if": {"carousel_group": {"not": [""]}},
                          "description": "Seconds between slides."},
}}
STANDARD_FIELDS = list(STANDARD_SCHEMA["properties"])
MODULE_DEFAULT_KEYS = ("show_header", "heading", "opacity_percent", "overflow")


def standard_schema_for(module_schema: dict) -> dict:
    """STANDARD_SCHEMA with the module's own defaults for the standard keys it names (MODULE_DEFAULT_KEYS):
    the module author's choice, which the owner can still change. The module's entry is otherwise ignored."""
    module_properties = module_schema.get("properties") if isinstance(module_schema, dict) else None
    module_properties = module_properties if isinstance(module_properties, dict) else {}
    properties = {}
    for key, prop in STANDARD_SCHEMA["properties"].items():
        theirs = module_properties.get(key)
        if key in MODULE_DEFAULT_KEYS and isinstance(theirs, dict) and "default" in theirs:
            prop = {**prop, "default": theirs["default"]}
        properties[key] = prop
    return {"properties": properties}
# A setting whose choices the module gives (ARCHITECTURE #48): saved as {"id", "name"}
CHOICE_FORMATS = ("list", "search")
# A setting whose boxes the module builds (ARCHITECTURE #63): saved as {field: value} of the boxes it gave last
FIELDS_FORMAT = "fields"
# The colour box's swatches ("format": "color", e.g. a calendar's colour); any CSS colour may be typed beside them.
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
    env.globals.update(html_id=_html_id, SWATCHES=SWATCHES)
    return env.get_template("schema_form.html").module


def _safe_properties(properties) -> dict:
    """The schema's properties whose keys are SAFE_KEY; others are left out of the form."""
    if not isinstance(properties, dict):
        return {}
    unsafe_keys = [key for key in properties if not SAFE_KEY.match(str(key)) or not isinstance(properties[key], dict)]
    if unsafe_keys:
        logger.warning(f"Schema properties left out of the form (keys may hold only letters, digits, - and _): {unsafe_keys}")
    return {key: prop for key, prop in properties.items() if key not in unsafe_keys}


UNIT_WORDS = {"s": "seconds", "min": "minutes", "h": "hours", "%": "%"}  # schema keyword "unit" -> the suffix drawn
# Never reset to a default: secrets, files, and the kinds whose value is a list or an object
NO_RESET_FORMATS = ("password", "files", "lan-url", FIELDS_FORMAT, *CHOICE_FORMATS)


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
    copy_text: str = ""  # copy_value filled in from the stored values of the form level, the row's first text
    suggestions: list = field(default_factory=list)  # names to offer in a text box (a <datalist>)

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
    def help(self) -> str:
        """The longer text behind the info button (schema keyword "help"); the description stays one short line."""
        return str(self.schema.get("help", ""))

    @property
    def unit(self) -> str:
        """The suffix after a number or slider ("seconds"), from the schema keyword "unit"; any other value is ignored."""
        unit = self.schema.get("unit")
        return UNIT_WORDS.get(unit, "") if isinstance(unit, str) else ""

    @property
    def min_label(self) -> str:
        """A slider's words at its minimum, in place of the number (schema keyword "min_label", e.g. "Auto")."""
        return str(self.schema.get("min_label", ""))

    def slider_text(self, value) -> str:
        """What a slider's readout says for value: the min_label at the minimum, else the number and its unit."""
        if self.min_label and value == self.schema["minimum"]:
            return self.min_label
        return f"{value} {self.unit}".strip()

    @property
    def can_reset(self) -> bool:
        """Whether the field offers "Reset to <default>": it has a default and holds one plain value, outside a
        list row (rows have no default of their own to go back to). The core turns it off with "reset": false
        where going back is no help (the position: the card would jump to the middle)."""
        schema = self.schema
        return (schema.get("reset", True) is not False and "default" in schema and schema.get("format") not in NO_RESET_FORMATS and schema.get("type") not in ("array", "object")
                and not re.search(r"\[\d+\]", self.prefix))

    @property
    def default_json(self) -> str:
        return json.dumps(self.schema.get("default"))

    @property
    def default_text(self) -> str:
        """The default as words for the reset link."""
        default = self.schema.get("default")
        if isinstance(default, bool):
            return "on" if default else "off"
        if "enum" in self.schema and default in self.schema["enum"]:
            return str(_macros().option_title(self.schema, self.schema["enum"].index(default))).strip()
        if self.schema.get("format") == "slider" and isinstance(default, (int, float)):
            return self.slider_text(default)
        if default == "":
            return "empty"
        return f"{default} {self.unit}".strip()

    @property
    def copy_value(self) -> str:
        """The template of the "Shared as" row under a switch (schema keyword "copy_value", "{key}" = a sibling's value)."""
        return str(self.schema.get("copy_value", "")) if self.schema.get("type") == "boolean" else ""

    @property
    def instance_id(self) -> str | None:
        """The module instance a top-level field of its settings belongs to ("modules[clock-2]" -> "clock-2")."""
        match = re.fullmatch(r"modules\[([A-Za-z0-9_-]+)\]", self.prefix)
        return match[1] if match else None


@dataclass
class SharedState:
    """How one shared setting stands in an instance's form (ARCHITECTURE #62): its row under the control."""
    own: bool  # the instance has an own value (its "Own value" switch is on)
    shared_value: object  # the value the other instances get ("" for a secret, which is never printed)


def _array(field: Field) -> str:
    items_schema = field.schema.get("items", {})
    item_title = str(items_schema.get("title", "Item"))
    # Only objects: a list saved by an older version of the module (["https://..."]) has no fields to show
    items = [item for item in field.stored_value if isinstance(item, dict)] if isinstance(field.stored_value, list) else []
    required = items_schema.get("required") if isinstance(items_schema.get("required"), list) else []
    cards = "\n".join(render_array_item(field.prefix, field.key, items_schema.get("properties", {}), i, item, item_title,
                                         required) for i, item in enumerate(items))
    # "Add entity" from the list's items.title; without one, "Add Item"
    add_label = f"Add {item_title.lower()}" if "title" in items_schema else "Add Item"
    return _macros().array(field, Markup(cards), item_title, add_label)


def _object(field: Field) -> str:
    inner = render_schema_form({"properties": field.schema.get("properties", {})},
                               field.stored_value if isinstance(field.stored_value, dict) else {}, field.name, field.module_name)
    return _macros().accordion(field, Markup(inner))


def _item_schema(prop: dict) -> dict:
    return prop.get("items", {})


def _is_choices(key: str, prop: dict) -> bool:
    """A list of strings from an enum: a box per choice."""
    items = _item_schema(prop)
    return prop.get("type") == "array" and items.get("type") == "string" and bool(items.get("enum") or items.get("enum_source"))


# Which input a schema property gets: the first rule that matches. The type picks it, the format changes
# it, never the key's name (ARCHITECTURE #53); a macro's name in templates/schema_form.html, or a function
# for the fields that hold fields
WIDGET_RULES = [
    (lambda key, p: p.get("format") == FIELDS_FORMAT, "fields"),
    (lambda key, p: p.get("format") == "files", "files"),
    (lambda key, p: p.get("type") == "boolean", "toggle"),
    (lambda key, p: bool(p.get("enum")), "select"),
    (_is_choices, "checkboxes"),
    (lambda key, p: p.get("type") == "array" and _item_schema(p).get("type") == "object", _array),
    (lambda key, p: p.get("format") in CHOICE_FORMATS, "choice"),
    (lambda key, p: p.get("type") == "object", _object),
    (lambda key, p: p.get("type") in ("integer", "number") and p.get("format") == "slider"
     and "minimum" in p and "maximum" in p, "slider"),
    (lambda key, p: p.get("type") in ("integer", "number"), "number"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "color", "color"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "password", "password"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "textarea", "textarea"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "lan-url", "lan_url"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "icon", "icon"),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "time", "time"),
]


def _render(widget, field: Field) -> str:
    return widget(field) if callable(widget) else getattr(_macros(), widget)(field)


def render_schema_form(schema: dict, current_values: dict, name_prefix: str = "", module_name: str = "",
                       shared: dict[str, SharedState] | None = None,
                       suggestions: dict[str, list[str]] | None = None) -> str:
    """The form for a schema's properties: the core's standard fields first, then the module's own. Fields
    with the same "group" (a title) share a collapsible section where the first of them is; only the form
    changes, they're saved as if they had none (ARCHITECTURE #51). shared: the fields that are shared
    settings of an instance's form (#62) get the "Own value" row, and their box only while it is on.
    suggestions: names to offer in a string field's box, by key (a <datalist>; any other text can still be typed)."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_PREFIX.match(module_name):
        raise ValueError("Unsafe form name prefix or module name")
    properties = _safe_properties(schema.get("properties", {}))
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    keys = [k for k in STANDARD_FIELDS if k in properties] + [k for k in properties if k not in STANDARD_FIELDS]
    parts, groups = [], {}
    for key in keys:
        prop = properties[key]
        value = current_values.get(key)
        default = "" if prop.get("format") == "lan-url" else prop.get("default", "")  # lan_url: a hint only
        field = Field(key, prop, default if value is None else value,
                      _html_id("field", name_prefix, key), f"{name_prefix}[{key}]" if name_prefix else key,
                      name_prefix, module_name, key in required, suggestions=(suggestions or {}).get(key, []))
        if field.copy_value:  # copy_value.js keeps it up to date from here
            field.copy_text = re.sub(r"\{(\w+)\}", lambda m: _sibling_text(m[1], properties, current_values), field.copy_value)
        widget = next((widget for matches, widget in WIDGET_RULES if matches(key, prop)), "text")
        html = _render(widget, field)
        if shared and key in shared:
            html = _macros().shared_field(field, shared[key], Markup(html))
        html = _shown_if(html, prop, properties, current_values, name_prefix)
        if group := str(prop.get("group") or ""):
            if group not in groups:
                groups[group] = []
                parts.append((group, groups[group]))
            groups[group].append(html)
        else:
            parts.append(html)
    return "\n".join(part if isinstance(part, str)
                     else _macros().accordion({"title": part[0], "description": ""}, Markup("\n".join(part[1])))
                     for part in parts)


def _sibling_text(key: str, properties: dict, current_values: dict) -> str:
    """A sibling field's value for a copy_value template, "<key>" while it is empty."""
    value = current_values.get(key)
    text = str(properties.get(key, {}).get("default", "") if value is None else value).strip()
    return text or f"<{key}>"


def _shown_if(html: str, prop: dict, properties: dict, current_values: dict, name_prefix: str) -> str:
    """A field with "show_if": {"source": ["immich"]} (or {"show_cover": [true]}, a switch) is shown only
    while each named field of its form has one of the values (ARCHITECTURE #50): hidden from the start here,
    so it doesn't flash, then show_if.js follows the form. Hidden it is still sent, so its value stays saved."""
    rules = prop.get("show_if")
    if not isinstance(rules, dict):
        return html

    def text(value):  # a switch's True as the browser has it: "true"
        return json.dumps(value) if isinstance(value, bool) else str(value)

    def texts(values):
        return [text(v) for v in (values if isinstance(values, list) else [values])]
    # {"not": [...]} is "anything but these"
    rules = {key: {"not": texts(values["not"])} if isinstance(values, dict) and "not" in values else texts(values)
             for key, values in rules.items() if key in properties}  # keys from _safe_properties: SAFE_KEY

    def current(key):
        value = current_values.get(key)
        return text(properties[key].get("default", "") if value is None else value)

    def matches(key, values):
        return current(key) not in values["not"] if isinstance(values, dict) else current(key) in values
    shown = all(matches(key, values) for key, values in rules.items())
    names = {(f"{name_prefix}[{key}]" if name_prefix else key): values for key, values in rules.items()}
    return Markup('<div class="show-if" data-show-if="{}"{}>{}</div>').format(
        json.dumps(names), "" if shown else Markup(" hidden"), Markup(html))


def render_fields(fields: dict, current: dict, name_prefix: str) -> str:
    """A fields setting's boxes (ARCHITECTURE #63): the module's cleaned properties drawn as the form's own,
    named under name_prefix (the setting's form name), then the type of each, so the save can type the values.
    current: the boxes' values now; a field without one gets its default."""
    inner = (render_schema_form({"properties": fields}, current, name_prefix) if fields
             else str(_macros().fields_empty()))
    return inner + str(_macros().fields_types(name_prefix, json.dumps({key: prop["type"] for key, prop in fields.items()})))


def fields_loading() -> str:
    """What a fields setting shows until the module has answered (the design page swaps in a drawn answer)."""
    return str(_macros().fields_loading())


def render_array_item(name_prefix: str, array_key: str, sub_properties: dict, index: int, item_val: dict, item_title: str,
                      required: list | tuple = ()) -> str:
    """One card in a list setting (e.g. a calendar), with move up/down and remove. Its fields are drawn as
    the form's own (render_schema_form): every kind of box, show_if and group work in a row too (#54)."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_KEY.match(array_key) or type(index) is not int:
        raise ValueError("Unsafe form name prefix or list name")
    fields = render_schema_form({"properties": sub_properties, "required": list(required)}, item_val,
                                f"{name_prefix}[{array_key}][{index}]")
    # A new, empty item opens to be filled in
    return str(_macros().array_item(index, str(item_title), Markup(fields), not any(item_val.values()),
                                    _row_summary(sub_properties, item_val)))


# A string field that names nothing a row could be called by
_UNNAMING_FORMATS = ("password", "color", "icon", "textarea", "files", "time", "lan-url")


def _row_summary(properties: dict, item: dict) -> str:
    """A list row's header: the display text of its first enum field (its enum_titles title), else its first
    non-empty string field, with " \u00b7 off" when its boolean "enabled" is false; "" when it has neither
    (the header is then "# N: <items.title>"). admin.js arrayItemSummary keeps it live."""
    text = ""
    for key, prop in properties.items():
        if isinstance(prop, dict) and prop.get("enum") and item.get(key) in prop["enum"]:
            text = str(_macros().option_title(prop, prop["enum"].index(item[key]))).strip()
            break
    if not text:
        text = next((value.strip() for key, prop in properties.items()
                     if isinstance(prop, dict) and prop.get("type") == "string" and not prop.get("enum")
                     and prop.get("format") not in _UNNAMING_FORMATS
                     and isinstance(value := item.get(key), str) and value.strip()), "")
    enabled = properties.get("enabled")
    if text and isinstance(enabled, dict) and enabled.get("type") == "boolean" and item.get("enabled") is False:
        text += " \u00b7 off"
    return text


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


def _plain_dict(text) -> dict:
    """A JSON object's text as a dict, {} when it is anything else."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _cast_fields(value) -> dict:
    """A fields setting as saved (ARCHITECTURE #63): exactly the boxes the module gave last, each typed by the
    type the form carried for it (__types), so a box that is gone or unticked is not kept. When the boxes never
    loaded (no __types: the module didn't answer) the setting as it was saved, carried in __saved. The form
    comes from the browser, so it is only cast here, never believed."""
    if not isinstance(value, dict):
        return {}
    if "__types" not in value:
        return _plain_dict(value.get("__saved"))
    types = _plain_dict(value["__types"])
    return {key: _cast_to_schema_type(value[key], {"type": schema_type, "items": {"type": "string"}})
            for key, schema_type in types.items() if key in value and SAFE_KEY.match(key)
            and schema_type in ("string", "integer", "number", "boolean", "array")}


def _cast_to_schema_type(value, prop: dict):
    """One form value as the type its schema property says."""
    if prop.get("format") == FIELDS_FORMAT:
        return _cast_fields(value)
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
        if item_schema.get("enum_source"):  # a module that is gone is dropped, no error (ARCHITECTURE #68)
            return [item for item in items if item in item_schema.get("enum", [])]
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
