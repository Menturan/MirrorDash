from mirrordash_core.forms import (
    cast_values_by_schema,
    parse_flat_form_data,
    render_array_item,
    render_schema_form,
)


def test_fields_with_a_group_share_one_section_saved_as_before():
    schema = {"properties": {
        "source": {"type": "string", "title": "Source"},
        "immich_url": {"type": "string", "title": "Immich Address", "group": "Immich"},
        "unsplash_key": {"type": "string", "format": "password", "title": "Unsplash Key", "group": "Unsplash"},
        "immich_key": {"type": "string", "format": "password", "title": "Immich Key", "group": "Immich"},
    }}
    html = render_schema_form(schema, {}, "modules[photos]", "photos")
    assert html.count('<details class="form-accordion">') == 2
    immich, unsplash = html.index("<summary>Immich</summary>"), html.index("<summary>Unsplash</summary>")
    # the section where its first field is, with all of its fields; the names as without a group
    assert html.index("Source") < immich < html.index("Immich Address") < html.index("Immich Key") < unsplash
    assert 'name="modules[photos][immich_key]"' in html


def test_show_if_hides_a_field_while_the_other_field_does_not_match():
    import re
    schema = {"properties": {
        "source": {"type": "string", "enum": ["uploaded", "immich"], "default": "uploaded", "title": "Source"},
        "immich_url": {"type": "string", "title": "Immich Address", "show_if": {"source": ["immich"]}},
        "order": {"type": "string", "title": "Order", "show_if": {"source": ["uploaded", "immich"], "nope": ["x"]}},
    }}

    def wrappers(values):
        html = render_schema_form(schema, values, "modules[photos]", "photos")
        return re.findall(r'<div class="show-if" data-show-if="([^"]*)"( hidden)?>', html), html
    (url, order), html = wrappers({})  # the default: uploaded
    assert url == ("{&#34;modules[photos][source]&#34;: [&#34;immich&#34;]}", " hidden")
    assert order[1] == ""  # an unknown field in show_if is left out, not hidden for good
    assert 'name="modules[photos][immich_url]"' in html  # hidden, still sent: its value stays saved
    (url, _), _ = wrappers({"source": "immich"})
    assert url[1] == ""


def test_show_if_follows_a_switch_saved_true_or_false():
    schema = {"properties": {
        "show_cover": {"type": "boolean", "title": "Show Cover", "default": True},
        "cover_style": {"type": "string", "title": "Cover", "show_if": {"show_cover": [True]}},
    }}
    def shown(values):
        html = render_schema_form(schema, values, "modules[spotify]", "spotify")
        return 'data-show-if="{&#34;modules[spotify][show_cover]&#34;: [&#34;true&#34;]}">' in html  # as show_if.js reads it
    assert shown({}) and shown({"show_cover": True}) and not shown({"show_cover": False})


def test_parse_flat_form_data():
    flat = {
        "globals[language]": "sv",
        "globals[latitude]": "59.3293",
        "globals[enabled]": ["false", "true"],
        "feeds[0][name]": "Work",
        "feeds[0][url]": "https://work.com",
        "feeds[1][name]": "Personal",
        "feeds[1][url]": "https://personal.com",
    }
    parsed = parse_flat_form_data(flat)
    assert parsed == {
        "globals": {
            "language": "sv",
            "latitude": "59.3293",
            "enabled": True
        },
        "feeds": [
            {"name": "Work", "url": "https://work.com"},
            {"name": "Personal", "url": "https://personal.com"}
        ]
    }

def test_cast_values_by_schema():
    schema = {
        "properties": {
            "language": {"type": "string"},
            "latitude": {"type": "number"},
            "enabled": {"type": "boolean"},
            "feeds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "count": {"type": "integer"}
                    }
                }
            }
        }
    }
    raw_data = {
        "language": "sv",
        "latitude": "59.3293",
        "enabled": "true",
        "feeds": [
            {"name": "Work", "count": "10"},
            {"name": "Personal", "count": "20"}
        ]
    }
    casted = cast_values_by_schema(raw_data, schema)
    assert casted == {
        "language": "sv",
        "latitude": 59.3293,
        "enabled": True,
        "feeds": [
            {"name": "Work", "count": 10},
            {"name": "Personal", "count": 20}
        ]
    }

def test_multiselect_string_array():
    schema = {
        "properties": {
            "builtin_feeds": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["bbc_news", "sr_ekot", "cnn_edition"]
                }
            }
        }
    }
    
    # 1. Render form
    html = render_schema_form(schema, {"builtin_feeds": ["bbc_news"]})
    assert 'name="builtin_feeds"' in html
    assert 'value="bbc_news"' in html
    assert 'checked' in html
    
    # 2. Parse submitted multi-value list
    flat_data = {
        "builtin_feeds": ["", "bbc_news", "sr_ekot"]
    }
    parsed = parse_flat_form_data(flat_data)
    assert parsed == {"builtin_feeds": ["bbc_news", "sr_ekot"]}
    
    # 3. Cast values
    casted = cast_values_by_schema(parsed, schema)
    assert casted == {"builtin_feeds": ["bbc_news", "sr_ekot"]}

    # 4. Empty resolution
    empty_flat = {
        "builtin_feeds": ""
    }
    parsed_empty = parse_flat_form_data(empty_flat)
    casted_empty = cast_values_by_schema(parsed_empty, schema)
    assert casted_empty == {"builtin_feeds": []}

def test_extra_form_controls():
    schema = {
        "properties": {
            "brightness": {
                "type": "integer",
                "minimum": 10,
                "maximum": 100, "format": "slider",
                "default": 85
            },
            "bg_color": {
                "type": "string",
                "format": "color",
                "default": "#000000"
            },
            "api_key": {
                "type": "string",
                "format": "password"
            },
            "notes": {
                "type": "string",
                "format": "textarea"
            }
        }
    }
    
    html = render_schema_form(schema, {
        "brightness": 50,
        "bg_color": "#ffffff",
        "api_key": "secret123",
        "notes": "my notes"
    })
    
    # Assert range slider
    assert 'type="range"' in html
    assert 'min="10"' in html
    assert 'max="100"' in html
    assert 'value="50"' in html
    
    # Assert color picker
    assert 'type="color"' in html
    assert 'value="#ffffff"' in html
    
    # Assert password field
    assert 'type="password"' in html
    assert 'value="secret123"' in html
    
    # Assert textarea
    assert '<textarea' in html
    assert 'my notes' in html

def test_dynamic_object_list_sorting():
    schema = {
        "properties": {
            "feeds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    }
                }
            }
        }
    }
    
    html = render_schema_form(schema, {
        "feeds": [{"name": "Feed 1"}]
    })
    
    # Assert move controls are present
    assert 'moveArrayItem(this, \'up\')' in html or "moveArrayItem(this, 'up')" in html
    assert 'moveArrayItem(this, \'down\')' in html or "moveArrayItem(this, 'down')" in html
    assert 'reindexArrayContainer' in html
    assert 'class="array-item-index-title"' in html

def test_render_array_item_enum_select():
    sub_properties = {
        "layout_style": {
            "type": "string",
            "enum": ["detailed", "compact"],
            "title": "Layout Style"
        }
    }
    
    html = render_array_item(
        name_prefix="test",
        array_key="entities",
        sub_properties=sub_properties,
        index=0,
        item_val={"layout_style": "compact"},
        item_title="Entity"
    )
    
    assert "<select" in html
    assert "option value=\"detailed\"" in html or "option value='detailed'" in html
    assert "option value=\"compact\" selected" in html or "option value='compact' selected" in html




def test_values_are_escaped_and_lists_nest():
    """A saved value can't break the form (a quote used to end the value attribute)."""
    from mirrordash_core.forms import parse_flat_form_data, render_schema_form
    html = render_schema_form({"properties": {"name": {"type": "string"}}}, {"name": 'say "hi" <b>'})
    assert 'value="say &#34;hi&#34; &lt;b&gt;"' in html
    parsed = parse_flat_form_data({"m[cal][0][url]": "a", "m[cal][1][url]": "b", "m[on]": ["false", "true"]})
    assert parsed == {"m": {"cal": [{"url": "a"}, {"url": "b"}], "on": True}}


def test_a_list_saved_in_an_older_shape_and_an_index_past_any_list():
    # A module whose list of strings became a list of objects: the old items aren't shown, the form still is
    feeds = {"type": "array", "items": {"type": "object", "properties": {"url": {"type": "string"}}}}
    form = render_schema_form({"properties": {"feeds": feeds}}, {"feeds": ["https://old.example/rss", {"url": "https://new.example"}]},
                              "modules[news]")
    assert "https://new.example" in form and "old.example" not in form
    # A posted index far past any list is left out, not filled up to with placeholders
    assert parse_flat_form_data({"a[100000000]": "x", "b[0]": "y", "c[²]": "z"}) == {"b": ["y"], "c": {"²": "z"}}


def test_render_array_item_checkboxes():
    """A list of choices in a list item: a titled box per choice, ticked as saved, no ids (a move renames them)."""
    from mirrordash_core.forms import cast_values_by_schema, parse_flat_form_data
    prop = {"type": "array", "title": "Extra readings",
            "items": {"type": "string", "enum": ["humidity", "linkquality"], "enum_titles": ["Humidity", "Signal"]}}
    html = render_array_item("m", "entities", {"readings": prop}, 0, {"readings": ["linkquality"]}, "Entity")
    assert 'type="checkbox" name="m[entities][0][readings]" value="humidity">' in html
    assert 'value="linkquality" checked' in html and "<span>Signal</span>" in html
    # None ticked saves as [], not missing: the module then shows none instead of its default
    parsed = parse_flat_form_data({"entities[0][readings]": ""})
    schema = {"properties": {"entities": {"type": "array", "items": {"type": "object", "properties": {"readings": prop}}}}}
    assert cast_values_by_schema(parsed, schema) == {"entities": [{"readings": []}]}
