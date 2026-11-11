# SPDX-License-Identifier: AGPL-3.0-only
"""A list or search setting in the admin's form (ARCHITECTURE #48): how it is drawn, saved and checked, and
the route the form asks its choices from."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules.choice_field import MAX_QUERY, choice_request
from mirrordash_core.features.modules.schema import _check_module_settings
from mirrordash_core.forms import (
    cast_values_by_schema,
    render_array_item,
    render_schema_form,
)

ROW = {"provider": {"type": "string", "enum": ["mateo", "skolmaten"]},
       "school": {"type": "object", "format": "list", "title": "School"},
       "token": {"type": "string", "format": "password", "title": "Token"}}
SCHEMA = {"properties": {"schools": {"type": "array", "items": {"type": "object", "properties": ROW}},
                         "city": {"type": "object", "format": "search", "title": "Place"},
                         "token": {"type": "string", "format": "password"}}}


def test_a_list_and_a_search_are_drawn_with_the_saved_choice_full_width():
    html = render_schema_form(SCHEMA, {"city": {"id": "7", "name": "Gävle"}}, "modules[s]", "md_s")
    assert 'data-choice-field="search"' in html and 'role="combobox"' in html
    assert 'name="modules[s][city][id]" value="7"' in html and 'name="modules[s][city][name]" value="Gävle"' in html
    row = render_array_item("modules[s]", "schools", ROW, 0, {"school": {"id": "913", "name": "Fridhemsskolan"}}, "School")
    assert '<div class="form-group schema-field wide">' in row and 'data-choice-field="list"' in row
    assert '<option value="913" selected>Fridhemsskolan</option>' in row  # saved unchanged until the module answers
    assert '<option value="">Choose…</option>' in render_array_item("modules[s]", "schools", ROW, 1, {}, "School")


def test_a_password_in_a_list_item_is_hidden_with_a_show_button():
    row = render_array_item("modules[s]", "schools", ROW, 0, {"token": "abc"}, "School")
    assert '<input type="password"' in row and 'name="modules[s][schools][0][token]" value="abc"' in row
    assert 'class="pw-reveal"' in row and ">Show</button>" in row


def test_a_choice_is_saved_as_id_and_name_or_nothing_and_checked():
    form = {"city": {"id": "7", "name": "Gävle", "extra": "x"}, "schools": [{"school": {"id": "", "name": ""}}]}
    assert cast_values_by_schema(form, SCHEMA) == {"city": {"id": "7", "name": "Gävle"}, "schools": [{"school": {}}]}
    properties = SCHEMA["properties"]
    for good in ({"id": "7", "name": "Gävle"}, {}):
        _check_module_settings("s", {"city": good}, properties)
    for bad in ("Gävle", {"id": 7, "name": "Gävle"}, {"id": "7"}, {"id": "7", "name": "x" * 201}):
        with pytest.raises(ValueError, match="must be a choice"):
            _check_module_settings("s", {"city": bad}, properties)


def test_only_a_list_or_search_of_the_schema_is_asked_about():
    cfg = {"schools": [{"provider": "mateo", "school": {}}], "city": {}}
    assert choice_request("modules[s][schools][0][school]", SCHEMA, cfg, "q") == {
        "kind": "list", "key": "school", "item": {"provider": "mateo", "school": {}}, "query": "q"}
    asked = choice_request("modules[s][city]", SCHEMA, cfg, "x" * 500)
    assert asked["kind"] == "search" and asked["item"] is cfg and len(asked["query"]) == MAX_QUERY
    assert choice_request("modules[s][schools][5][school]", SCHEMA, cfg)["item"] == {}  # a row not there yet
    for bad in ("modules[s][token]", "modules[s][schools][0][provider]", "modules[s][nope]", "modules[s][city][id]",
                "modules[s][../x]", "", "city"):
        with pytest.raises(ValueError):
            choice_request(bad, SCHEMA, cfg)


AUTH = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
HEADERS = {"X-API-Key": "secret"}
URL = "/admin/panels/modules/choices/md_s?instance_id=s"
FORM = {"modules[s][enabled]": "true", "modules[s][position]": "top_left", "modules[s][token]": "t",
        "modules[s][schools][0][provider]": "mateo", "modules[s][schools][0][school][id]": "",
        "modules[s][schools][0][school][name]": ""}


@pytest.fixture
def client():
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value={"globals": {}, "modules": {}}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=SimpleNamespace(dist=None)), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA):
        yield TestClient(app)


def test_the_route_asks_the_module_with_the_unsaved_form(client):
    assert client.post(f"{URL}&field=modules[s][city]", data=FORM).status_code == 401
    answer = {"choices": [{"id": "913", "name": "Fridhemsskolan", "detail": "Gävle"}]}
    with patch("mirrordash_core.features.modules.routes.module_host.choices", new_callable=AsyncMock, return_value=answer) as ask:
        r = client.post(f"{URL}&field=modules[s][schools][0][school]&query=x", data=FORM, headers=HEADERS)
    assert r.status_code == 200 and r.json() == answer
    instance_id, module_cfg, request = ask.call_args.args
    assert instance_id == "s" and module_cfg["token"] == "t"  # what is typed, not what is saved
    assert request == {"kind": "list", "key": "school", "item": {"provider": "mateo", "school": {}}, "query": "x"}

    assert client.post(f"{URL}&field=modules[s][token]", data=FORM, headers=HEADERS).status_code == 400
    with patch("mirrordash_core.features.modules.routes.module_host.choices", new_callable=AsyncMock, side_effect=ConnectionError):
        r = client.post(f"{URL}&field=modules[s][city]", data=FORM, headers=HEADERS)
    assert r.json() == {"error": "The modules aren't running right now."}
    r = client.post(f"{URL}&field=modules[s][city]", data={**FORM, "modules[s][position]": "nowhere"}, headers=HEADERS)
    assert r.json()["error"].startswith("Fix the settings first:")
