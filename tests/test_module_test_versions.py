"""A module's "Test versions" switch: which GitHub release its update check offers (releases.py, service.py)."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules import releases, routes
from mirrordash_core.features.modules import service as modules

LOGIN = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
REPO = "https://github.com/user1/mirrordash-widget"
LATEST = "https://api.github.com/repos/user1/mirrordash-widget/releases/latest"
LIST = "https://api.github.com/repos/user1/mirrordash-widget/releases?per_page=20"
LIST_OF_RELEASES = [
    {"tag_name": "v1.9.0", "prerelease": False},
    {"tag_name": "v1.10.0rc1", "prerelease": True},
    {"tag_name": "v2.0.0", "prerelease": False, "draft": True},  # unpublished: never offered
    {"tag_name": "v1.10.0rc2", "prerelease": True},
]


@pytest.fixture
def client():
    with patch("mirrordash_core.admin.load_config", return_value=LOGIN):
        yield TestClient(app)


def entry_point(version: str, tag: str):
    direct_url = json.dumps({"url": REPO, "vcs_info": {"vcs": "git", "requested_revision": tag}})
    dist = SimpleNamespace(name="mirrordash-widget", version=version, read_text=lambda _name: direct_url)
    return SimpleNamespace(name="mirrordash_widget", dist=dist)


def check(installed: str, tag: str, switch: bool, releases_list=LIST_OF_RELEASES, stable="v1.9.0"):
    """check_module_update's answer for a module installed from `tag`, with GitHub answering `releases_list`
    (the list) and `stable` (the latest release); also the URLs asked."""
    asked = []

    async def answer(url, headers=None, max_age=0):
        asked.append(url)
        return {"tag_name": stable, "prerelease": False} if url == LATEST else releases_list

    config = {"module_test_versions": ["mirrordash-widget"] if switch else []}
    with patch.object(modules, "find_entry_point", return_value=entry_point(installed, tag)), \
         patch.object(modules, "site_name", return_value="mirrordash-widget"), \
         patch.object(modules, "load_config", return_value=config), \
         patch.object(releases, "fetch_json_cached", side_effect=answer):
        return asyncio.run(modules.check_module_update("mirrordash_widget")), asked


def test_switch_on_offers_the_highest_version_pre_releases_included_and_skips_drafts():
    result, asked = check("1.9.0", "v1.9.0", switch=True)
    assert asked == [LIST]  # one request
    assert result["latest_version"] == "1.10.0rc2" and result["prerelease"] is True
    assert result["target"] == f"git+{REPO}@v1.10.0rc2"


def test_switch_off_offers_only_the_newest_stable_release():
    result, asked = check("1.8.0", "v1.8.0", switch=False)
    assert asked == [LATEST]
    assert result["latest_version"] == "1.9.0" and result["prerelease"] is False


def test_switch_off_on_a_test_version_offers_no_downgrade():
    result, _ = check("1.10.0rc1", "v1.10.0rc1", switch=False)
    assert result["latest_version"] is None


def test_the_stable_release_is_offered_over_its_own_release_candidate():
    result, _ = check("1.10.0rc1", "v1.10.0rc1", switch=False, stable="v1.10.0")
    assert result["latest_version"] == "1.10.0" and result["prerelease"] is False
    assert result["target"] == f"git+{REPO}@v1.10.0"


def test_nothing_is_offered_when_the_installed_version_is_the_candidate():
    result, _ = check("1.10.0rc2", "v1.10.0rc2", switch=True)
    assert result["latest_version"] is None
    result, _ = check("1.0.0", "v1.10.0rc2", switch=True)  # the package's own version lags the tag
    assert result["latest_version"] is None


def test_a_list_that_isnt_one_offers_nothing():
    result, _ = check("1.0.0", "v1.0.0", switch=True, releases_list=None)
    assert result["latest_version"] is None


def test_the_badge_marks_a_test_version_only():
    def badge(**update):
        base = {"module_name": "w", "package_name": "p", "current_version": "1", "latest_version": "2", "target": "t",
                "notes": False, "needs": None, "prerelease": False}
        with patch.object(routes, "check_module_update", AsyncMock(return_value={**base, **update})):
            return asyncio.run(routes.check_module_update_route("w")).body.decode()
    assert ">Test<" in badge(prerelease=True) and "Update Available (v2)" in badge(prerelease=True)
    assert ">Test<" not in badge()


def test_the_switch_saves_and_reads_the_flag(client):
    config = {}
    ep = entry_point("1.0.0", "v1.0.0")
    headers = {"X-API-Key": "secret"}
    with patch.object(modules, "load_config", return_value=config), patch.object(modules, "save_config"), \
         patch.object(routes, "find_entry_point", return_value=ep), patch.object(routes, "site_name", return_value="mirrordash-widget"), \
         patch.object(routes, "check_module_update", AsyncMock()) as asked, \
         patch.object(routes.event_bus, "publish") as published:
        url = "/admin/panels/modules/test-versions/mirrordash_widget"
        assert client.post(url, data={"on": "true"}, headers=headers).status_code == 200
        assert config["module_test_versions"] == ["mirrordash-widget"]
        assert asked.await_args.kwargs == {"max_age": 0}  # a fresh check
        published.assert_called_with("modules.test_versions", "mirrordash_widget")
        assert client.post(url, data={"on": "false"}, headers=headers).status_code == 200
        assert config["module_test_versions"] == []
    with patch.object(routes, "find_entry_point", return_value=None):
        assert client.post(url, data={"on": "true"}, headers=headers).status_code == 404


def test_the_settings_sheet_shows_the_switch(client):
    ep = entry_point("1.0.0", "v1.0.0")
    ep.dist.metadata = {"Summary": ""}
    config = {"module_test_versions": ["mirrordash-widget"]}
    with patch.object(routes, "find_entry_point", return_value=ep), patch.object(routes, "load_config", return_value=config), \
         patch.object(routes, "site_name", return_value="mirrordash-widget"), patch.object(routes, "get_module_schema", return_value={}), \
         patch.object(routes, "scripts_allowed", return_value=False):
        page = client.get("/admin/panels/modules/config/mirrordash_widget", headers={"X-API-Key": "secret"})
    assert page.status_code == 200
    assert 'id="module-test-versions"' in page.text and "Test versions" in page.text


def test_the_nightly_update_installs_a_test_version_only_for_a_module_with_the_switch_on():
    def nightly(switch: bool):
        config = {"module_test_versions": ["mirrordash-widget"] if switch else []}
        with patch.object(modules, "module_entry_points", return_value=[entry_point("1.9.0", "v1.9.0")]), \
             patch.object(modules, "find_entry_point", return_value=entry_point("1.9.0", "v1.9.0")), \
             patch.object(modules, "site_name", return_value="mirrordash-widget"), \
             patch.object(modules, "load_config", return_value=config), \
             patch.object(releases, "fetch_json_cached", side_effect=_answer):
            return asyncio.run(modules.modules_to_auto_update(config))
    on, off = nightly(True), nightly(False)
    assert [update["target"] for update in on] == [f"git+{REPO}@v1.10.0rc2"]
    assert off == []  # 1.9.0 is the newest stable release


async def _answer(url, headers=None, max_age=0):
    return {"tag_name": "v1.9.0"} if url == LATEST else LIST_OF_RELEASES


def test_a_fresh_install_pins_the_newest_stable_release():
    asked = []

    async def pinned():
        def urlopen(request, timeout):
            asked.append(request.full_url)
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps({"tag_name": "v1.9.0"}).encode()
            return response
        with patch.object(modules.urllib.request, "urlopen", urlopen):
            return await modules._pinned_to_latest_release(f"git+{REPO}")
    assert asyncio.run(pinned()) == f"git+{REPO}@v1.9.0"
    assert asked == [LATEST]


def test_uninstalling_a_module_removes_its_flag(module_site):
    config = {"module_test_versions": ["mirrordash-widget", "mirrordash-other"]}
    with patch.object(modules, "load_config", return_value=config), patch.object(modules, "save_config"), \
         patch.object(modules, "_uninstall_from_venv", AsyncMock()), patch.object(modules, "run_in_background"), patch.object(modules, "run_restart", MagicMock()):
        asyncio.run(modules.uninstall_module("mirrordash-widget"))
    assert config["module_test_versions"] == ["mirrordash-other"]


@pytest.mark.parametrize("data,tag", [([], None), ([{"tag_name": "x", "draft": True}], None), ("nope", None),
                                      ([{"tag_name": "v1.0.0"}, {"tag_name": "not-a-version"}], "v1.0.0")])
def test_highest_release_ignores_what_it_cannot_use(data, tag):
    release = releases.highest_release(data)
    assert (release["tag_name"] if release else None) == tag
