"""The drawer's preview (ARCHITECTURE #43): a module's sample answers, its throwaway render, and the route."""
import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from mirrordash_core.admin import hash_password
from mirrordash_core.app import app
from mirrordash_core.features.modules import loader
from mirrordash_core.forms import render_array_item

AUTH = {"admin_auth": {"hash": hash_password("secret", "0123456789abcdef"), "salt": "0123456789abcdef"}}
HEADERS = {"X-API-Key": "secret"}


@pytest.fixture
def module_pkg(tmp_path, monkeypatch):
    """A module package with sample answers captured three days ago, on sys.path as a developer's would be."""
    pkg = tmp_path / "md_preview_test"
    (pkg / "preview").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    captured = date.today() - timedelta(days=3)
    (pkg / "preview" / "fixtures.json").write_text(json.dumps({"captured": captured.isoformat(), "answers": {
        "*/api/states": "states.json", "calendar.example.org/*": "cal.ics", "evil": "../secret.json"}}))
    (pkg / "preview" / "states.json").write_text(json.dumps({"state": "21.4", "when": f"{captured}T08:00:00Z", "version": "2.1"}))
    (pkg / "preview" / "cal.ics").write_text(f"DTSTART:{captured:%Y%m%d}T073000\r\n")
    (tmp_path / "secret.json").write_text("{}")
    monkeypatch.syspath_prepend(str(tmp_path))
    return pkg


def test_sample_answers_move_to_today_and_stay_in_the_preview_folder(module_pkg):
    fixtures = loader.preview_fixtures(module_pkg)
    assert set(fixtures) == {"*/api/states", "calendar.example.org/*"}  # ../secret.json is never read
    assert json.loads(fixtures["*/api/states"])["when"] == f"{date.today()}T08:00:00Z"
    assert json.loads(fixtures["*/api/states"])["version"] == "2.1"
    assert fixtures["calendar.example.org/*"] == f"DTSTART:{date.today():%Y%m%d}T073000\r\n".encode()
    assert loader.preview_fixtures(module_pkg.parent) is None  # no preview/fixtures.json: no preview


def test_a_preview_fetch_answers_from_the_samples_and_never_the_network(module_pkg):
    fetch, fetch_json = loader._make_fetch(None, "m", asyncio.Event(), fixtures=loader.preview_fixtures(module_pkg))

    async def fetches():  # awake is never set: a real fetch would wait for it, and then go out
        return (await fetch_json("http://ha.local:8123/api/states", headers={"Authorization": "Bearer x"}),
                await fetch("https://calendar.example.org/family.ics"),
                await fetch_json("https://elsewhere.example.org/api"))

    with patch("urllib.request.urlopen", side_effect=AssertionError("went to the network")):
        states, calendar, other = asyncio.run(asyncio.wait_for(fetches(), 2))
    assert states[0]["state"] == "21.4" and states[1] is None
    assert calendar[0].startswith(b"DTSTART:") and calendar[1] is None
    assert other == (None, "offline")


class Card:
    """A module that shows its setting and a fetched value, and reaches for the GPIO pins if asked to."""
    def __init__(self, config):
        self.config = config

    async def render(self):
        if self.config.get("use_gpio"):
            await self.gpio([17], output=True)
        data, _ = await self.fetch_json("http://ha.local/api/states")
        self.config["event_bus"].publish("seen", data)
        return f"<p>{self.config['heading']}: {data['state']}</p>"


def test_a_preview_renders_the_unsaved_settings_and_touches_nothing_else(module_pkg):
    ep = SimpleNamespace(load=lambda: Card, dist=None)
    published = []
    with patch.object(loader, "find_entry_point", return_value=ep), \
         patch.object(loader, "package_dir", return_value=module_pkg), \
         patch.object(loader.event_bus, "publish", side_effect=lambda *a, **k: published.append(a)):
        html = asyncio.run(loader.render_preview("card", {"module": "card", "heading": "Unsaved"}, {}, SimpleNamespace(awake=asyncio.Event())))
        assert html == "<p>Unsaved: 21.4</p>"
        assert published == []  # the event bus of a preview goes nowhere
        with pytest.raises(RuntimeError, match="not in a preview"):
            asyncio.run(loader.render_preview("card", {"module": "card", "heading": "x", "use_gpio": True}, {}, SimpleNamespace(awake=asyncio.Event())))
    with patch.object(loader, "find_entry_point", return_value=ep), patch.object(loader, "package_dir", return_value=module_pkg.parent):
        assert asyncio.run(loader.render_preview("card", {"module": "card"}, {}, None)) is None  # no samples: none


@pytest.fixture
def client():
    with patch("mirrordash_core.admin.load_config", return_value=AUTH), \
         patch("mirrordash_core.features.modules.routes.load_config", return_value={"globals": {}, "modules": {}}), \
         patch("mirrordash_core.features.modules.routes.find_entry_point", return_value=SimpleNamespace(dist=None)), \
         patch("mirrordash_core.features.modules.routes.get_module_schema", return_value=None):
        yield TestClient(app)


FORM = {"modules[ha-one][enabled]": "true", "modules[ha-one][position]": "top_left", "modules[ha-one][heading]": "Hem"}
URL = "/admin/panels/modules/preview/mirrordash_homeassistant?instance_id=ha-one"


def test_the_preview_route_needs_a_login(client):
    assert client.post(URL, data=FORM).status_code == 401


def test_the_preview_shows_the_samples_then_the_saved_card_then_why_not(client):
    card = '<img src=x onerror="alert(1)"><script>steal()</script>Hem'
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=card) as preview:
        r = client.post(URL, data=FORM, headers=HEADERS)
    assert r.status_code == 200 and r.headers["X-Preview"] == "sample"
    assert preview.call_args.args[1]["heading"] == "Hem"  # the unsaved form, typed and checked as Save would
    assert "<img" not in r.text and "\\u003cimg src=x" in r.text  # the card only as JSON, for the shadow root
    assert "Content-Security-Policy" in r.text and "script-src 'nonce-" in r.text

    from mirrordash_core.features.kiosk.ws import manager
    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, side_effect=ConnectionError), \
         patch.dict(manager.latest_messages, {"ha-one": {"module": "ha-one", "html": "<p>On the mirror</p>"}}):
        r = client.post(URL, data=FORM, headers=HEADERS)
    assert r.headers["X-Preview"] == "saved" and "On the mirror" in r.text

    with patch("mirrordash_core.features.modules.routes.module_host.preview", new_callable=AsyncMock, return_value=None):
        r = client.post(URL, data=FORM, headers=HEADERS)
    assert r.headers["X-Preview"] == "none" and "Not on the mirror yet" in r.text


def test_a_list_item_folds_unless_it_is_new():
    filled = render_array_item("modules[cal]", "calendars", {"name": {"type": "string"}}, 0, {"name": "Family"}, "Calendar")
    empty = render_array_item("modules[cal]", "calendars", {"name": {"type": "string"}}, 1, {}, "Calendar")
    assert filled.startswith('<details class="array-item-card">') and "<summary" in filled
    assert empty.startswith('<details class="array-item-card" open>')
