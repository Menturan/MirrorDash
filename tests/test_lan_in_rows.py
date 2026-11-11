# SPDX-License-Identifier: AGPL-3.0-only
"""A module may reach the home addresses typed in a list's rows and a nested object too (lan.py)."""

from unittest.mock import patch

from mirrordash_core.features.modules import lan

SCHEMA = {"properties": {
    "url": {"type": "string", "format": "lan-url"},
    "printers": {"type": "array", "items": {"type": "object", "properties": {
        "address": {"type": "string", "format": "lan-url"}, "name": {"type": "string"}}}},
    "camera": {"type": "object", "properties": {"address": {"type": "string", "format": "lan-url"}}},
}}


def test_the_addresses_in_rows_and_nested_objects_count():
    modules = {"m": {"module": "m", "url": "http://ha.local:8123", "camera": {"address": "192.168.1.30"},
                     "printers": [{"address": "http://printer-1.lan", "name": "Hall"}, {"name": "No address"}, "junk"]}}
    with patch("mirrordash_core.features.modules.schema._installed_module_schemas", return_value={"m": SCHEMA}):
        assert lan.lan_hosts(modules) == {"m": {"ha.local", "printer-1.lan", "192.168.1.30"}}
