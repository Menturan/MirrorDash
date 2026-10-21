"""The docs point at what exists: every #anchor at a heading, every admin path at a label in the admin page."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS = sorted(ROOT.glob("*.md"))


def prose(path):
    """The file without its fenced code blocks, where `#` and `→` mean something else."""
    return re.sub(r"^```.*?^```", "", path.read_text(), flags=re.M | re.S)


def anchor(heading):
    """GitHub's anchor for a heading: lowercase, punctuation dropped, each space a hyphen."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def test_anchor_rule():
    assert anchor("14. WiFi Fallback / Captive Portal State Machine") == "14-wifi-fallback--captive-portal-state-machine"
    assert anchor("35. Matter: a Node.js Sidecar That Runs the Screen Through /api/v1") == \
        "35-matter-a-nodejs-sidecar-that-runs-the-screen-through-apiv1"


def test_links_to_headings_exist():
    anchors = {p.name: {anchor(h) for h in re.findall(r"^#+ (.+)$", prose(p), re.M)} for p in DOCS}
    broken = [f"{p.name}: {target}#{name}"
              for p in DOCS
              for target, name in re.findall(r"\]\(([\w.-]*\.md)?#([^)\s]+)\)", prose(p))
              if (target or p.name) in anchors and name not in anchors[target or p.name]]
    assert not broken, "links to headings that don't exist"


def test_admin_paths_exist():
    """**Settings → Developer** and the like: each part is a label somewhere in the admin page."""
    code = ROOT / "mirrordash_core"
    ui = " ".join(p.read_text() for p in [*code.rglob("*.html"), *code.rglob("*.js"), code / "forms.py"]
                  if not p.name.endswith(".min.js"))
    ui = re.sub(r"\s+", " ", ui.replace("&amp;", "&").replace("’", "'")).lower()
    missing = []
    for p in DOCS:
        for path in re.findall(r"\*{1,2}([^*\n]+→[^*\n]+)\*{1,2}", prose(p)):
            parts = [part.strip() for part in path.split("→")]
            # ponytail: only plain labels; a part with ( : . is prose (a phone's menu), not a path to check
            if all(re.fullmatch(r"[\w&' -]+", part) for part in parts):
                missing += [f"{p.name}: {path} ({part})" for part in parts if part.lower() not in ui]
    assert not missing, "admin page labels that don't exist"
