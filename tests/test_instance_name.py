# SPDX-License-Identifier: AGPL-3.0-only
"""An instance's name: the owner's own (the Name setting, instance_name), else the module's title and its
number, the same on the admin page, the screen map, the notifications and the crash guard."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules.schema import validate_config
from mirrordash_core.features.modules.service import instance_title


def test_the_owners_name_else_the_modules_title_and_number():
    assert instance_title("mirrordash_clock-2", {"instance_name": "  Kitchen Clock "}) == "Kitchen Clock"
    assert instance_title("mirrordash_clock-2", {"instance_name": "  "}) == "Clock 2"  # blank: the automatic name
    assert instance_title("mirrordash_clock", {}) == "Clock"
    assert instance_title("mirrordash-weather-radar-3", {}, {"title": "Rain Radar"}) == "Rain Radar 3"


def test_a_name_must_be_short_text():
    validate_config({"modules": {"c": {"instance_name": "x" * 40}}})
    for bad in ("x" * 41, 5):
        with pytest.raises(ValueError, match="name"):
            validate_config({"modules": {"c": {"instance_name": bad}}})


def test_the_settings_start_with_the_name():
    auth = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
    modules = {"modules": {"mirrordash_clock-2": {"module": "mirrordash_clock", "instance_name": "Hall Clock"}}}
    with patch("mirrordash_core.admin.load_config", return_value=auth), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value=modules), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=SimpleNamespace(dist=None, name="x")), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value={"title": "Clock"}), \
         patch("mirrordash_core.features.modules.routes.site_name", return_value="x"):
        html = TestClient(app).get("/admin/panels/modules/config/mirrordash_clock?instance_id=mirrordash_clock-2",
                                   headers={"X-API-Key": "secret"}).text
    assert '<h3 class="module-config-instance">Hall Clock</h3>' in html
    name_field = html.index('name="modules[mirrordash_clock-2][instance_name]"')
    assert name_field < html.index('name="modules[mirrordash_clock-2][position]"') and 'maxlength="40"' in html
