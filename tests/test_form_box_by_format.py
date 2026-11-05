# SPDX-License-Identifier: AGPL-3.0-only
"""The type picks a setting's box, the format changes it, never the key's name (ARCHITECTURE #53)."""

from mirrordash_core.forms import render_array_item, render_schema_form


def test_the_type_picks_the_box_the_format_changes_it_never_the_name():
    schema = {"properties": {
        "token": {"type": "string", "title": "Token"},
        "text": {"type": "string", "title": "Text"},
        "color": {"type": "string", "title": "Colour"},
        "days": {"type": "integer", "title": "Days", "minimum": 1, "maximum": 7},
        "provider": {"type": "string", "title": "Provider", "enum": ["open_meteo", "smhi"]},
    }}
    html = render_schema_form(schema, {}, "modules[m]", "m")
    assert 'type="password"' not in html and "<textarea" not in html and 'type="color"' not in html
    assert 'type="range"' not in html and 'type="number"' in html  # minimum and maximum: rules, not a slider
    assert ">Open meteo</option>" in html and ">Smhi</option>" in html  # without enum_titles: readable, not capitals


def test_a_list_items_colour_and_icon_by_their_format():
    item = render_array_item("modules[m]", "calendars", {"color": {"type": "string", "format": "color"},
                                                          "icon": {"type": "string", "format": "icon"},
                                                          "name": {"type": "string"}}, 0, {}, "Calendar")
    assert item.count("color-swatch-btn") == 7 and "data-icon-input" in item
    plain = render_array_item("modules[m]", "calendars", {"color": {"type": "string"}}, 0, {}, "Calendar")
    assert "color-swatch-btn" not in plain
