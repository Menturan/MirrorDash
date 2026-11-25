# SPDX-License-Identifier: AGPL-3.0-only
"""Shared module settings (ARCHITECTURE #62): the merge, the Shared Settings section, saving an instance with "Own value" on and off, the restart of every
instance when a shared value changes, the check of config["module_settings"], the drawer's form, the plain
backup and the uninstall."""
import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.backup import secret_settings
from mirrordash_core.features.modules import client as client_module
from mirrordash_core.features.modules import lan, schema, service
from mirrordash_core.features.modules.defaults import with_defaults
from mirrordash_core.features.modules.shared_settings import (
    is_shareable,
    merged_instances,
    shared_block,
    shared_keys,
    with_shared,
)

AUTH = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
HEADERS = {"X-API-Key": "secret"}
SCHEMA = {"title": "Home Assistant", "properties": {
    "url": {"type": "string", "format": "lan-url", "title": "Address", "shared": True, "default": "http://ha.local:8123"},
    "token": {"type": "string", "format": "password", "title": "Token", "shared": True},
    "entity": {"type": "string", "title": "Entity", "default": "sun.sun"},
    "unit": {"type": "string", "enum": ["C", "F"], "shared": True, "default": "C"},
    "rows": {"type": "array", "items": {"type": "object"}, "shared": True},  # not allowed: ignored
    "position": {"type": "string", "shared": True},  # a standard field: ignored
}}
CONFIG = {"module_settings": {"mirrordash-ha": {"url": "http://192.168.1.5:8123", "token": "shared-token"}},
          "modules": {"one": {"module": "mirrordash-ha"},
                      "two": {"module": "mirrordash-ha", "token": "own-token"}}}


def test_an_instance_gets_its_own_value_else_the_shared_one_else_the_default():
    merged = with_defaults(with_shared(CONFIG["modules"]["two"], CONFIG, SCHEMA), SCHEMA)
    assert merged["token"] == "own-token"  # own
    assert merged["url"] == "http://192.168.1.5:8123"  # shared
    assert merged["unit"] == "C" and merged["entity"] == "sun.sun"  # the schema's default
    assert with_shared(CONFIG["modules"]["one"], CONFIG, SCHEMA)["token"] == "shared-token"
    assert "token" not in CONFIG["modules"]["one"]  # a copy
    assert with_shared({"module": "other"}, CONFIG, SCHEMA) == {"module": "other"}  # another module's
    assert with_shared({"module": "mirrordash-ha"}, CONFIG, None) == {"module": "mirrordash-ha"}  # no schema


def test_an_own_empty_value_still_overrides():
    assert with_shared({"module": "mirrordash-ha", "token": ""}, CONFIG, SCHEMA)["token"] == ""


def test_only_single_value_settings_of_the_top_level_can_be_shared():
    assert shared_keys(SCHEMA) == ["url", "token", "unit"]
    for prop in ({"type": "array"}, {"type": "object"}, {"format": "files"}, {"type": "object", "format": "list"},
                 {"type": "object", "format": "search"}, {}):
        assert not is_shareable("x", {**prop, "shared": True})
    assert not is_shareable("x", {"type": "string", "shared": "yes"}) and not is_shareable("x", {"type": "string"})
    for kind in ("string", "integer", "number", "boolean"):
        assert is_shareable("x", {"type": kind, "shared": True})


def test_merged_instances_covers_every_instance():
    with patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_ha": SCHEMA}):
        merged = merged_instances(CONFIG)
    assert merged["one"]["token"] == "shared-token" and merged["two"]["token"] == "own-token"


def test_the_lan_allow_list_follows_a_shared_address():
    with patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_ha": SCHEMA}):
        assert lan.lan_hosts(merged_instances(CONFIG)) == {"one": {"192.168.1.5"}, "two": {"192.168.1.5"}}
        assert lan.lan_hosts(CONFIG["modules"]) == {}  # as it was: only what an instance holds


# The save route

@pytest.fixture
def saving():
    """The save route with a module whose schema is SCHEMA; yields (client, the saved config, the reload mock)."""
    config = {"globals": {}, "module_settings": {}, "modules": {"one": {"module": "mirrordash-ha"}}}
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_ha": SCHEMA}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=MagicMock()), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA), \
         patch("mirrordash_core.features.modules.routes.load_config", side_effect=lambda: __import__("copy").deepcopy(config)), \
         patch("mirrordash_core.features.modules.routes.save_config", side_effect=lambda saved: config.update(saved)), \
         patch("mirrordash_core.features.modules.routes.module_host.reload_modules", new_callable=AsyncMock) as reload:
        yield TestClient(app), config, reload


def post_save(client, instance, own=(), shared=None, **values):
    """One Save: the instance's fields, its Own value switches and the shared fields, in one request."""
    values = {"entity": "sun.sun", **values}  # a real form always sends the module's own fields
    form = {f"modules[{instance}][{key}]": value for key, value in values.items()}
    form |= {f"shared_own[{key}]": "true" for key in own}
    form |= {f"shared[{key}]": value for key, value in (shared or {}).items()}
    return client.post(f"/admin/panels/modules/config/mirrordash-ha/save?instance_id={instance}", data=form, headers=HEADERS)


def save(client, instance, own=(), shared=None, **values):
    response = post_save(client, instance, own, shared, **values)
    assert response.status_code == 200, response.text


def test_saving_without_a_shared_part_leaves_the_shared_values_and_drops_a_shared_field_without_own_value(saving):
    client, config, reload = saving
    shared = {"url": "http://192.168.1.5:8123", "token": "shared-token"}
    config["module_settings"] = {"mirrordash-ha": dict(shared)}
    config["modules"]["one"]["token"] = "an-old-own-one"
    save(client, "one", url="http://192.168.1.9:8123", token="typed", entity="light.x")
    assert config["module_settings"]["mirrordash-ha"] == shared  # not touched
    assert config["modules"]["one"] == {"module": "mirrordash-ha", "entity": "light.x", "enabled": True}  # own off: the key is gone
    reload.assert_awaited_once()


def test_saving_with_own_value_on_keeps_it_in_the_instance(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"url": "http://192.168.1.5:8123", "token": "shared-token"}}
    save(client, "one", own=["token"], url="http://192.168.1.5:8123", token="mine")
    assert config["modules"]["one"]["token"] == "mine" and "url" not in config["modules"]["one"]
    assert config["module_settings"]["mirrordash-ha"] == {"url": "http://192.168.1.5:8123", "token": "shared-token"}


def test_a_new_instance_without_own_value_adds_no_key_of_its_own(saving):
    client, config, _ = saving
    save(client, "two", url="http://192.168.1.5:8123", token="t")
    assert config["modules"]["two"] == {"module": "mirrordash-ha", "entity": "sun.sun", "enabled": True}
    assert config["module_settings"] == {}


def test_the_rules_check_what_the_module_gets():
    from mirrordash_core.features.modules.setting_rules import check_setting_rules
    rules = {"required": ["url"], "properties": {"url": {**SCHEMA["properties"]["url"], "pattern": "https?://.+"}}}
    instance = {"module": "mirrordash-ha"}
    with pytest.raises(ValueError, match="Address doesn't have the right format"):
        check_setting_rules(with_shared(instance, {"module_settings": {"mirrordash-ha": {"url": "ftp://x"}}}, rules), rules)
    check_setting_rules(with_shared(instance, {"module_settings": {"mirrordash-ha": {"url": "http://x"}}}, rules), rules)


# One Save: the shared fields ride with the instance

def test_one_save_writes_the_shared_values_and_the_instance_and_reloads_once(saving):
    client, config, reload = saving
    config["modules"]["two"] = {"module": "mirrordash-ha", "token": "own"}
    response = post_save(client, "one", shared={"url": "http://192.168.1.9:8123", "token": "new-shared", "unit": "F", "entity": "ignored"},
                         entity="light.x")
    assert response.status_code == 200 and "Settings saved." in response.headers["HX-Trigger-After-Swap"]
    assert config["module_settings"]["mirrordash-ha"] == {"url": "http://192.168.1.9:8123", "token": "new-shared", "unit": "F"}
    assert config["modules"]["one"] == {"module": "mirrordash-ha", "entity": "light.x", "enabled": True}
    assert config["modules"]["two"]["token"] == "own"  # an own value is not touched
    reload.assert_awaited_once()


def test_adding_a_new_instance_saves_the_shared_fields_too(saving):
    client, config, _ = saving
    save(client, "two", shared={"url": "http://192.168.1.9:8123"}, entity="x")
    assert config["module_settings"]["mirrordash-ha"] == {"url": "http://192.168.1.9:8123"}
    assert config["modules"]["two"] == {"module": "mirrordash-ha", "entity": "x", "enabled": True}


def test_a_secret_left_empty_keeps_the_saved_one_and_a_stale_key_is_dropped(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"token": "keep-me", "entity": "stale"}}
    save(client, "one", shared={"url": "http://192.168.1.9:8123", "token": ""})
    assert config["module_settings"]["mirrordash-ha"] == {"token": "keep-me", "url": "http://192.168.1.9:8123"}


def test_an_invalid_shared_value_saves_nothing(saving):
    client, config, reload = saving
    response = post_save(client, "one", shared={"unit": "K"})
    assert response.status_code == 400 and "must be one of" in response.json()["detail"]
    assert config["module_settings"] == {}
    reload.assert_not_awaited()


def test_a_broken_shared_rule_is_named_by_the_shared_input_and_saves_nothing(saving):
    client, config, reload = saving
    rules = {**SCHEMA, "required": ["url"], "properties": {**SCHEMA["properties"], "url": {**SCHEMA["properties"]["url"], "pattern": "https?://.+"}}}
    with patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=rules):
        response = post_save(client, "one", shared={"url": ""})
        assert response.status_code == 422 and response.json()["fields"] == {"shared[url]": "Address is required."}
        response = post_save(client, "one", own=["url"], url="ftp://x", shared={"url": "http://ok"})
        assert response.json()["fields"] == {"modules[one][url]": "Address doesn't have the right format."}  # the own value counts
    assert config["module_settings"] == {}
    reload.assert_not_awaited()


def test_a_required_shared_field_nothing_is_saved_for_is_named_a_saved_secret_is_not(saving):
    client, config, reload = saving
    rules = {**SCHEMA, "required": ["token"]}
    with patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=rules):
        response = post_save(client, "one", shared={"url": "http://ha", "token": ""})
        assert response.status_code == 422 and response.json()["fields"] == {"shared[token]": "Token is required."}
        assert config["module_settings"] == {}
        reload.assert_not_awaited()
        config["module_settings"] = {"mirrordash-ha": {"token": "keep-me"}}
        assert post_save(client, "one", shared={"url": "http://ha", "token": ""}).status_code == 200
    assert config["module_settings"]["mirrordash-ha"]["token"] == "keep-me"


def test_the_shared_block_marks_only_the_required_fields_required():
    html = shared_block({}, "mirrordash-ha", {**SCHEMA, "required": ["token"]}, 1)["html"]
    assert re.search(r'<input[^>]*required[^>]*name="shared\[token\]"', html)
    assert not re.search(r'<input[^>]*required[^>]*name="shared\[url\]"', html)


def test_the_shared_section_has_no_route_of_its_own(saving):
    client, config, _ = saving
    assert client.post("/admin/panels/modules/shared/mirrordash-ha", data={"shared[token]": "t"}, headers=HEADERS).status_code == 404
    assert config["module_settings"] == {}


def test_preview_and_choices_get_the_shared_value(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"token": "shared-token"}}
    form = {"modules[one][entity]": "x"}
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "shared-token"
    form["modules[one][token]"] = "typed"  # a field without Own value is ignored: the shared value stays
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "shared-token"
    form |= {"shared_own[token]": "true", "modules[one][token]": "mine"}  # an own value shows before it is saved
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "mine"
    form = {"modules[one][entity]": "x", "shared[token]": "typed-shared"}  # an unsaved shared value shows too
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "typed-shared"
    assert config["module_settings"] == {"mirrordash-ha": {"token": "shared-token"}}  # but nothing is saved


# Every instance restarts when a shared value changes

def test_every_instance_of_the_module_restarts_when_a_shared_value_changes(monkeypatch):
    config = {"globals": {}, "module_settings": {"mirrordash-ha": {"token": "a"}},
              "modules": {"one": {"module": "mirrordash-ha"}, "two": {"module": "mirrordash-ha", "token": "own"},
                          "three": {"module": "mirrordash-ha"}, "clock": {"module": "mirrordash-clock"}}}
    monkeypatch.setattr(client_module, "load_config", lambda: config)
    monkeypatch.setattr(client_module, "module_entry_points", lambda: [])
    monkeypatch.setattr(client_module, "scripts_allowed", lambda ep, config: False)
    monkeypatch.setattr(client_module, "find_entry_point", lambda name, eps=None: None)
    monkeypatch.setattr(schema, "_installed_module_schemas", lambda: {"mirrordash_ha": SCHEMA})
    monkeypatch.setattr(client_module, "manager", MagicMock(broadcast=AsyncMock()))
    host = client_module.ModuleHost()
    host._control = object()
    host._request = AsyncMock(side_effect=lambda message, timeout: {"uids": {i: 70002 + n for n, i in enumerate(message["instances"])}})
    host._apply_lan = AsyncMock()
    host.set_hidden = AsyncMock()
    started = []
    host._start = AsyncMock(side_effect=lambda instance_id, cfg, globals_cfg, awake: started.append(instance_id))
    monkeypatch.setattr(client_module, "MODULE_UIDS", range(70000, 70100))
    stopped = []
    monkeypatch.setattr(host, "_stop", lambda instance_id: (stopped.append(instance_id), host._instances.pop(instance_id)))

    async def sync_twice():
        await host._sync()
        # what they run with now
        for instance_id in started:
            host._instances[instance_id] = SimpleNamespace(
                html=None, key=client_module.settings_key(_wanted(config, instance_id), config["globals"]))
        started.clear()
        config["module_settings"]["mirrordash-ha"]["token"] = "b"
        await host._sync()

    def _wanted(config, instance_id):
        cfg = config["modules"][instance_id]
        return with_defaults(with_shared(cfg, config, SCHEMA if cfg["module"] == "mirrordash-ha" else {}),
                             SCHEMA if cfg["module"] == "mirrordash-ha" else {})

    with patch("mirrordash_core.features.power.display_power.display_power_manager", MagicMock(awake=MagicMock(is_set=lambda: True))), \
         patch("mirrordash_core.features.power.privacy.hidden_instances", return_value=set()):
        asyncio.run(sync_twice())
    assert sorted(stopped) == ["one", "three"]  # "two" has its own token; the clock isn't this module's
    assert sorted(started) == ["one", "three"]


# validate_config

def test_validate_config_checks_the_shape_and_types_of_module_settings():
    with patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_ha": SCHEMA}):
        schema.validate_config({"modules": {}, "module_settings": {"mirrordash-ha": {"token": "t", "unit": "F"}}})
        schema.validate_config({"modules": {}})  # none at all
        for bad, message in [([], "JSON object of objects"), ({"mirrordash-ha": "x"}, "JSON object of objects"),
                             ({"mirrordash-ha": {"token": 3}}, "'Token' must be a string"),
                             ({"mirrordash-ha": {"unit": "K"}}, "must be one of")]:
            with pytest.raises(ValueError, match=message):
                schema.validate_config({"modules": {}, "module_settings": bad})


# The form

@pytest.fixture
def drawer():
    config = {"globals": {}, "module_settings": {"mirrordash-ha": {"url": "http://192.168.1.5:8123", "token": "s3cret-shared"}},
              "modules": {"one": {"module": "mirrordash-ha"}, "two": {"module": "mirrordash-ha", "token": "own-secret",
                                                                      "entity": "x"}}}
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=MagicMock()), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value=config):
        client = TestClient(app)
        yield lambda instance: client.get(f"/admin/panels/modules/config/mirrordash-ha?instance_id={instance}", headers=HEADERS).text


def row_of(html, key):
    """The shared row under a field: (its HTML, the switch's tag)."""
    row = html.split(f'name="shared_own[{key}]"')[0].rsplit('<div class="shared-row"', 1)[1]
    return row + html.split(f'name="shared_own[{key}]"')[1].split("</label>")[0], \
        html.split(f'name="shared_own[{key}]"')[1].split(">")[0]


def box_of(html, key):
    """The fieldset holding a shared field's box (its opening tag and what follows up to its end)."""
    return html.split(f'name="shared_own[{key}]"')[1].split("<fieldset", 1)[1].split("</fieldset>")[0]


def test_a_shared_field_without_own_value_shows_a_line_and_the_switch_and_a_hidden_box(drawer):
    html = drawer("one")
    row, switch = row_of(html, "url")
    assert "Shared: set in Shared settings above." in row and "fa-link" in row and "Own value" in row
    assert " checked" not in switch  # Own value is off
    assert '<p class="field-description shared-note shared-note-own" hidden>' in row
    box = box_of(html, "url")
    assert box.startswith(' class="shared-box" disabled hidden>') and 'name="modules[one][url]"' in box
    assert 's3cret-shared' not in html  # a secret isn't printed
    assert 'name="shared_own[entity]"' not in html and 'name="shared_own[rows]"' not in html  # not shared


def test_own_value_on_shows_the_box_with_the_instances_own_value(drawer):
    html = drawer("two")
    row, switch = row_of(html, "token")
    assert " checked" in switch and "Only this instance. The shared value stays as it is." in row and "fa-user" in row
    box = box_of(html, "token")
    assert box.startswith(' class="shared-box">') and 'value="own-secret"' in box
    assert " checked" not in row_of(html, "url")[1]


def test_a_secret_is_never_printed_in_a_note_or_a_data_attribute(drawer):
    for instance in ("one", "two"):
        row = row_of(drawer(instance), "token")[0]
        assert "s3cret-shared" not in row and 'data-shared-value=""' in row
    note = row_of(drawer("two"), "unit")[0].split('shared-note-own"')[1].split("</p>")[0]
    assert "The shared value is <span" in note  # a plain setting names its shared value


def test_a_new_instance_starts_with_own_value_off(drawer):
    html = drawer("three")
    row, switch = row_of(html, "token")
    assert "Shared: set in Shared settings above." in row and " checked" not in switch
    assert box_of(html, "token").startswith(' class="shared-box" disabled hidden>')


# The shared block: its summary and whether it is open

REQUIRED = {**SCHEMA, "required": ["url", "token"]}


def test_the_summary_is_the_first_plain_value_as_a_host_and_the_number_of_instances():
    config = {"module_settings": {"mirrordash-ha": {"url": "http://192.168.1.5:8123/path", "token": "s3cret"}}}
    block = shared_block(config, "mirrordash-ha", SCHEMA, 2)
    assert block["summary"] == "192.168.1.5:8123 \u00b7 used by 2" and "s3cret" not in block["html"]
    assert 'name="shared[url]"' in block["html"] and 'name="shared[entity]"' not in block["html"]
    assert shared_block({}, "mirrordash-ha", SCHEMA, 1)["summary"] == "C \u00b7 used by 1"  # the first value shown: a default
    only_secret = {"properties": {"token": SCHEMA["properties"]["token"]}}
    assert shared_block({"module_settings": {"mirrordash-ha": {"token": "t"}}}, "mirrordash-ha", only_secret, 0)["summary"] == "Not set \u00b7 used by 0"
    assert shared_block(config, "mirrordash-ha", {"properties": {"entity": {"type": "string"}}}, 1) is None  # nothing shared


def test_the_block_is_open_while_a_required_shared_field_is_empty():
    saved = {"module_settings": {"mirrordash-ha": {"url": "http://ha", "token": "t"}}}
    assert shared_block(saved, "mirrordash-ha", REQUIRED, 1)["open"] is False
    assert shared_block({}, "mirrordash-ha", REQUIRED, 1)["open"] is True  # nothing saved yet
    no_secret = {"module_settings": {"mirrordash-ha": {"url": "http://ha"}}}
    assert shared_block(no_secret, "mirrordash-ha", REQUIRED, 1)["open"] is True  # the saved secret counts, not the printed ""
    assert shared_block({"module_settings": {"mirrordash-ha": {"url": ""}}}, "mirrordash-ha", {**SCHEMA, "required": ["url"]}, 1)["open"] is True
    assert shared_block({}, "mirrordash-ha", SCHEMA, 1)["open"] is False  # none required
    assert shared_block({}, "mirrordash-ha", {**SCHEMA, "required": ["unit"]}, 1)["open"] is False  # its default is a value


def test_the_sheet_gets_the_shared_block_and_the_chips(drawer):
    html = drawer("one")
    assert "192.168.1.5:8123 \u00b7 used by 2" in html and "Shared settings" in html and "<details" in html
    assert 'name="shared[url]" value="http://192.168.1.5:8123"' in html and "s3cret-shared" not in html


# The plain backup and the uninstall

def test_a_plain_backup_leaves_out_a_shared_secret_and_a_restore_gets_this_mirrors_back():
    mirror = {"module_settings": {"mirrordash-ha": {"url": "http://ha", "token": "shared-token"}},
              "modules": {"one": {"module": "mirrordash-ha", "token": "own-token"}}}
    with patch("mirrordash_core.features.modules.module_files.find_entry_point", return_value=MagicMock()), \
         patch("mirrordash_core.features.modules.schema.get_module_schema", return_value=SCHEMA):
        plain = secret_settings.without_secrets(mirror)
        assert plain["module_settings"] == {"mirrordash-ha": {"url": "http://ha"}}
        assert plain["modules"]["one"] == {"module": "mirrordash-ha"}
        assert mirror["module_settings"]["mirrordash-ha"]["token"] == "shared-token"  # the mirror's own isn't touched
        restored = {"module_settings": {"mirrordash-ha": {"url": "http://ha"}}, "modules": {"one": {"module": "mirrordash-ha"}}}
        secret_settings._keep_this_mirrors_secrets(restored, mirror)
    assert restored["module_settings"]["mirrordash-ha"] == {"url": "http://ha", "token": "shared-token"}
    assert restored["modules"]["one"]["token"] == "own-token"


def test_uninstalling_a_module_removes_its_shared_settings():
    config = {"module_settings": {"mirrordash-ha": {"token": "t"}, "mirrordash-clock": {"x": 1}},
              "modules": {"one": {"module": "mirrordash-ha"}}}
    with patch.object(service, "load_config", return_value=config), patch.object(service, "save_config") as save, \
         patch.object(service, "_change_packages", new_callable=AsyncMock), patch.object(service, "allow_scripts"):
        asyncio.run(service.uninstall_module("mirrordash-ha"))
    assert save.call_args[0][0]["module_settings"] == {"mirrordash-clock": {"x": 1}}
    assert save.call_args[0][0]["modules"] == {}
