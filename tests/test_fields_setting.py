# SPDX-License-Identifier: AGPL-3.0-only
"""A fields setting (ARCHITECTURE #63): the boxes a module builds. How its answer is cleaned, how the boxes are
drawn, how the value is cast and pruned on save, what is kept when the module can't answer, and the route."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules import choices
from mirrordash_core.features.modules.choice_field import choice_request
from mirrordash_core.features.modules.defaults import with_defaults
from mirrordash_core.features.modules.fields_schema import (
    MAX_FIELDS,
    MAX_TEXT,
    clean_fields,
)
from mirrordash_core.features.modules.schema import _check_module_settings
from mirrordash_core.forms import (
    cast_values_by_schema,
    parse_flat_form_data,
    render_array_item,
    render_fields,
    render_schema_form,
)

ROW = {"entity_id": {"type": "string", "title": "Entity"},
       "extras": {"type": "object", "format": "fields", "title": "Extra Readings", "description": "Ticked are shown."}}
SCHEMA = {"properties": {"entities": {"type": "array", "items": {"type": "object", "properties": ROW}},
                         "extras": {"type": "object", "format": "fields", "title": "Top"}}}
SHOWN = {"type": "array", "title": "Show", "default": ["a", "b"], "items": {"type": "string", "enum": ["a", "b", "c"]}}


def test_a_returned_schema_keeps_only_what_the_form_can_draw_and_read_back():
    answer = clean_fields({
        "shown": SHOWN, "on": {"type": "boolean"}, "n": {"type": "integer", "format": "slider", "minimum": 0, "maximum": 5},
        "c": {"type": "string", "format": "color"}, "pw": {"type": "string", "format": "password"},
        "pick": {"type": "string", "enum": ["x", "y"], "show_if": {"on": [True]}, "group": "G"},
        # each of these is rejected
        "obj": {"type": "object", "properties": {}}, "rows": {"type": "array", "items": {"type": "object"}},
        "nums": {"type": "array", "items": {"type": "integer", "enum": [1]}}, "free": {"type": "array", "items": {"type": "string"}},
        "none": {"type": "array", "items": {"type": "string", "enum": []}}, "null": {"type": "null"}, "notype": {"title": "x"},
        "l": {"type": "object", "format": "list"}, "s": {"type": "string", "format": "search"},
        "f": {"type": "string", "format": "files"}, "nested": {"type": "object", "format": "fields"},
        "lan": {"type": "string", "format": "lan-url"}, "a b": {"type": "string"}, "../x": {"type": "string"},
        "__types": {"type": "string"}, "__saved": {"type": "string"}, "text": "not a dict"})
    assert list(answer) == ["shown", "on", "n", "c", "pw", "pick"]
    assert answer["shown"] == SHOWN and answer["shown"] is not SHOWN  # a copy


def test_the_answer_is_cut_and_limited():
    long = "x" * 500
    answer = clean_fields({"t": {"type": "string", "title": long, "description": long, "default": long,
                                 "enum": [long], "enum_titles": [long]}})
    assert all(len(v) == MAX_TEXT for v in (answer["t"]["title"], answer["t"]["description"], answer["t"]["default"],
                                            answer["t"]["enum"][0], answer["t"]["enum_titles"][0]))
    assert len(clean_fields({f"k{i}": {"type": "string"} for i in range(MAX_FIELDS + 20)})) == MAX_FIELDS
    assert clean_fields({}) == {}
    with pytest.raises(TypeError):
        clean_fields([{"type": "string"}])


def test_the_boxes_are_the_forms_own_named_under_the_setting_with_defaults_only_for_a_missing_value():
    fields = {"shown": SHOWN, "loud": {"type": "boolean", "default": True}, "note": {"type": "string", "default": "hi"}}
    html = render_fields(fields, {"shown": ["c"], "loud": False}, "modules[s][entities][0][extras]")
    assert 'name="modules[s][entities][0][extras][shown]" value="c" checked' in html
    assert 'value="a" checked' not in html and 'value="b" checked' not in html  # a saved [c] beats the default
    assert 'name="modules[s][entities][0][extras][note]" value="hi"' in html  # no saved value: the default
    assert 'value="true" checked' not in html  # a saved False beats the default True
    assert '__types]" value="{&#34;shown&#34;: &#34;array&#34;, &#34;loud&#34;: &#34;boolean&#34;, &#34;note&#34;: &#34;string&#34;}"' in html
    assert "Nothing to set for this one." in render_fields({}, {}, "modules[s][extras]")
    assert 'name="modules[s][extras][__types]" value="{}"' in render_fields({}, {}, "modules[s][extras]")


def test_the_setting_itself_waits_loading_and_carries_what_was_saved_in_its_row():
    row = render_array_item("modules[s]", "entities", ROW, 0, {"extras": {"shown": ["a"]}}, "Entity")
    assert 'name="modules[s][entities][0][extras][__saved]" value="{&#34;shown&#34;: [&#34;a&#34;]}"' in row
    assert "Loading…" in row and 'class="form-group schema-field wide"' in row and "Extra Readings" in row
    new = render_array_item("modules[s]", "entities", ROW, 1, {}, "Entity")
    assert 'extras][__saved]" value="{}"' in new  # a new row has nothing saved
    assert 'extras][__saved]" value="{}"' in render_schema_form(SCHEMA, {}, "modules[s]", "md_s")


def cast(flat):
    return cast_values_by_schema(parse_flat_form_data(flat)["modules"]["s"], SCHEMA)


def test_the_save_keeps_only_the_boxes_the_module_gave_last_typed_by_their_schema():
    box = "modules[s][extras]"
    types = '{"shown": "array", "loud": "boolean", "n": "integer", "ratio": "number", "note": "string"}'
    flat = {f"{box}[__saved]": '{"old": "x"}', f"{box}[__types]": types, f"{box}[shown]": ["", "a", "c"],
            f"{box}[loud]": ["false", "true"], f"{box}[n]": "5", f"{box}[ratio]": "0.5", f"{box}[note]": "hi",
            f"{box}[stale]": "x"}  # a box that is no longer given (not in __types) is dropped
    assert cast(flat)["extras"] == {"shown": ["a", "c"], "loud": True, "n": 5, "ratio": 0.5, "note": "hi"}
    flat.update({f"{box}[shown]": "", f"{box}[loud]": "false", f"{box}[n]": "x"})  # nothing ticked, a bad number
    assert cast(flat)["extras"] == {"shown": [], "loud": False, "n": "x", "ratio": 0.5, "note": "hi"}
    for key in ("shown", "loud", "n", "ratio", "note"):  # a box missing from the form is not invented
        assert key not in cast({f"{box}[__types]": types})["extras"]


def test_a_module_that_didnt_answer_keeps_the_saved_value_and_no_boxes_means_empty():
    row = "modules[s][entities][%d][extras]"
    flat = {"modules[s][entities][0][entity_id]": "a", f"{row % 0}[__saved]": '{"shown": ["a"], "loud": true}',
            "modules[s][entities][1][entity_id]": "b", f"{row % 1}[__saved]": "{}",
            "modules[s][entities][2][entity_id]": "c", f"{row % 2}[__saved]": '{"shown": ["a"]}',
            f"{row % 2}[__types]": "{}"}  # the module answered {}: nothing to keep
    rows = cast(flat)["entities"]
    assert [r["extras"] for r in rows] == [{"shown": ["a"], "loud": True}, {}, {}]
    for broken in ("not json", "[1]", "", None):
        assert cast({f"{row % 0}[__saved]": broken or ""})["entities"][0]["extras"] == {}


def test_moving_a_row_moves_what_was_saved_with_it():
    flat = {"modules[s][entities][0][entity_id]": "b", "modules[s][entities][0][extras][__saved]": '{"shown": ["b"]}',
            "modules[s][entities][1][entity_id]": "a", "modules[s][entities][1][extras][__saved]": '{"shown": ["a"]}'}
    assert [(r["entity_id"], r["extras"]["shown"]) for r in cast(flat)["entities"]] == [("b", ["b"]), ("a", ["a"])]


def test_a_saved_fields_value_is_checked_as_an_object_of_plain_values():
    props = SCHEMA["properties"]
    for good in ({}, {"shown": ["a"], "n": 1, "x": 0.5, "on": True, "t": "x"}):
        _check_module_settings("s", {"extras": good}, props)
    for bad in ("x", [], {"o": {"a": 1}}, {"l": [1]}, {"a b": "x"}, {"__x": 1}, {f"k{i}": 1 for i in range(101)}):
        with pytest.raises(ValueError, match="plain values"):
            _check_module_settings("s", {"extras": bad}, props)
    assert with_defaults({}, SCHEMA)["extras"] == {}


def test_only_a_fields_setting_of_the_schema_is_asked_for_its_boxes_without_itself_in_the_row():
    cfg = {"entities": [{"entity_id": "x", "extras": {"shown": ["a"]}}], "extras": {"old": 1}, "other": 2}
    ask = choice_request("modules[s][entities][0][extras]", SCHEMA, cfg)
    assert ask == {"kind": "fields", "key": "extras", "item": {"entity_id": "x"}, "query": ""}
    assert choice_request("modules[s][extras]", SCHEMA, cfg)["item"] == {"entities": cfg["entities"], "other": 2}
    for bad in ("modules[s][entities][0][entity_id]", "modules[s][extras][shown]", "modules[s][nope]"):
        with pytest.raises(ValueError):
            choice_request(bad, SCHEMA, cfg)


class Readings:
    def __init__(self, config):
        self.config = config

    async def settings_fields_extras(self, item):
        if not item.get("entity_id"):
            raise ValueError("Enter the entity ID first")
        if item["entity_id"] == "crash":
            raise KeyError("x")
        if item["entity_id"] == "wrong":
            return ["not", "a", "dict"]
        return {"shown": SHOWN, "bad": {"type": "object"}} if item["entity_id"] != "none" else {}

    def settings_fields_sync(self, item):
        return {}


def ask(request):
    with patch.object(choices, "find_entry_point", return_value=SimpleNamespace(load=lambda: Readings, dist=None)), \
         patch.object(choices, "package_dir", return_value=None):
        return asyncio.run(choices.setting_choices("s", {"module": "s"}, {}, SimpleNamespace(awake=asyncio.Event()), request))


def test_the_module_host_asks_the_method_and_cleans_the_answer(caplog):
    assert ask({"kind": "fields", "key": "extras", "item": {"entity_id": "x"}}) == {"fields": {"shown": SHOWN}}
    assert ask({"kind": "fields", "key": "extras", "item": {"entity_id": "none"}}) == {"fields": {}}
    assert ask({"kind": "fields", "key": "extras", "item": {}}) == {"error": "Enter the entity ID first"}
    assert ask({"kind": "fields", "key": "extras", "item": {"entity_id": "crash"}}) == {"error": "Couldn't load the fields (see Logs)."}
    assert "KeyError" in caplog.text
    assert ask({"kind": "fields", "key": "extras", "item": {"entity_id": "wrong"}}) == {"error": "Couldn't load the fields (see Logs)."}
    assert "no async settings_fields_sync()" in ask({"kind": "fields", "key": "sync", "item": {}})["error"]


AUTH = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
HEADERS = {"X-API-Key": "secret"}
URL = "/admin/panels/modules/fields/md_s?instance_id=s"
ROW_FORM = {"modules[s][enabled]": "true", "modules[s][position]": "top_left",
            "modules[s][entities][0][entity_id]": "x",
            "modules[s][entities][0][extras][__saved]": '{"shown": ["c"]}',
            "modules[s][entities][0][extras][__types]": '{"shown": "array"}',
            "modules[s][entities][0][extras][shown]": ["", "b"]}
FIELD = "modules[s][entities][0][extras]"


@pytest.fixture
def client():
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value={"globals": {}, "modules": {}}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=SimpleNamespace(dist=None)), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA):
        yield TestClient(app)


def test_the_route_answers_html_drawn_with_what_the_form_holds_or_the_modules_error_text(client):
    assert client.post(f"{URL}&field={FIELD}", data=ROW_FORM).status_code == 401
    answer = {"fields": {"shown": SHOWN, "note": {"type": "string", "default": "hi"}}}
    with patch("mirrordash_core.features.modules.routes.module_host.choices", new_callable=AsyncMock, return_value=answer) as asked:
        r = client.post(f"{URL}&field={FIELD}", data=ROW_FORM, headers=HEADERS)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert f'name="{FIELD}[shown]" value="b" checked' in r.text and 'value="c" checked' not in r.text  # the unsaved ticks
    assert f'name="{FIELD}[note]" value="hi"' in r.text and f'name="{FIELD}[__types]"' in r.text
    assert asked.call_args.args[2] == {"kind": "fields", "key": "extras", "item": {"entity_id": "x"}, "query": ""}

    with patch("mirrordash_core.features.modules.routes.module_host.choices", new_callable=AsyncMock,
               return_value={"error": "Enter the entity ID first"}):
        r = client.post(f"{URL}&field={FIELD}", data=ROW_FORM, headers=HEADERS)
    assert (r.status_code, r.text) == (422, "Enter the entity ID first")
    with patch("mirrordash_core.features.modules.routes.module_host.choices", new_callable=AsyncMock, side_effect=ConnectionError):
        assert client.post(f"{URL}&field={FIELD}", data=ROW_FORM, headers=HEADERS).status_code == 422
    assert client.post(f"{URL}&field=modules[s][entities][0][entity_id]", data=ROW_FORM, headers=HEADERS).status_code == 400
    r = client.post(f"{URL}&field={FIELD}", data={**ROW_FORM, "modules[s][position]": "nowhere"}, headers=HEADERS)
    assert r.status_code == 422 and r.text.startswith("Fix the settings first:")


def test_the_preview_and_the_choices_route_take_a_form_with_a_fields_setting(client):
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value="<p>card</p>") as preview:
        r = client.post("/admin/panels/modules/preview/md_s?instance_id=s", data=ROW_FORM, headers=HEADERS)
    assert r.status_code == 200 and r.headers["X-Preview"] == "sample"
    assert preview.call_args.args[1]["entities"][0]["extras"] == {"shown": ["b"]}  # pruned and typed as Save would
