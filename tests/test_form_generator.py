import re

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
    assert html.count("color-swatch-btn") == 7 and "swatch-value" in html  # the swatches and the value as text
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
    assert 'name="m[entities][0][readings]" value="humidity">' in html
    assert 'value="linkquality" checked' in html and "<span>Signal</span>" in html
    # None ticked saves as [], not missing: the module then shows none instead of its default
    parsed = parse_flat_form_data({"entities[0][readings]": ""})
    schema = {"properties": {"entities": {"type": "array", "items": {"type": "object", "properties": {"readings": prop}}}}}
    assert cast_values_by_schema(parsed, schema) == {"entities": [{"readings": []}]}


def test_a_field_with_help_gets_an_info_button_with_the_text_escaped_and_one_without_gets_none():
    schema = {"properties": {
        "a": {"type": "boolean", "title": "Has Help", "description": "Short.", "help": 'Long <b>"text"</b>'},
        "b": {"type": "string", "title": "No Help"}}}
    html = render_schema_form(schema, {}, "modules[x]", "x")
    assert html.count('class="help-btn"') == 1
    assert 'data-help="Long &lt;b&gt;&#34;text&#34;&lt;/b&gt;" data-help-title="Has Help"' in html
    assert 'aria-label="More about Has Help" aria-haspopup="dialog"' in html
    assert html.index("</label>") < html.index("help-btn")  # outside the label: a click never toggles the switch


def test_a_switch_with_copy_value_gets_a_shared_as_row_filled_from_its_sibling_and_hidden_while_off():
    schema = {"properties": {
        "entity_id": {"type": "string", "title": "Entity ID"},
        "share": {"type": "boolean", "title": "Share", "copy_value": "mod.{entity_id}"},
        "plain": {"type": "boolean", "title": "Plain"},
        "text": {"type": "string", "title": "Text", "copy_value": "ignored.{entity_id}"}}}
    on = render_schema_form(schema, {"entity_id": "sensor.a", "share": True}, "modules[x]", "x")
    assert on.count('class="copy-row"') == 1  # only a boolean gets one
    assert 'data-copy-value="mod.{entity_id}">' in on and ">mod.sensor.a</code>" in on
    assert 'aria-label="Copy mod.sensor.a"' in on
    assert on.index("slider round") < on.index("copy-row")
    off = render_schema_form(schema, {"entity_id": "", "share": False}, "modules[x]", "x")
    assert 'data-copy-value="mod.{entity_id}" hidden>' in off and ">mod.&lt;entity_id&gt;</code>" in off


def _unit_schema(**extra):
    return {"properties": {"interval": {"type": "integer", "title": "Interval", "default": 30, **extra}}}


def test_a_unit_is_drawn_after_a_number_and_a_slider_readout():
    html = render_schema_form(_unit_schema(unit="min"), {}, "modules[x]", "x")
    assert '<span class="unit">minutes</span>' in html and 'type="number"' in html and 'value="30"' in html
    slider = {"properties": {"size": {"type": "integer", "format": "slider", "minimum": 0, "maximum": 100, "unit": "%", "default": 100}}}
    assert ">100 %</output>" in render_schema_form(slider, {}, "modules[x]", "x")


def test_an_unknown_unit_is_not_drawn():
    for unit in ("days", "", None, ["s"]):
        assert "unit" not in render_schema_form(_unit_schema(unit=unit), {}, "modules[x]", "x").replace('data-unit=""', "")


def test_a_slider_says_its_min_label_at_the_minimum():
    schema = {"properties": {"width": {"type": "integer", "format": "slider", "minimum": 0, "maximum": 100, "default": 0,
                                       "min_label": "Auto", "unit": "%"}}}
    assert ">Auto</output>" in render_schema_form(schema, {}, "modules[x]", "x")
    html = render_schema_form(schema, {"width": 40}, "modules[x]", "x")
    assert ">40 %</output>" in html and 'data-min-label="Auto"' in html  # the oninput swaps it live


def test_show_if_not_hides_a_field_while_the_value_is_one_of_the_listed():
    schema = {"properties": {
        "group": {"type": "string", "default": "", "title": "Group"},
        "interval": {"type": "integer", "title": "Every", "show_if": {"group": {"not": [""]}}}}}

    def wrapper(values):
        import re
        return re.search(r'data-show-if="([^"]*)"( hidden)?>', render_schema_form(schema, values, "m", "x"))
    empty, named = wrapper({}), wrapper({"group": "kitchen"})
    assert empty[1] == "{&#34;m[group]&#34;: {&#34;not&#34;: [&#34;&#34;]}}" and empty[2] == " hidden"
    assert named[2] is None


def test_suggestions_become_a_datalist_on_that_text_field():
    schema = {"properties": {"carousel_group": {"type": "string", "title": "Group", "default": ""},
                             "other": {"type": "string", "title": "Other"}}}
    html = render_schema_form(schema, {}, "m", "x", suggestions={"carousel_group": ["a", "b<"]})
    assert html.count("<datalist") == 1 and '<option value="a">' in html and '<option value="b&lt;">' in html
    assert 'list="field-m-carousel-group-suggestions"' in html and 'id="field-m-carousel-group-suggestions"' in html
    assert "<datalist" not in render_schema_form(schema, {}, "m", "x")


def test_the_carousel_group_box_suggests_the_group_names_in_use(monkeypatch):
    from mirrordash_core.features.modules.routes import _standard_forms
    modules = {"clock": {"carousel_group": "top"}, "clock-2": {"carousel_group": "alpha"}, "news": {"carousel_group": "top"},
               "photos": {"carousel_group": ""}, "weather": {}}
    html = _standard_forms("clock", {}, modules["clock"], "modules[clock]", modules)["when_form"]
    assert html.index('<option value="alpha">') < html.index('<option value="top">') and html.count("<option value=") == 2


def test_fields_with_a_default_carry_it_for_the_reset_link():
    from mirrordash_core.forms import standard_schema_for
    schema = {"properties": {
        "mode": {"type": "string", "enum": ["a", "b"], "enum_titles": ["Alpha", "Beta"], "default": "b"},
        "on": {"type": "boolean", "default": True},
        "every": {"type": "integer", "default": 15, "unit": "s"},
        "nodefault": {"type": "string"},
        "key": {"type": "string", "format": "password", "default": ""},
        "rows": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "string", "default": "x"}}}, "default": []}}}
    html = render_schema_form(schema, {}, "m", "x")
    assert 'data-default="&#34;b&#34;" data-default-text="Beta"' in html
    assert 'data-default="true" data-default-text="on"' in html
    assert 'data-default="15" data-default-text="15 seconds"' in html
    assert html.count("data-default=") == 3  # none for no default, a secret, a list or a row's field
    # a module's own default for a standard field is the one the link goes back to
    standard = standard_schema_for({"properties": {"opacity_percent": {"default": 40}, "show_header": {"default": False}}})
    opacity = render_schema_form({"properties": {"opacity_percent": standard["properties"]["opacity_percent"]}}, {}, "m", "x")
    assert 'data-default="40" data-default-text="40 %"' in opacity
    header = render_schema_form({"properties": {"show_header": standard["properties"]["show_header"]}}, {}, "m", "x")
    assert 'data-default="false"' in header


def test_the_add_button_says_add_and_the_lowercased_items_title():
    def button(items):
        schema = {"properties": {"things": {"type": "array", "title": "Things", "items": {"type": "object", "properties": {}, **items}}}}
        return render_schema_form(schema, {}, "m", "x")
    assert "</i> Add entity</button>" in button({"title": "Entity"})
    assert "</i> Add Item</button>" in button({})


def test_a_list_row_header_is_its_first_choice_or_text_and_says_when_it_is_off():
    props = {"theme": {"type": "string", "enum": ["christmas", "easter"], "enum_titles": ["Christmas", "Easter egg"]},
             "enabled": {"type": "boolean"}, "label": {"type": "string"}}
    row = lambda item, properties=props: render_array_item("m", "themes", properties, 0, item, "Theme")
    header = lambda html: re.search(r'array-item-index-title"[^>]*>([^<]*)<', html)[1]
    assert header(row({"theme": "christmas", "enabled": True})) == "Christmas"
    assert header(row({"theme": "easter", "enabled": False})) == "Easter egg \u00b7 off"
    assert header(row({"label": " Mine ", "enabled": False})) == "Mine \u00b7 off"  # no choice: the first text
    assert header(row({"theme": "easter", "label": "Mine"})) == "Easter egg"  # the choice first
    assert header(row({"enabled": False})) == "# 1: Theme"  # nothing to say
    assert header(row({"theme": "x"}, {"theme": {"type": "string", "enum": ["x"]}})) == "X"  # no enum_titles


def test_the_position_offers_no_reset():
    """Going back to the default position would move the card to the middle: no reset link for it."""
    from mirrordash_core.forms import STANDARD_SCHEMA, render_schema_form
    html = render_schema_form({"properties": {"position": STANDARD_SCHEMA["properties"]["position"]}},
                              {"position": "top_right"}, "modules[x]", "clock")
    assert "data-default" not in html
