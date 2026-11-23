"""requires_mirrordash in a module's config_schema.json (ARCHITECTURE #65): an install or update of a release
that needs a newer MirrorDash is refused before it is switched in; no field, or a met one, goes through."""
import asyncio
import json

import pytest
from conftest import call

from mirrordash_core.features.modules import service
from mirrordash_core.features.modules.module_requirements import requirement_problem


@pytest.mark.parametrize("schema, problem", [
    ({}, None),                                                         # no field: no check
    (None, None),
    ({"requires_mirrordash": "0.12"}, None),                           # met
    ({"requires_mirrordash": "0.17"}, None),                           # exactly this one
    ({"requires_mirrordash": "0.18"}, "Needs MirrorDash 0.18 or newer: update MirrorDash first"),
    ({"requires_mirrordash": ">=0.12"}, "not a version"),               # a specifier is not a version
    ({"requires_mirrordash": 0.12}, "not a version"),
    ({"requires_mirrordash": None}, "not a version"),
])
def test_the_field_against_the_running_version(schema, problem):
    found = requirement_problem(schema, "mirrordash-x", core_version="0.17.0rc1")  # a release candidate counts as 0.17
    assert (problem is None and found is None) or (problem and problem in found)


def make_release(tmp_path, monkeypatch, schema: dict) -> None:
    """What uv leaves in the next site: pkgs/<folder> with the package and its dist-info, linked from site_b."""
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path))
    folder = tmp_path / "pkgs" / "mirrordash-x_1"
    (folder / "mirrordash_x").mkdir(parents=True)
    (folder / "mirrordash_x" / "config_schema.json").write_text(json.dumps(schema))
    (folder / "mirrordash_x" / "plugin.py").write_text("class X: pass\n")
    info = folder / "mirrordash_x-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: mirrordash-x\nVersion: 1.0\n")
    (info / "entry_points.txt").write_text("[mirrordash.modules]\nmirrordash-x = mirrordash_x.plugin:X\n")
    (tmp_path / "site_b").mkdir()
    (tmp_path / "site_b" / "mirrordash-x").symlink_to("../pkgs/mirrordash-x_1")


@pytest.fixture
def release(tmp_path, monkeypatch, module_site):
    """Whatever the test puts in `schema` is what pip "installs" for mirrordash-x."""
    monkeypatch.setattr("mirrordash_core.features.modules.module_requirements.get_core_version", lambda: "0.17.0")
    state = {}
    module_site.answer = lambda op, args: (make_release(tmp_path, monkeypatch, state["schema"]), (0, ""))[1] \
        if op == "pip" and not (tmp_path / "pkgs").exists() else (0, "")
    service.too_new.clear()
    return state


def test_an_update_that_needs_a_newer_mirrordash_is_refused_and_the_old_one_stays(release, module_site, monkeypatch):
    release["schema"] = {"requires_mirrordash": "0.99"}
    running = [{"name": "mirrordash_x", "package_name": "mirrordash-x", "version": "0.9", "type": "pypi"}]
    monkeypatch.setattr("mirrordash_core.features.modules.installed.installed_modules", lambda: running)
    response = call(service.update_modules, "mirrordash-x")
    assert response.status_code == 500
    assert "Needs MirrorDash 0.99 or newer: update MirrorDash first" in response.json()["detail"]
    assert module_site.kept is False  # the site with the new release was dropped: the running one is as it was
    assert ("check", "mirrordash-x", "mirrordash_x", "mirrordash-x") not in module_site.calls  # it never ran
    assert service.too_new["mirrordash-x"] == ("1.0", "Needs MirrorDash 0.99 or newer: update MirrorDash first")


def test_an_update_that_meets_its_requirement_goes_through(release, module_site):
    release["schema"] = {"requires_mirrordash": "0.12"}
    assert call(service.update_modules, "mirrordash-x").status_code == 200
    assert module_site.kept and not service.too_new


def test_a_release_without_the_field_goes_through(release, module_site):
    release["schema"] = {"title": "X"}
    assert call(service.update_modules, "mirrordash-x").status_code == 200
    assert module_site.kept


def test_a_malformed_field_is_refused(release, module_site, caplog):
    release["schema"] = {"requires_mirrordash": ">=0.12"}
    response = call(service.update_modules, "mirrordash-x")
    assert response.status_code == 500 and "not a version like 0.12" in response.json()["detail"]
    assert module_site.kept is False
    assert "requires_mirrordash" in caplog.text


def test_an_install_that_needs_a_newer_mirrordash_is_refused_before_it_is_switched_in(release, module_site):
    release["schema"] = {"requires_mirrordash": "0.99"}
    response = call(service.install_module, "mirrordash-x")
    assert response.status_code == 409 and "update MirrorDash first" in response.json()["detail"]
    assert module_site.kept is False


def test_an_install_that_meets_its_requirement_is_kept(release, module_site):
    release["schema"] = {"requires_mirrordash": "0.12"}
    assert call(service.install_module, "mirrordash-x").status_code == 200 and module_site.kept


def test_the_update_list_shows_a_refused_release_as_needing_mirrordash(monkeypatch):
    """After a refusal the module's update carries the message; the nightly update and Update All skip it."""
    from unittest.mock import AsyncMock, patch
    message = "Needs MirrorDash 0.99 or newer: update MirrorDash first"
    updates = [{"module_name": "a", "package_name": "a", "latest_version": "2.0", "target": "a", "needs": message},
               {"module_name": "b", "package_name": "b", "latest_version": "2.0", "target": "b", "needs": None}]
    with patch.object(service, "module_updates", AsyncMock(return_value=(updates, None))):
        offered = asyncio.run(service.modules_to_auto_update({}))
    assert [update["package_name"] for update in offered] == ["b"]
