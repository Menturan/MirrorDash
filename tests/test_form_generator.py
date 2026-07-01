import pytest
from mirrordash_core.api.form_generator import (
    render_schema_form,
    render_array_item,
    parse_flat_form_data,
    cast_values_by_schema
)

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
                "maximum": 100,
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
