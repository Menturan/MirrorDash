# SPDX-License-Identifier: AGPL-3.0-only
"""Shared module settings (ARCHITECTURE #62): the merge, saving with "Own value" on and off, the restart of every
instance when a shared value changes, the check of config["module_settings"], the drawer's form, the plain
backup and the uninstall."""
import asyncio
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
    """The save route with a module whose schema is SCHEMA; yields (client, the saved config's getter)."""
    config = {"globals": {}, "module_settings": {}, "modules": {"one": {"module": "mirrordash-ha"}}}
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch.object(schema, "_installed_module_schemas", return_value={"mirrordash_ha": SCHEMA}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=MagicMock()), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=SCHEMA), \
         patch("mirrordash_core.features.modules.routes.load_config", side_effect=lambda: __import__("copy").deepcopy(config)), \
         patch("mirrordash_core.features.modules.routes.save_config", side_effect=lambda saved: config.update(saved)), \
         patch("mirrordash_core.features.modules.routes.module_host.reload_modules", new_callable=AsyncMock) as reload:
        yield TestClient(app), config, reload


def save(client, instance, own=(), **values):
    form = {f"modules[{instance}][{key}]": value for key, value in values.items()}
    form |= {f"shared_own[{key}]": "true" for key in own}
    response = client.post(f"/admin/panels/modules/config/mirrordash-ha/save?instance_id={instance}", data=form, headers=HEADERS)
    assert response.status_code == 200, response.text


def test_saving_with_own_value_off_changes_the_shared_value_and_leaves_the_instance_without_it(saving):
    client, config, reload = saving
    config["modules"]["one"]["token"] = "an-old-own-one"
    save(client, "one", url="http://192.168.1.9:8123", token="new-shared", entity="light.x")
    assert config["module_settings"]["mirrordash-ha"] == {"url": "http://192.168.1.9:8123", "token": "new-shared"}
    assert config["modules"]["one"] == {"module": "mirrordash-ha", "entity": "light.x"}  # the override is gone
    reload.assert_awaited_once()


def test_saving_with_own_value_on_keeps_it_in_the_instance_and_the_shared_one_as_it_was(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"url": "http://192.168.1.5:8123", "token": "shared-token"}}
    save(client, "one", own=["token"], url="http://192.168.1.5:8123", token="mine")
    assert config["modules"]["one"]["token"] == "mine" and "url" not in config["modules"]["one"]
    assert config["module_settings"]["mirrordash-ha"] == {"url": "http://192.168.1.5:8123", "token": "shared-token"}


def test_a_new_instance_without_own_value_adds_no_key_of_its_own(saving):
    client, config, _ = saving
    save(client, "two", url="http://192.168.1.5:8123", token="t")
    assert config["modules"]["two"] == {"module": "mirrordash-ha"}


def test_the_rules_check_what_the_module_gets():
    from mirrordash_core.features.modules.setting_rules import check_setting_rules
    rules = {"required": ["url"], "properties": {"url": {**SCHEMA["properties"]["url"], "pattern": "https?://.+"}}}
    instance = {"module": "mirrordash-ha"}
    with pytest.raises(ValueError, match="Address doesn't have the right format"):
        check_setting_rules(with_shared(instance, {"module_settings": {"mirrordash-ha": {"url": "ftp://x"}}}, rules), rules)
    check_setting_rules(with_shared(instance, {"module_settings": {"mirrordash-ha": {"url": "http://x"}}}, rules), rules)


def test_a_setting_not_shared_by_the_schema_is_dropped_from_the_shared_values(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"entity": "stale"}}
    save(client, "one", token="t")
    assert config["module_settings"] == {"mirrordash-ha": {"token": "t"}}


def test_preview_and_choices_get_the_shared_value(saving):
    client, config, _ = saving
    config["module_settings"] = {"mirrordash-ha": {"token": "shared-token"}}
    form = {"modules[one][entity]": "x"}
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "shared-token"
    form["modules[one][token]"] = "typed-not-saved"  # an edited shared value shows in the preview before it is saved
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None) as preview:
        client.post("/admin/panels/modules/preview/mirrordash-ha?instance_id=one", data=form, headers=HEADERS)
    assert preview.call_args[0][1]["token"] == "typed-not-saved"


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


def test_the_form_draws_a_shared_field_with_the_shared_value_and_the_count(drawer):
    html = drawer("one")
    assert 'value="s3cret-shared"' in html and 'value="http://192.168.1.5:8123"' in html
    row, switch = row_of(html, "url")
    assert "Shared by all 2 Home Assistant instances: changing it changes them all." in row and "fa-link" in row
    assert " checked" not in switch  # Own value is off
    assert '<p class="field-description shared-note shared-note-own" hidden>' in row
    assert 'name="shared_own[entity]"' not in html and 'name="shared_own[rows]"' not in html  # not shared


def test_the_form_draws_own_value_on_with_the_instances_own_value(drawer):
    html = drawer("two")
    assert 'value="own-secret"' in html and 'value="s3cret-shared"' not in html.split('shared_own[url]')[0]
    _, switch = row_of(html, "url")
    assert " checked" in row_of(html, "token")[1] and " checked" not in switch
    own_row = row_of(html, "token")[0]
    assert "Only this instance. The shared value stays as it is." in own_row and "fa-user" in own_row


def test_a_secret_is_never_printed_in_the_note(drawer):
    for instance in ("one", "two"):
        notes = "".join(note.split("</p>")[0] for note in row_of(drawer(instance), "token")[0].split("<p ")[1:])
        assert "s3cret-shared" not in notes and "own-secret" not in notes
    note = row_of(drawer("two"), "unit")[0].split('shared-note-own"')[1].split("</p>")[0]
    assert "The shared value is <span" in note  # a plain setting names its shared value


def test_a_new_instance_counts_itself_and_starts_with_own_value_off(drawer):
    html = drawer("three")
    row, switch = row_of(html, "token")
    assert "Shared by all 3 Home Assistant instances" in row and " checked" not in switch
    assert 'value="s3cret-shared"' in html


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
