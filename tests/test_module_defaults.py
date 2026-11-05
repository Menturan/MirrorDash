# SPDX-License-Identifier: AGPL-3.0-only
"""A module gets its schema's defaults for the settings never saved (features/modules/defaults.py)."""

from mirrordash_core.features.modules.defaults import with_defaults

SCHEMA = {"properties": {
    "city": {"type": "string", "default": "Stockholm"},
    "days": {"type": "integer", "default": 3},
    "feeds": {"type": "array", "default": ["bbc"]},
    "url": {"type": "string", "format": "lan-url", "default": "http://homeassistant.local:8123"},
    "school": {"type": "object", "format": "list"},
    "no_default": {"type": "string"},
    "snow": {"type": "object", "properties": {"drift": {"type": "boolean", "default": True},
                                              "speed": {"type": "integer", "default": 100}}},
}}


def test_the_missing_settings_get_their_defaults_the_saved_ones_stay():
    filled = with_defaults({"city": "Gävle", "snow": {"speed": 50}, "module": "weather"}, SCHEMA)
    assert filled == {"city": "Gävle", "days": 3, "feeds": ["bbc"], "snow": {"drift": True, "speed": 50},
                      "module": "weather", "url": "", "school": {}}  # the lan-url's hint is no address; none picked
    assert "no_default" not in filled
    filled["feeds"].append("cnn")
    assert SCHEMA["properties"]["feeds"]["default"] == ["bbc"]  # a copy: the schema's own list untouched
    assert with_defaults({"days": 0}, SCHEMA)["days"] == 0  # a saved falsy value is a value
