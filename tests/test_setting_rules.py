# SPDX-License-Identifier: AGPL-3.0-only
"""The rules a module's schema sets on what the owner types (ARCHITECTURE #49): checked by the browser in the
form, and again when one instance's settings are saved."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.settings.setting_rules import check_setting_rules
from mirrordash_core.forms import render_array_item, render_schema_form

SCHEMA = {
    "required": ["url", "show_header", "school"],
    "properties": {
        "url": {"type": "string", "title": "Address", "pattern": "https?://.+", "pattern_message": "Starts with http:// or https://"},
        "switch_time": {"type": "string", "title": "Switch At", "pattern": "([01]\\d|2[0-3]):[0-5]\\d"},
        "days": {"type": "integer", "title": "Days", "format": "slider", "minimum": 1, "maximum": 10},
        "token": {"type": "string", "title": "Token", "format": "password", "minLength": 10, "maxLength": 40},
        "show_header": {"type": "boolean", "title": "Show Header"},
        "school": {"type": "object", "format": "list", "title": "School"},
        "calendars": {"type": "array", "title": "Calendars", "items": {
            "type": "object", "required": ["url"],
            "properties": {"name": {"type": "string", "title": "Name"}, "url": {"type": "string", "title": "URL"}}}},
    },
}
GOOD = {"url": "https://x", "switch_time": "13:00", "days": 3, "token": "0123456789", "show_header": False,
        "school": {"id": "913", "name": "Fridhemsskolan"}, "calendars": [{"name": "", "url": "https://c"}]}


@pytest.mark.parametrize("change, message", [
    ({"url": ""}, "Address is required."),
    ({"school": {}}, "School is required."),
    ({"url": "ftp://x"}, "Address Starts with http:// or https://."),
    ({"switch_time": "13:00 "}, "Switch At doesn't have the right format."),  # the whole value, as the browser
    ({"days": 0}, "Days must be at least 1."),
    ({"days": 11}, "Days must be at most 10."),
    ({"token": "short"}, "Token needs at least 10 characters."),
    ({"token": "x" * 41}, "Token takes at most 40 characters."),
    ({"calendars": [{"name": "Family", "url": ""}]}, "Calendars #1: URL is required."),
])
def test_a_broken_rule_is_named_for_the_owner(change, message):
    with pytest.raises(ValueError) as broken:
        check_setting_rules({**GOOD, **change}, SCHEMA)
    assert str(broken.value) == message


def test_empty_breaks_only_required_and_unsent_keys_are_left_alone():
    check_setting_rules(GOOD, SCHEMA)
    check_setting_rules({**GOOD, "switch_time": "", "token": ""}, SCHEMA)  # empty, not required: no pattern, no length
    check_setting_rules({"url": "https://x"}, SCHEMA)  # what the form didn't send: the module's default
    check_setting_rules({**GOOD, "show_header": False, "calendars": []}, SCHEMA)  # false and [] are values
    check_setting_rules({**GOOD, "url": "x"}, {"properties": {"url": {"type": "string", "pattern": "("}}})  # the module's bad pattern


def test_the_form_asks_the_browser_for_the_same_rules():
    html = render_schema_form(SCHEMA, GOOD, "modules[s]", "md_s")
    assert '<input required pattern="https?://.+" title="Starts with http:// or https://" type="text"' in html
    assert 'type="range" min="1" max="10"' in html  # a slider: never out of its bounds
    assert 'minlength="10" maxlength="40" type="password"' in html
    assert '<select required id=' in html  # the school list: "Choose…" is no choice
    row = render_array_item("modules[s]", "calendars", SCHEMA["properties"]["calendars"]["items"]["properties"], 0, {},
                            "Calendar", ["url"])
    assert row.count(" required ") == 1 and 'name="modules[s][calendars][0][url]"' in row


AUTH = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
HEADERS = {"X-API-Key": "secret"}
FORM = {"modules[s][enabled]": "true", "modules[s][position]": "top_left", "modules[s][url]": "ftp://x",
        "modules[s][show_header]": "true", "modules[s][school][id]": "913", "modules[s][school][name]": "F"}


def test_a_save_is_refused_but_the_preview_still_draws_a_form_being_filled_in():
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value={"globals": {}, "modules": {}}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=SimpleNamespace(dist=None)), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA), \
         patch("mirrordash_core.features.modules.routes.save_config") as save, \
         patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value="<p>card</p>"):
        client = TestClient(app)
        r = client.post("/admin/panels/modules/config/md_s/save?instance_id=s", data=FORM, headers=HEADERS)
        assert r.status_code == 400 and "Address Starts with http:// or https://." in r.text
        save.assert_not_called()
        r = client.post("/admin/panels/modules/preview/md_s?instance_id=s", data=FORM, headers=HEADERS)
        assert r.headers["X-Preview"] == "sample"
