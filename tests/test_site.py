"""The website in docs/ (GitHub Pages): every file a page links to is there."""

from html.parser import HTMLParser
from pathlib import Path

DOCS = Path(__file__).parent.parent / "docs"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []

    def handle_starttag(self, tag, attrs):
        self.found += [v for k, v in attrs if k in ("href", "src") and v]


def test_local_links_exist():
    pages = sorted(DOCS.glob("*.html"))
    assert pages
    for page in pages:
        parser = Links()
        parser.feed(page.read_text())
        for link in parser.found:
            path = link.split("#")[0]
            if not path or ":" in path:
                continue
            assert (DOCS / path).exists(), f"{page.name} links to {link}, which isn't in docs/"
