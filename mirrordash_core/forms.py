# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Admin forms built from a JSON schema (a module's config_schema, the global settings), and the
posted form read back into nested data. One small render function per kind of field."""

import itertools
import json
import logging
import re
from dataclasses import dataclass
from html import escape

logger = logging.getLogger("mirrordash.core.forms")


POSITIONS = ["top_left", "top_center", "top_right",
             "middle_left", "middle_center", "middle_right",
             "bottom_left", "bottom_center", "bottom_right"]
# Settings the core gives every module (module developers must NOT declare these in config_schema)
STANDARD_SCHEMA = {"properties": {
    "enabled": {"type": "boolean", "default": True, "title": "Enabled",
                "description": "Enable or disable this module on the mirror."},
    "position": {"type": "string", "default": "middle_center", "enum": POSITIONS, "title": "Screen Position",
                 "description": "Which anchor region on the mirror this module floats from."},
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
}}
STANDARD_FIELDS = list(STANDARD_SCHEMA["properties"])
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
    """Coerce standard core-owned fields to their correct Python types.

    Because standard fields are NOT in the module's own config_schema, they
    bypass cast_values_by_schema.  This function must be called explicitly on
    the parsed form data after schema-based casting.
    """
    result = dict(module_cfg)
    if "enabled" in result:
        enabled = result["enabled"]
        result["enabled"] = enabled.lower() in ("true", "1", "yes") if isinstance(enabled, str) else bool(enabled)
    for key, cast_fn in (("carousel_interval", int), ("z_index", int), ("opacity", float)):
        if key in result and result[key] not in (None, ""):
            try:
                result[key] = cast_fn(result[key])
            except (ValueError, TypeError):
                pass
    return result


# A schema key or a form name prefix: they end up in ids, names and JS strings in onclick, where HTML
# escaping doesn't help (the browser decodes it before the JS runs), so only these characters pass
SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SAFE_PREFIX = re.compile(r"^[A-Za-z0-9_\[\]-]{0,256}$")


def _html_id(*parts) -> str:
    """An HTML id from its parts: "field", "modules[clock]", "show_seconds" -> "field-modules-clock--show-seconds"."""
    return escape("-".join(str(p) for p in parts).replace("[", "-").replace("]", "-").replace("_", "-"), quote=True)


def _as_js_string_in_attribute(value) -> str:
    """A value as a JS string literal inside an HTML attribute."""
    return escape(json.dumps(str(value)), quote=True)


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
    """One schema property, ready to render. Everything from a schema comes from a module's author and
    is escaped: `value`, `title` and `description` here, the rest where it is used."""
    key: str
    schema: dict  # the property's own schema: type, title, enum, default...
    stored_value: object  # what is saved, or the schema's default
    id: str  # the input's HTML id
    name: str  # the input's form name, e.g. "modules[clock][show_seconds]"
    prefix: str  # the form name of what the field sits in
    module_name: str

    @property
    def value(self) -> str:
        return escape(str(self.stored_value), quote=True)

    @property
    def title(self) -> str:
        return escape(str(self.schema.get("title", self.key)), quote=True)

    @property
    def description(self) -> str:
        return escape(str(self.schema.get("description", "")), quote=True)


def _label(field: Field, with_for: bool = True) -> str:
    target = f' for="{field.id}"' if with_for else ""
    return (f'<div class="form-label-desc"><label{target} class="field-title">{field.title}</label>'
            f'<p class="field-description">{field.description}</p></div>')


def _options(prop: dict, current) -> str:
    titles = prop.get("enum_titles") or prop.get("enumNames")
    return "\n".join(
        f'<option value="{escape(str(opt), quote=True)}"{" selected" if current == opt else ""}>'
        f'{escape(str(titles[i] if titles and i < len(titles) else str(opt).replace("_", " ").upper()))}</option>'
        for i, opt in enumerate(prop["enum"]))


def _toggle(field: Field) -> str:
    return f'''<div class="form-group toggle-group schema-field">{_label(field)}
        <label class="switch"><input type="hidden" name="{field.name}" value="false">
        <input type="checkbox" id="{field.id}" name="{field.name}" value="true"{" checked" if field.stored_value else ""}>
        <span class="slider round"></span></label></div>'''


def _select(field: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(field)}
        <select id="{field.id}" name="{field.name}" class="form-control">{_options(field.schema, field.stored_value)}</select></div>'''


def _checkboxes(field: Field) -> str:
    chosen = field.stored_value if isinstance(field.stored_value, list) else []
    boxes = "\n".join(
        f'<label class="checkbox-inline"><input type="checkbox" id="{_html_id(field.id, opt)}" name="{field.name}" '
        f'value="{escape(str(opt), quote=True)}"{" checked" if opt in chosen else ""}><span>{escape(str(opt).replace("_", " ").upper())}</span></label>'
        for opt in field.schema["items"]["enum"])
    return f'''<div class="form-group schema-group">{_label(field, with_for=False)}
        <div class="schema-checkboxes"><input type="hidden" name="{field.name}" value="">{boxes}</div></div>'''


def _array(field: Field) -> str:
    items_schema = field.schema.get("items", {})
    item_title = str(items_schema.get("title", "Item"))
    items = field.stored_value if isinstance(field.stored_value, list) else []
    cards = "\n".join(render_array_item(field.prefix, field.key, items_schema.get("properties", {}), i, item, item_title)
                      for i, item in enumerate(items))
    container_id = f"array-container-{field.key}"
    return f'''<div class="form-group schema-group">{_label(field, with_for=False)}
        <div class="array-items-container" id="{container_id}">{cards}</div>
        <button type="button" class="btn secondary btn-sm" hx-get="/admin/panels/config/add-array-item"
                hx-vals='js:{{index: document.querySelectorAll("#{container_id} .array-item-card").length, name_prefix: {_as_js_string_in_attribute(field.prefix)}, array_key: {_as_js_string_in_attribute(field.key)}, item_title: {_as_js_string_in_attribute(item_title)}, module_name: {_as_js_string_in_attribute(field.module_name)}}}'
                hx-target="#{container_id}" hx-swap="beforeend"><i class="fas fa-plus"></i> Add {escape(item_title)}</button></div>'''


def _object(field: Field) -> str:
    inner = render_schema_form({"properties": field.schema.get("properties", {})},
                               field.stored_value if isinstance(field.stored_value, dict) else {}, field.name, field.module_name)
    return f'''<details class="form-accordion"><summary>{field.title}</summary>
        <div class="form-accordion-body"><p class="field-description">{field.description}</p>{inner}</div></details>'''


def _range(field: Field) -> str:
    step = escape(str(field.schema.get("step", "1" if field.schema["type"] == "integer" else "any")), quote=True)
    minimum, maximum = (escape(str(field.schema[bound]), quote=True) for bound in ("minimum", "maximum"))
    return f'''<div class="form-group schema-field">{_label(field)}
        <div class="range-row"><input type="range" min="{minimum}" max="{maximum}" step="{step}" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control-range" oninput="this.nextElementSibling.value = this.value">
        <output>{field.value}</output></div></div>'''


def _number(field: Field) -> str:
    step = "1" if field.schema["type"] == "integer" else "any"
    return f'''<div class="form-group schema-field">{_label(field)}
        <input type="number" step="{step}" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control"></div>'''


def _color(field: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(field)}
        <div class="color-row"><input type="color" id="{field.id}-picker" value="{field.value}" oninput="document.getElementById('{field.id}').value = this.value">
        <input type="text" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control" oninput="document.getElementById('{field.id}-picker').value = this.value"></div></div>'''


def _password(field: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(field)}
        <div class="pw-field"><input type="password" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control"><button type="button" class="pw-reveal" data-reveal="{field.id}" aria-controls="{field.id}" aria-pressed="false">Show</button></div></div>'''


def _textarea(field: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(field)}
        <textarea id="{field.id}" name="{field.name}" rows="3" class="form-control schema-textarea">{field.value}</textarea></div>'''


def _text(field: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(field)}
        <input type="text" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control"></div>'''


def _lan_url(field: Field) -> str:
    """An address at home the module may reach (client.py lets the module host through to it). The schema's
    default is only a hint, never filled in: saving the form must not open an address the owner didn't type."""
    hint = escape(str(field.schema.get("default", "")), quote=True)
    return f'''<div class="form-group schema-field">{_label(field)}
        <input type="text" id="{field.id}" name="{field.name}" value="{field.value}" placeholder="{hint}" class="form-control">
        <p class="field-description">MirrorDash lets this module reach this address on your home network, and nothing else there.</p></div>'''


def _item_schema(prop: dict) -> dict:
    return prop.get("items", {})


# Which input a schema property gets: the first rule that matches (type, then format, then name)
WIDGET_RULES = [
    (lambda key, p: p.get("type") == "boolean", _toggle),
    (lambda key, p: bool(p.get("enum")), _select),
    (lambda key, p: p.get("type") == "array" and _item_schema(p).get("type") == "string" and bool(_item_schema(p).get("enum")), _checkboxes),
    (lambda key, p: p.get("type") == "array" and _item_schema(p).get("type") == "object", _array),
    (lambda key, p: p.get("type") == "object", _object),
    (lambda key, p: p.get("type") in ("integer", "number") and "minimum" in p and "maximum" in p, _range),
    (lambda key, p: p.get("type") in ("integer", "number"), _number),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "color" or key == "color"), _color),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "password" or key in SECRET_KEYS), _password),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "textarea" or key in LONG_TEXT_KEYS), _textarea),
    (lambda key, p: p.get("type") == "string" and p.get("format") == "lan-url", _lan_url),
]


def _render_function_for(key: str, prop: dict):
    return next((render for matches, render in WIDGET_RULES if matches(key, prop)), _text)


def render_schema_form(schema: dict, current_values: dict, name_prefix: str = "", module_name: str = "") -> str:
    """The form for a schema's properties: the core's standard fields first, then the module's own."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_PREFIX.match(module_name):
        raise ValueError("Unsafe form name prefix or module name")
    properties = _safe_properties(schema.get("properties", {}))
    keys = [k for k in STANDARD_FIELDS if k in properties] + [k for k in properties if k not in STANDARD_FIELDS]
    parts = []
    for key in keys:
        prop = properties[key]
        value = current_values.get(key)
        default = "" if prop.get("format") == "lan-url" else prop.get("default", "")  # _lan_url: a hint only
        field = Field(key, prop, default if value is None else value,
                      _html_id("field", name_prefix, key), f"{name_prefix}[{key}]" if name_prefix else key,
                      name_prefix, module_name)
        parts.append(_render_function_for(key, prop)(field))
    return "\n".join(parts)


def _sub_swatches(field: Field) -> str:
    swatches = "\n".join(
        f'''<button type="button" class="color-swatch-btn{" active" if field.stored_value == value else ""}" style="background-color: {preview_hex_color};" title="{title}" onclick="selectSubFieldColor(this, '{field.id}', '{value}')"></button>'''
        for title, value, preview_hex_color in SWATCHES)
    return f'''<div class="sub-form-group wide"><label for="{field.id}">{field.title}</label>
        <div class="swatch-row"><div class="swatches">{swatches}</div>
        <input type="text" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control form-control-sm swatch-value" oninput="updateSwatchSelection(this)"></div></div>'''


def _sub_icons(field: Field) -> str:
    icons = "\n".join(
        f'''<button type="button" class="icon-picker-btn{" active" if field.stored_value == icon else ""}" title="{icon}" onclick="selectSubFieldIcon(this, '{field.id}', '{icon}')"><i data-lucide="{icon}"></i></button>'''
        for icon in ICONS)
    return f'''<div class="sub-form-group wide"><label for="{field.id}">{field.title}</label>
        <div class="icon-picker"><div class="icon-grid">{icons}</div>
        <div class="icon-custom"><span>Custom Name:</span><input type="text" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control form-control-sm" oninput="updateIconSelection(this)"></div></div></div>'''


def _sub_select(field: Field) -> str:
    return f'''<div class="sub-form-group"><label for="{field.id}">{field.title}</label>
        <select id="{field.id}" name="{field.name}" class="form-control form-control-sm">{_options(field.schema, field.stored_value)}</select></div>'''


def _sub_text(field: Field) -> str:
    return f'''<div class="sub-form-group"><label for="{field.id}">{field.title}</label>
        <input type="text" id="{field.id}" name="{field.name}" value="{field.value}" class="form-control form-control-sm"></div>'''


def render_array_item(name_prefix: str, array_key: str, sub_properties: dict, index: int, item_val: dict, item_title: str) -> str:
    """One card in a list setting (e.g. a calendar), with move up/down and remove."""
    if not SAFE_PREFIX.match(name_prefix) or not SAFE_KEY.match(array_key):
        raise ValueError("Unsafe form name prefix or list name")
    item_title = escape(str(item_title), quote=True)
    fields = []
    for key, prop in _safe_properties(sub_properties).items():
        value = item_val.get(key)
        field = Field(key, prop, prop.get("default", "") if value is None else value,
                      _html_id("field", name_prefix, array_key, index, key), f"{name_prefix}[{array_key}][{index}][{key}]",
                      name_prefix, "")
        render = {"color": _sub_swatches, "icon": _sub_icons}.get(key) or (_sub_select if prop.get("enum") else _sub_text)
        fields.append(render(field))
    return f'''<div class="array-item-card">
        <div class="array-item-tools">
            <button type="button" class="btn secondary btn-sm" onclick="moveArrayItem(this, 'up')" title="Move Up"><i class="fas fa-arrow-up"></i></button>
            <button type="button" class="btn secondary btn-sm" onclick="moveArrayItem(this, 'down')" title="Move Down"><i class="fas fa-arrow-down"></i></button>
            <button type="button" class="btn danger btn-sm" onclick="const container = this.closest('.array-items-container'); this.closest('.array-item-card').remove(); reindexArrayContainer(container); triggerLucide();" title="Remove"><i class="fas fa-trash"></i></button>
        </div>
        <div class="array-item-index-title" data-item-title="{item_title}"># {index + 1}: {item_title}</div>
        <div class="array-item-fields">{"".join(fields)}</div>
    </div>'''


async def read_form(request) -> dict:
    """The posted form as nested data: `a[b][c]` keys become dicts, repeated keys lists."""
    flat = {}
    for key, value in (await request.form()).multi_items():
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


def parse_flat_form_data(form_data: dict) -> dict:
    """Nest flat form keys: 'a', 'a[b]', 'a[0][b]', 'a[b][0][c]'."""
    result = {}
    for key, value in form_data.items():
        # "a[b][0][c]" -> ["a", "b", 0, "c"]: a name, then each bracket's content, numbers as list indexes
        tokens = [int(part) if part.isdigit() else part for part in re.split(r"\[|\]", key) if part]
        if not tokens:
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
    if isinstance(value, dict):
        return cast_values_by_schema(value, prop)
    return value


def cast_values_by_schema(data: dict, schema: dict) -> dict:
    """Casts string values to their proper types (bool, int, float) based on the schema."""
    if not isinstance(data, dict) or not schema:
        return data
    properties = schema.get("properties", {})
    return {k: _cast_to_schema_type(v, properties[k]) if k in properties else v for k, v in data.items()}
