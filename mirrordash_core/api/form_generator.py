# Licensed under the PolyForm Noncommercial License 1.0.0.

import logging
import re

logger = logging.getLogger("mirrordash.core.api.form_generator")

# Fields owned entirely by the core — injected automatically for every module.
# Module developers must NOT redeclare these in their config_schema.
STANDARD_FIELDS = [
    "enabled",
    "position",
    "carousel_group",
    "carousel_interval",
    "max_width",
    "max_height",
    "z_index",
    "opacity",
]


def cast_standard_fields(module_cfg: dict) -> dict:
    """Coerce standard core-owned fields to their correct Python types.

    Because standard fields are NOT in the module's own config_schema, they
    bypass cast_values_by_schema.  This function must be called explicitly on
    the parsed form data after schema-based casting.
    """
    result = dict(module_cfg)
    if "enabled" in result:
        v = result["enabled"]
        if isinstance(v, str):
            result["enabled"] = v.lower() in ("true", "1", "yes")
        else:
            result["enabled"] = bool(v)
    for key in ("carousel_interval",):
        if key in result:
            try:
                result[key] = int(result[key])
            except (ValueError, TypeError):
                pass
    for key in ("opacity",):
        if key in result:
            try:
                result[key] = float(result[key])
            except (ValueError, TypeError):
                pass
    for key in ("z_index",):
        if key in result:
            try:
                result[key] = int(result[key])
            except (ValueError, TypeError):
                pass
    # max_width, max_height, position, carousel_group stay as strings.
    return result

def render_schema_form(schema: dict, current_values: dict, name_prefix: str = "") -> str:
    properties = schema.get("properties", {})
    if not properties:
        return ""
        
    html_parts = []

    # Order: standard fields first (in defined order), then module-specific fields.
    ordered_keys = [k for k in STANDARD_FIELDS if k in properties]
    for k in properties:
        if k not in ordered_keys:
            ordered_keys.append(k)
            
    for key in ordered_keys:
        prop = properties[key]
        val = current_values.get(key)
        if val is None:
            val = prop.get("default", "")
            
        field_id = f"field-{name_prefix}-{key}".replace("[", "-").replace("]", "-").replace("_", "-")
        name = f"{name_prefix}[{key}]" if name_prefix else key
        
        prop_type = prop.get("type")
        title = prop.get("title", key)
        description = prop.get("description", "")
        enum_list = prop.get("enum")
        
        if prop_type == "boolean":
            checked = "checked" if val else ""
            html_parts.append(f"""
                <div class="form-group toggle-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <label class="switch">
                        <input type="hidden" name="{name}" value="false">
                        <input type="checkbox" id="{field_id}" name="{name}" value="true" {checked}>
                        <span class="slider round"></span>
                    </label>
                </div>
            """)
        elif enum_list:
            options_html = []
            for opt in enum_list:
                selected = "selected" if val == opt else ""
                display_opt = opt.replace("_", " ").upper()
                options_html.append(f'<option value="{opt}" {selected}>{display_opt}</option>')
            options_str = "\n".join(options_html)
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <select id="{field_id}" name="{name}" class="form-control">
                        {options_str}
                    </select>
                </div>
            """)
        elif prop_type == "array" and prop.get("items", {}).get("type") == "string" and prop.get("items", {}).get("enum"):
            enum_list = prop.get("items", {}).get("enum")
            selected_vals = val if isinstance(val, list) else []
            
            checkboxes_html = []
            for opt in enum_list:
                checked = "checked" if opt in selected_vals else ""
                display_opt = opt.replace("_", " ").upper()
                field_opt_id = f"{field_id}-{opt}".replace("[", "-").replace("]", "-")
                checkboxes_html.append(f"""
                    <label class="checkbox-inline" style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px; color: white; cursor: pointer;">
                        <input type="checkbox" id="{field_opt_id}" name="{name}" value="{opt}" {checked} style="cursor: pointer;">
                        <span>{display_opt}</span>
                    </label>
                """)
            checkboxes_str = "\n".join(checkboxes_html)
            
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px; border-left: 2px solid #52525b; padding-left: 12px; margin-top: 10px; margin-bottom: 10px;">
                    <div class="form-label-desc" style="margin-bottom: 8px;">
                        <label style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0; color: #a1a1aa;">{description}</p>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 4px;">
                        <input type="hidden" name="{name}" value="">
                        {checkboxes_str}
                    </div>
                </div>
            """)
        elif prop_type == "array" and prop.get("items", {}).get("type") == "object":
            items = val if isinstance(val, list) else []
            sub_properties = prop.get("items", {}).get("properties", {})
            item_title = prop.get("items", {}).get("title", "Item")
            
            items_html_parts = []
            for idx, item in enumerate(items):
                item_html = render_array_item(
                    name_prefix=name_prefix,
                    array_key=key,
                    sub_properties=sub_properties,
                    index=idx,
                    item_val=item,
                    item_title=item_title
                )
                items_html_parts.append(item_html)
                
            items_str = "\n".join(items_html_parts)
            container_id = f"array-container-{key}"
            
            html_parts.append(f"""
                <div class="form-group" style="border-left: 2px solid #52525b; padding-left: 12px; margin-top: 15px; margin-bottom: 15px;">
                    <div class="form-label-desc" style="margin-bottom: 10px;">
                        <label style="font-weight: 600; font-size: 0.95rem; color: white;">{title}</label>
                        <p class="field-description" style="font-size: 0.75rem; color: #a1a1aa; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <div class="array-items-container" id="{container_id}">
                        {items_str}
                    </div>
                    <button type="button" class="btn secondary btn-sm"
                            hx-get="/admin/panels/config/add-array-item"
                            hx-vals='js:{{index: document.querySelectorAll("#{container_id} .array-item-card").length, name_prefix: "{name_prefix}", array_key: "{key}", item_title: "{item_title}"}}'
                            hx-target="#{container_id}"
                            hx-swap="beforeend">
                        <i class="fas fa-plus"></i> Add {item_title}
                    </button>
                </div>
            """)
        elif prop_type == "object":
            sub_properties = prop.get("properties", {})
            sub_val = val if isinstance(val, dict) else {}
            sub_form_html = render_schema_form(
                {"properties": sub_properties},
                sub_val,
                name
            )
            html_parts.append(f"""
                <details class="form-accordion" style="margin-bottom: 16px; border: 1px solid #27272a; border-radius: 6px; background: rgba(255,255,255,0.01); overflow: hidden;">
                    <summary style="padding: 12px; font-weight: 600; color: white; cursor: pointer; user-select: none; background: rgba(255,255,255,0.03); outline: none;">
                        {title}
                    </summary>
                    <div style="padding: 12px; border-top: 1px solid #27272a;">
                        <p class="field-description" style="font-size:0.75rem; margin: 0 0 12px 0; color: #a1a1aa; line-height: 1.4;">{description}</p>
                        {sub_form_html}
                    </div>
                </details>
            """)
        elif prop_type in ("integer", "number") and "minimum" in prop and "maximum" in prop:
            step = prop.get("step", "1" if prop_type == "integer" else "any")
            min_val = prop["minimum"]
            max_val = prop["maximum"]
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0; color: #a1a1aa;">{description}</p>
                    </div>
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <input type="range" min="{min_val}" max="{max_val}" step="{step}" id="{field_id}" name="{name}" value="{val}" class="form-control-range" style="flex: 1; accent-color: white;" oninput="this.nextElementSibling.value = this.value">
                        <output style="font-family: monospace; font-size: 13px; min-width: 28px; text-align: right; color: white;">{val}</output>
                    </div>
                </div>
            """)
        elif prop_type in ("integer", "number"):
            step = "1" if prop_type == "integer" else "any"
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <input type="number" step="{step}" id="{field_id}" name="{name}" value="{val}" class="form-control">
                </div>
            """)
        elif prop_type == "string" and (prop.get("format") == "color" or key == "color"):
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <input type="color" id="{field_id}-picker" value="{val}" style="border: none; background: none; width: 36px; height: 36px; cursor: pointer; padding: 0; flex-shrink: 0;" oninput="document.getElementById('{field_id}').value = this.value">
                        <input type="text" id="{field_id}" name="{name}" value="{val}" class="form-control" style="flex: 1;" oninput="document.getElementById('{field_id}-picker').value = this.value">
                    </div>
                </div>
            """)
        elif prop_type == "string" and (prop.get("format") == "password" or key in ("api_key", "password", "token", "secret")):
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <input type="password" id="{field_id}" name="{name}" value="{val}" class="form-control">
                </div>
            """)
        elif prop_type == "string" and (prop.get("format") == "textarea" or key in ("description", "text", "message", "preamble")):
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <textarea id="{field_id}" name="{name}" rows="3" class="form-control" style="resize: vertical; background: #09090b; border: 1px solid #27272a; color: white; padding: 8px; border-radius: 4px; width: 100%;">{val}</textarea>
                </div>
            """)
        else:
            html_parts.append(f"""
                <div class="form-group" style="margin-bottom: 12px;">
                    <div class="form-label-desc">
                        <label for="{field_id}" style="font-weight:600; color: white;">{title}</label>
                        <p class="field-description" style="font-size:0.75rem; margin: 2px 0 0 0;">{description}</p>
                    </div>
                    <input type="text" id="{field_id}" name="{name}" value="{val}" class="form-control">
                </div>
            """)
            
    return "\n".join(html_parts)


def render_array_item(name_prefix: str, array_key: str, sub_properties: dict, index: int, item_val: dict, item_title: str) -> str:
    sub_fields_html = []
    
    for sub_key, sub_prop in sub_properties.items():
        sub_val = item_val.get(sub_key)
        if sub_val is None:
            sub_val = sub_prop.get("default", "")
            
        name = f"{name_prefix}[{array_key}][{index}][{sub_key}]"
        field_id = f"field-{name_prefix}-{array_key}-{index}-{sub_key}".replace("[", "-").replace("]", "-").replace("_", "-")
        
        sub_title = sub_prop.get("title", sub_key)
        
        if sub_key == "color":
            colors = [
                {"name": "White", "value": "#ffffff", "hex": "#ffffff"},
                {"name": "Ice Blue", "value": "var(--color-ice-blue)", "hex": "#cceeff"},
                {"name": "Rose Pink", "value": "var(--color-rose-pink)", "hex": "#ffccd5"},
                {"name": "Green", "value": "var(--color-status-online)", "hex": "#a0ffba"},
                {"name": "Red", "value": "var(--color-status-warning)", "hex": "#f87171"},
                {"name": "Gray", "value": "var(--color-standard-gray)", "hex": "#999999"},
                {"name": "Charcoal", "value": "var(--color-dimmed-charcoal)", "hex": "#666666"}
            ]
            swatches_html = []
            for col in colors:
                is_selected = sub_val == col["value"]
                active_class = "active" if is_selected else ""
                border = "2px solid white" if is_selected else "1px solid #52525b"
                transform = "scale(1.15)" if is_selected else "none"
                box_shadow = "0 0 8px white" if is_selected else "none"
                
                swatches_html.append(f"""
                    <button type="button" 
                            class="color-swatch-btn {active_class}" 
                            style="width: 20px; height: 20px; border-radius: 50%; border: {border}; background-color: {col['hex']}; cursor: pointer; outline: none; transition: transform 0.1s; transform: {transform}; box-shadow: {box_shadow};" 
                            title="{col['name']}" 
                            onclick="selectSubFieldColor(this, '{field_id}', '{col['value']}')">
                    </button>
                """)
            swatches_str = "\n".join(swatches_html)
            
            sub_fields_html.append(f"""
                <div class="sub-form-group" style="margin-bottom: 8px; grid-column: span 2;">
                    <label for="{field_id}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 6px;">{sub_title}</label>
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <div style="display: flex; gap: 6px; flex-wrap: wrap; align-items: center;">
                            {swatches_str}
                        </div>
                        <input type="text" id="{field_id}" name="{name}" value="{sub_val}" class="form-control form-control-sm" style="font-size: 0.8rem; padding: 2px 6px; width: 120px; background: #09090b; border: 1px solid #27272a; color: white;" oninput="updateSwatchSelection(this)">
                    </div>
                </div>
            """)
        elif sub_key == "icon":
            icons = [
                "calendar", "clock", "users", "briefcase", "home", "heart", "gift", "trophy", 
                "music", "plane", "shopping-cart", "utensils", "alert-circle", "book-open", "coffee", "film"
            ]
            icons_grid_html = []
            for ic in icons:
                is_selected = sub_val == ic
                active_class = "active" if is_selected else ""
                bg = "#3f3f46" if is_selected else "#09090b"
                border = "1px solid white" if is_selected else "1px solid #27272a"
                color = "white" if is_selected else "#a1a1aa"
                
                icons_grid_html.append(f"""
                    <button type="button" 
                            class="icon-picker-btn {active_class}" 
                            style="width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; background: {bg}; border: {border}; border-radius: 4px; color: {color}; cursor: pointer; outline: none; transition: background 0.1s;" 
                            title="{ic}" 
                            onclick="selectSubFieldIcon(this, '{field_id}', '{ic}')">
                        <i data-lucide="{ic}" style="width: 14px; height: 14px; stroke-width: 2px;"></i>
                    </button>
                """)
            icons_grid_str = "\n".join(icons_grid_html)
            
            sub_fields_html.append(f"""
                <div class="sub-form-group" style="margin-bottom: 8px; grid-column: span 2;">
                    <label for="{field_id}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 6px;">{sub_title}</label>
                    <div style="display: flex; align-items: flex-start; gap: 10px; flex-direction: column;">
                        <div style="display: grid; grid-template-columns: repeat(8, 1fr); gap: 6px; width: 100%;">
                            {icons_grid_str}
                        </div>
                        <div style="display: flex; align-items: center; gap: 8px; width: 100%;">
                            <span style="font-size: 0.75rem; color: #71717a;">Custom Name:</span>
                            <input type="text" id="{field_id}" name="{name}" value="{sub_val}" class="form-control form-control-sm" style="font-size: 0.8rem; padding: 2px 6px; flex-grow: 1; background: #09090b; border: 1px solid #27272a; color: white;" oninput="updateIconSelection(this)">
                        </div>
                    </div>
                </div>
            """)
        else:
            sub_fields_html.append(f"""
                <div class="sub-form-group" style="margin-bottom: 8px;">
                    <label for="{field_id}" style="font-size: 0.8rem; color: #a1a1aa; display: block; margin-bottom: 4px;">{sub_title}</label>
                    <input type="text" id="{field_id}" name="{name}" value="{sub_val}" class="form-control form-control-sm" style="font-size: 0.85rem; padding: 4px 8px; background: #09090b; border: 1px solid #27272a; color: white;">
                </div>
            """)
            
    sub_fields_str = "\n".join(sub_fields_html)
    
    return f"""
        <div class="array-item-card" style="border: 1px solid #3f3f46; border-radius: 6px; padding: 12px; margin-bottom: 12px; position: relative; background: #18181b;">
            <button type="button" class="btn danger btn-sm" style="position: absolute; top: 8px; right: 8px; padding: 2px 6px; font-size: 0.75rem;" onclick="this.closest('.array-item-card').remove(); triggerLucide();">
                <i class="fas fa-trash"></i>
            </button>
            <div style="font-size: 0.8rem; font-weight: 600; color: #e4e4e7; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em;"># {index + 1}: {item_title}</div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px;">
                {sub_fields_str}
            </div>
        </div>
    """


def parse_flat_form_data(form_data: dict) -> dict:
    """Parses flat dictionary from form fields into a nested dict structure.
    Handles keys like:
      - 'simple_key'
      - 'dict_key[sub_key]'
      - 'array_key[0][sub_key]'
      - 'nested[sub][0][sub_sub]'
    """
    result = {}
    
    # Pre-process form_data to handle lists (like checkboxes with hidden fallbacks)
    processed_data = {}
    for k, v in form_data.items():
        if isinstance(v, list):
            # If all items are boolean strings, resolve as single boolean
            if all(isinstance(item, str) and item.lower() in ("true", "false") for item in v):
                processed_data[k] = any(item.lower() == "true" for item in v)
            else:
                # Keep as a list, filtering out empty strings if any
                processed_data[k] = [item for item in v if item != ""]
        else:
            if isinstance(v, str) and v.lower() == "true":
                processed_data[k] = True
            elif isinstance(v, str) and v.lower() == "false":
                processed_data[k] = False
            else:
                processed_data[k] = v

    for key, value in processed_data.items():
        tokens = []
        parts = re.split(r'\[|\]', key)
        parts = [p for p in parts if p != '']
        for p in parts:
            if p.isdigit():
                tokens.append(int(p))
            else:
                tokens.append(p)
                
        if not tokens:
            continue
            
        curr = result
        for i, token in enumerate(tokens[:-1]):
            nxt_token = tokens[i+1]
            if isinstance(nxt_token, int):
                if isinstance(curr, dict):
                    if token not in curr:
                        curr[token] = []
                    lst = curr[token]
                else:
                    while len(curr) <= token:
                        curr.append([])
                    lst = curr[token]
                
                while len(lst) <= nxt_token:
                    lst.append({})
            else:
                if isinstance(curr, dict):
                    if token not in curr:
                        curr[token] = {}
                else:
                    while len(curr) <= token:
                        curr.append({})
                    if not isinstance(curr[token], dict):
                        curr[token] = {}
            
            curr = curr[token]
                
        leaf_token = tokens[-1]
        if isinstance(curr, list) and isinstance(leaf_token, int):
            while len(curr) <= leaf_token:
                curr.append(None)
            curr[leaf_token] = value
        elif isinstance(curr, dict):
            curr[leaf_token] = value
            
    return clean_nested_structures(result)


def clean_nested_structures(data):
    """Recursively removes None elements from lists, and cleans empty keys."""
    if isinstance(data, dict):
        return {k: clean_nested_structures(v) for k, v in data.items() if v is not None}
    elif isinstance(data, list):
        return [clean_nested_structures(item) for item in data if item is not None]
    return data


def cast_values_by_schema(data: dict, schema: dict) -> dict:
    """Casts string values to their proper types (bool, int, float) based on the schema."""
    if not isinstance(data, dict) or not schema:
        return data
        
    properties = schema.get("properties", {})
    casted = {}
    
    for k, v in data.items():
        prop_schema = properties.get(k)
        if not prop_schema:
            casted[k] = v
            continue
            
        prop_type = prop_schema.get("type")
        
        if prop_type == "boolean":
            if isinstance(v, str):
                casted[k] = v.lower() in ("true", "1", "yes", "on")
            else:
                casted[k] = bool(v)
        elif prop_type == "integer":
            try:
                casted[k] = int(v)
            except (ValueError, TypeError):
                casted[k] = v
        elif prop_type == "number":
            try:
                casted[k] = float(v)
            except (ValueError, TypeError):
                casted[k] = v
        elif prop_type == "array":
            items_schema = prop_schema.get("items", {})
            val_list = v if isinstance(v, list) else ([v] if v not in (None, "") else [])
            if items_schema.get("type") == "object":
                casted_list = []
                for item in val_list:
                    if isinstance(item, dict):
                        casted_list.append(cast_values_by_schema(item, items_schema))
                    else:
                        casted_list.append(item)
                casted[k] = casted_list
            else:
                casted[k] = val_list
        elif isinstance(v, dict):
            casted[k] = cast_values_by_schema(v, prop_schema)
        else:
            casted[k] = v
            
    return casted
