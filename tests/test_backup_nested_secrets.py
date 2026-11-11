# SPDX-License-Identifier: AGPL-3.0-only
"""A plain backup leaves out a module's secrets inside a nested object and a list's rows too, and a restore
puts this mirror's own back only into the same row (features/backup/secret_settings.py)."""

from unittest.mock import MagicMock, patch

from mirrordash_core.features.backup import secret_settings

SCHEMA = {"properties": {
    "accounts": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "password": {"type": "string", "format": "password"}}}},
    "service": {"type": "object", "properties": {"key": {"type": "string", "format": "password"},
                                                 "url": {"type": "string"}}},
}}


def test_secrets_in_rows_and_nested_objects_stay_out_and_come_back_to_their_own_row():
    mirror = {"modules": {"m": {"module": "m",
                                "accounts": [{"name": "Anna", "password": "a1"}, {"name": "Bo", "password": "b2"}],
                                "service": {"key": "k3y", "url": "https://x"}}}}
    with patch("mirrordash_core.features.modules.module_files.find_entry_point", return_value=MagicMock()), \
         patch("mirrordash_core.features.modules.schema.get_module_schema", return_value=SCHEMA):
        plain = secret_settings.without_secrets(mirror)
        assert plain["modules"]["m"] == {"module": "m", "accounts": [{"name": "Anna"}, {"name": "Bo"}],
                                         "service": {"url": "https://x"}}
        restored = {"modules": {"m": {**plain["modules"]["m"], "accounts": [{"name": "Anna"}, {"name": "Cecilia"}]}}}
        secret_settings._keep_this_mirrors_secrets(restored, mirror)
    assert restored["modules"]["m"]["accounts"] == [{"name": "Anna", "password": "a1"}, {"name": "Cecilia"}]  # not Bo's
    assert restored["modules"]["m"]["service"] == {"key": "k3y", "url": "https://x"}
