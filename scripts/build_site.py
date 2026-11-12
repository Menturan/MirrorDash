"""Builds the website into build_workspace/site: docs/ as it is, with USER_GUIDE.md rendered into guide.html."""
import re
import shutil
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).parent.parent
SITE = ROOT / "build_workspace" / "site"
REPO = "https://github.com/Menturan/MirrorDash/blob/master/"


def render_guide() -> str:
    html = MarkdownIt("commonmark").enable("table").render((ROOT / "USER_GUIDE.md").read_text())

    # ponytail: the id rule works on plain heading text, enough while the guide's headings have no links or code;
    # the upgrade is a slugger plugin.
    def heading_id(m):
        slug = re.sub(r"[^\w\- ]", "", re.sub(r"<[^>]+>", "", m[3]).strip().lower()).replace(" ", "-")
        return f'<h{m[1]}{m[2]} id="{slug}">{m[3]}</h{m[1]}>'
    html = re.sub(r"<h([1-6])([^>]*)>(.*?)</h\1>", heading_id, html)

    # Links within the guide stay on the page; links to the repo's other files go to GitHub
    def link(m):
        href = m[1]
        if href.startswith("USER_GUIDE.md#"):
            href = href[13:]
        elif not re.match(r"([a-z]+:|#)", href, re.I):
            href = REPO + href
        return f'href="{href}"'
    return re.sub(r'href="([^"]*)"', link, html)


def build():
    shutil.rmtree(SITE, ignore_errors=True)
    shutil.copytree(ROOT / "docs", SITE)
    page = SITE / "guide.html"
    page.write_text(page.read_text().replace("<!-- guide -->", render_guide()))


if __name__ == "__main__":
    build()
