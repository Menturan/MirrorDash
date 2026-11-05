# SPDX-License-Identifier: AGPL-3.0-only
"""/design#forms: every kind of setting drawn by the admin form's own code from the JSON shown beside it."""

import html
import json

from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.features.kiosk.design_forms import EXAMPLES, design_cards


def test_each_example_is_drawn_from_the_json_it_shows():
    for card, (name, _tag, _desc, properties, _values, required, _extra) in zip(design_cards(), EXAMPLES, strict=True):
        shown = json.loads("{" + card["code"].split("\n#")[0].split("\n\n")[0] + "}")  # the JSON before any Python
        assert shown == {**properties, **({"required": required} if required else {})}, name
        for prop in properties.values():
            assert html.escape(prop["title"]) in card["form"], (name, prop["title"])  # drawn from that schema


def test_the_design_page_shows_them_all():
    page = TestClient(app).get("/design").text
    assert "design-forms:" not in page  # the marker is filled in
    forms = page.split('<section id="forms"', 1)[1].split("</section>", 1)[0]
    assert forms.count('class="component-card"') == len(EXAMPLES)
    assert html.escape('"pattern_message": "An address starting with http:// or https://"', quote=False) in forms.replace("&#34;", '"')
    assert "addDemoArrayItem" not in page  # the hand-made demo is gone
