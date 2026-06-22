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
