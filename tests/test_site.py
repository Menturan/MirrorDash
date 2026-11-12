"""The website's guide is built into the page: every heading has its anchor, and nothing is left to fetch."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import build_site
from test_docs import ROOT, anchor, prose


def test_guide_is_rendered_with_every_anchor():
    html = build_site.render_guide()
    for heading in re.findall(r"^#+ (.+)$", prose(ROOT / "USER_GUIDE.md"), re.M):
        assert f'id="{anchor(heading)}"' in html, heading
    build_site.build()
    page = (build_site.SITE / "guide.html").read_text()
    assert "<!-- guide -->" not in page and "Loading" not in page
    # A closed accordion isn't opened by a jump to a heading inside it (Firefox, Safari): headings stay outside
    assert not re.search(r"<details>(?:(?!</details>).)*<h[1-6]", html, re.S)
