# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Admin forms built from a JSON schema (a module's config_schema, the global settings), and the
posted form read back into nested data. One small render function per kind of field."""

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
# The colour and icon pickers in list items (e.g. calendar entries)
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
        v = result["enabled"]
        result["enabled"] = v.lower() in ("true", "1", "yes") if isinstance(v, str) else bool(v)
    for key, cast_fn in (("carousel_interval", int), ("z_index", int), ("opacity", float)):
        if key in result and result[key] not in (None, ""):
            try:
                result[key] = cast_fn(result[key])
            except (ValueError, TypeError):
                pass
    return result


def _dom_id(*parts) -> str:
    return "-".join(str(p) for p in parts).replace("[", "-").replace("]", "-").replace("_", "-")


@dataclass
class Field:
    """One schema property, ready to render. `value` is escaped for HTML; title and description
    come from the module's author and may hold markup."""
    key: str
    prop: dict
    raw: object
    id: str
    name: str
    prefix: str
    module_name: str

    @property
    def value(self) -> str:
        return escape(str(self.raw), quote=True)

    @property
    def title(self) -> str:
        return self.prop.get("title", self.key)

    @property
    def description(self) -> str:
        return self.prop.get("description", "")


def _label(f: Field, with_for: bool = True) -> str:
    target = f' for="{f.id}"' if with_for else ""
    return (f'<div class="form-label-desc"><label{target} class="field-title">{f.title}</label>'
            f'<p class="field-description">{f.description}</p></div>')


def _options(prop: dict, current) -> str:
    titles = prop.get("enum_titles") or prop.get("enumNames")
    return "\n".join(
        f'<option value="{escape(str(opt), quote=True)}"{" selected" if current == opt else ""}>'
        f'{titles[i] if titles and i < len(titles) else str(opt).replace("_", " ").upper()}</option>'
        for i, opt in enumerate(prop["enum"]))


def _toggle(f: Field) -> str:
    return f'''<div class="form-group toggle-group schema-field">{_label(f)}
        <label class="switch"><input type="hidden" name="{f.name}" value="false">
        <input type="checkbox" id="{f.id}" name="{f.name}" value="true"{" checked" if f.raw else ""}>
        <span class="slider round"></span></label></div>'''


def _select(f: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(f)}
        <select id="{f.id}" name="{f.name}" class="form-control">{_options(f.prop, f.raw)}</select></div>'''


def _checkboxes(f: Field) -> str:
    chosen = f.raw if isinstance(f.raw, list) else []
    boxes = "\n".join(
        f'<label class="checkbox-inline"><input type="checkbox" id="{_dom_id(f.id, opt)}" name="{f.name}" '
        f'value="{escape(opt, quote=True)}"{" checked" if opt in chosen else ""}><span>{opt.replace("_", " ").upper()}</span></label>'
        for opt in f.prop["items"]["enum"])
    return f'''<div class="form-group schema-group">{_label(f, with_for=False)}
        <div class="schema-checkboxes"><input type="hidden" name="{f.name}" value="">{boxes}</div></div>'''


def _array(f: Field) -> str:
    items_schema = f.prop.get("items", {})
    item_title = items_schema.get("title", "Item")
    items = f.raw if isinstance(f.raw, list) else []
    cards = "\n".join(render_array_item(f.prefix, f.key, items_schema.get("properties", {}), i, item, item_title)
                      for i, item in enumerate(items))
    box = f"array-container-{f.key}"
    return f'''<div class="form-group schema-group">{_label(f, with_for=False)}
        <div class="array-items-container" id="{box}">{cards}</div>
        <button type="button" class="btn secondary btn-sm" hx-get="/admin/panels/config/add-array-item"
                hx-vals='js:{{index: document.querySelectorAll("#{box} .array-item-card").length, name_prefix: "{f.prefix}", array_key: "{f.key}", item_title: "{item_title}", module_name: "{f.module_name}"}}'
                hx-target="#{box}" hx-swap="beforeend"><i class="fas fa-plus"></i> Add {item_title}</button></div>'''


def _object(f: Field) -> str:
    inner = render_schema_form({"properties": f.prop.get("properties", {})},
                               f.raw if isinstance(f.raw, dict) else {}, f.name, f.module_name)
    return f'''<details class="form-accordion"><summary>{f.title}</summary>
        <div class="form-accordion-body"><p class="field-description">{f.description}</p>{inner}</div></details>'''


def _range(f: Field) -> str:
    step = f.prop.get("step", "1" if f.prop["type"] == "integer" else "any")
    return f'''<div class="form-group schema-field">{_label(f)}
        <div class="range-row"><input type="range" min="{f.prop["minimum"]}" max="{f.prop["maximum"]}" step="{step}" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control-range" oninput="this.nextElementSibling.value = this.value">
        <output>{f.value}</output></div></div>'''


def _number(f: Field) -> str:
    step = "1" if f.prop["type"] == "integer" else "any"
    return f'''<div class="form-group schema-field">{_label(f)}
        <input type="number" step="{step}" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control"></div>'''


def _color(f: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(f)}
        <div class="color-row"><input type="color" id="{f.id}-picker" value="{f.value}" oninput="document.getElementById('{f.id}').value = this.value">
        <input type="text" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control" oninput="document.getElementById('{f.id}-picker').value = this.value"></div></div>'''


def _password(f: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(f)}
        <div class="pw-field"><input type="password" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control"><button type="button" class="pw-reveal" data-reveal="{f.id}" aria-controls="{f.id}" aria-pressed="false">Show</button></div></div>'''


def _textarea(f: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(f)}
        <textarea id="{f.id}" name="{f.name}" rows="3" class="form-control schema-textarea">{f.value}</textarea></div>'''


def _text(f: Field) -> str:
    return f'''<div class="form-group schema-field">{_label(f)}
        <input type="text" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control"></div>'''


def _items(prop: dict) -> dict:
    return prop.get("items", {})


# Which input a schema property gets: the first rule that matches (type, then format, then name)
WIDGET_RULES = [
    (lambda key, p: p.get("type") == "boolean", _toggle),
    (lambda key, p: bool(p.get("enum")), _select),
    (lambda key, p: p.get("type") == "array" and _items(p).get("type") == "string" and bool(_items(p).get("enum")), _checkboxes),
    (lambda key, p: p.get("type") == "array" and _items(p).get("type") == "object", _array),
    (lambda key, p: p.get("type") == "object", _object),
    (lambda key, p: p.get("type") in ("integer", "number") and "minimum" in p and "maximum" in p, _range),
    (lambda key, p: p.get("type") in ("integer", "number"), _number),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "color" or key == "color"), _color),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "password" or key in SECRET_KEYS), _password),
    (lambda key, p: p.get("type") == "string" and (p.get("format") == "textarea" or key in LONG_TEXT_KEYS), _textarea),
]


def _widget(key: str, prop: dict):
    return next((render for matches, render in WIDGET_RULES if matches(key, prop)), _text)


def render_schema_form(schema: dict, current_values: dict, name_prefix: str = "", module_name: str = "") -> str:
    """The form for a schema's properties: the core's standard fields first, then the module's own."""
    properties = schema.get("properties", {})
    keys = [k for k in STANDARD_FIELDS if k in properties] + [k for k in properties if k not in STANDARD_FIELDS]
    parts = []
    for key in keys:
        prop = properties[key]
        value = current_values.get(key)
        field = Field(key, prop, prop.get("default", "") if value is None else value,
                      _dom_id("field", name_prefix, key), f"{name_prefix}[{key}]" if name_prefix else key,
                      name_prefix, module_name)
        parts.append(_widget(key, prop)(field))
    return "\n".join(parts)


def _sub_swatches(f: Field) -> str:
    swatches = "\n".join(
        f'''<button type="button" class="color-swatch-btn{" active" if f.raw == value else ""}" style="background-color: {hex_};" title="{title}" onclick="selectSubFieldColor(this, '{f.id}', '{value}')"></button>'''
        for title, value, hex_ in SWATCHES)
    return f'''<div class="sub-form-group wide"><label for="{f.id}">{f.title}</label>
        <div class="swatch-row"><div class="swatches">{swatches}</div>
        <input type="text" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control form-control-sm swatch-value" oninput="updateSwatchSelection(this)"></div></div>'''


def _sub_icons(f: Field) -> str:
    icons = "\n".join(
        f'''<button type="button" class="icon-picker-btn{" active" if f.raw == icon else ""}" title="{icon}" onclick="selectSubFieldIcon(this, '{f.id}', '{icon}')"><i data-lucide="{icon}"></i></button>'''
        for icon in ICONS)
    return f'''<div class="sub-form-group wide"><label for="{f.id}">{f.title}</label>
        <div class="icon-picker"><div class="icon-grid">{icons}</div>
        <div class="icon-custom"><span>Custom Name:</span><input type="text" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control form-control-sm" oninput="updateIconSelection(this)"></div></div></div>'''


def _sub_select(f: Field) -> str:
    return f'''<div class="sub-form-group"><label for="{f.id}">{f.title}</label>
        <select id="{f.id}" name="{f.name}" class="form-control form-control-sm">{_options(f.prop, f.raw)}</select></div>'''


def _sub_text(f: Field) -> str:
    return f'''<div class="sub-form-group"><label for="{f.id}">{f.title}</label>
        <input type="text" id="{f.id}" name="{f.name}" value="{f.value}" class="form-control form-control-sm"></div>'''


def render_array_item(name_prefix: str, array_key: str, sub_properties: dict, index: int, item_val: dict, item_title: str) -> str:
    """One card in a list setting (e.g. a calendar), with move up/down and remove."""
    fields = []
    for key, prop in sub_properties.items():
        value = item_val.get(key)
        f = Field(key, prop, prop.get("default", "") if value is None else value,
                  _dom_id("field", name_prefix, array_key, index, key), f"{name_prefix}[{array_key}][{index}][{key}]",
                  name_prefix, "")
        render = {"color": _sub_swatches, "icon": _sub_icons}.get(key) or (_sub_select if prop.get("enum") else _sub_text)
        fields.append(render(f))
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


def _form_value(value):
    """"true"/"false" become booleans; a checkbox with its hidden "false" twin becomes one boolean;
    other repeated values a list without the empty hidden placeholder."""
    if isinstance(value, list):
        if all(isinstance(v, str) and v.lower() in ("true", "false") for v in value):
            return any(v.lower() == "true" for v in value)
        return [v for v in value if v != ""]
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def _child(container, token, next_token):
    """The container under `token`, created as a list when the next token is an index."""
    empty = [] if isinstance(next_token, int) else {}
    if isinstance(container, dict):
        if not isinstance(container.get(token), type(empty)):
            container[token] = empty
        return container[token]
    while len(container) <= token:
        container.append(None)
    if not isinstance(container[token], type(empty)):
        container[token] = empty
    return container[token]


def parse_flat_form_data(form_data: dict) -> dict:
    """Nest flat form keys: 'a', 'a[b]', 'a[0][b]', 'a[b][0][c]'."""
    result = {}
    for key, value in form_data.items():
        tokens = [int(p) if p.isdigit() else p for p in re.split(r"\[|\]", key) if p]
        if not tokens:
            continue
        node = result
        for token, next_token in zip(tokens, tokens[1:]):
            node = _child(node, token, next_token)
        last = tokens[-1]
        if isinstance(node, list):
            if not isinstance(last, int):
                continue  # 'a[0]' and 'a[0]x' clash: keep the list
            while len(node) <= last:
                node.append(None)
        node[last] = _form_value(value)
    return clean_nested_structures(result)


def clean_nested_structures(data):
    """Recursively removes None elements from lists, and cleans empty keys."""
    if isinstance(data, dict):
        return {k: clean_nested_structures(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [clean_nested_structures(item) for item in data if item is not None]
    return data


def _cast(value, prop: dict):
    """One form value as the type its schema property says."""
    kind = prop.get("type")
    if kind == "boolean":
        return value.lower() in ("true", "1", "yes", "on") if isinstance(value, str) else bool(value)
    if kind in ("integer", "number"):
        try:
            return int(value) if kind == "integer" else float(value)
        except (ValueError, TypeError):
            return value
    if kind == "array":
        items = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
        item_schema = prop.get("items", {})
        if item_schema.get("type") == "object":
            return [cast_values_by_schema(i, item_schema) if isinstance(i, dict) else i for i in items]
        return items
    if isinstance(value, dict):
        return cast_values_by_schema(value, prop)
    return value


def cast_values_by_schema(data: dict, schema: dict) -> dict:
    """Casts string values to their proper types (bool, int, float) based on the schema."""
    if not isinstance(data, dict) or not schema:
        return data
    properties = schema.get("properties", {})
    return {k: _cast(v, properties[k]) if k in properties else v for k, v in data.items()}
