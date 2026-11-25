import pytest
from fastapi.testclient import TestClient

from mirrordash_core.app import app


@pytest.fixture
def client():
    return TestClient(app)

def test_static_js_files_served(client):
    """Verify refactored JS files and static HTML prompts are served correctly."""
    # design-tokens.js
    resp = client.get('/static/js/kiosk/design-tokens.js')
    assert resp.status_code == 200
    assert 'DESIGN_TOKENS_CSS' in resp.text
    assert 'color-standard-gray' in resp.text

    # qrcode.js (local copy, draws the Wi-Fi QR code on the setup screen)
    resp = client.get('/static/js/qrcode.js')
    assert resp.status_code == 200
    assert 'createSvgTag' in resp.text


    # core.js
    resp = client.get('/static/js/kiosk/core.js')
    assert resp.status_code == 200
    assert 'DESIGN_TOKENS_CSS' in resp.text
    assert 'WebSocket' in resp.text

    # lucide.min.js (local copy)
    resp = client.get('/static/js/lucide.min.js')
    assert resp.status_code == 200
    assert 'lucide' in resp.text

def test_health_is_readable_from_the_start_page(client):
    """loading.html runs from file:// and fetches /health: without this header it never leaves."""
    assert client.get('/health').headers['access-control-allow-origin'] == '*'

def test_every_component_on_the_design_page_works_inside_a_module():
    """/design is the component library for modules: every class in its copy-paste code must be in
    modules.css (the only stylesheet besides the tokens that reaches a module's shadow root).
    Cards and sections marked as the mirror's own UI, and the settings-form controls, are not for modules."""
    import html
    import re
    from pathlib import Path

    static = Path(__file__).parent.parent / "mirrordash_core" / "static"
    page = (static / "design.html").read_text(encoding="utf-8")
    module_css = set(re.findall(r"\.([a-zA-Z][\w-]*)", (static / "modules.css").read_text(encoding="utf-8")))

    missing = {}
    for section in re.split(r'<section id="', page)[1:]:
        section_id = section.split('"', 1)[0]
        if section_id == "forms" or "explorer-own-ui" in section.split('class="component-card"', 1)[0]:
            continue  # settings-form controls, or a whole section of the mirror's own UI
        for card in section.split('class="component-card"')[1:]:
            if "explorer-own-ui" in card:
                continue
            for code in re.findall(r"<code>(.*?)</code>", card, re.S):
                for classes in re.findall(r'class="([^"]+)"', html.unescape(code)):
                    for name in classes.split():
                        if name not in module_css and not name.startswith("fa"):
                            missing.setdefault(section_id, set()).add(name)
    assert not missing, f"classes on /design that don't work in a module: {missing}"
    assert "module-message" in module_css


def test_modules_get_the_component_library(client):
    """The kiosk puts modules.css into every module's shadow root, and the page's style.css imports it."""
    assert client.get("/static/modules.css").status_code == 200
    core = client.get("/static/js/kiosk/core.js").text
    assert "/static/modules.css" in core and "${MODULES_CSS}" in core
    assert '@import url("modules.css")' in client.get("/static/style.css").text


def test_index_html_uses_modular_js(client):
    """Verify index.html references split JS files and no longer includes setup-prompt."""
    resp = client.get('/static/index.html')
    assert resp.status_code == 200
    assert '/static/js/kiosk/design-tokens.js' in resp.text
    assert '/static/js/kiosk/core.js' in resp.text
    assert '/static/js/kiosk/setup-prompt.js' not in resp.text
    assert '<setup-prompt>' not in resp.text
    # Original inline script should be gone
    assert 'DESIGN_TOKENS_HTML' not in resp.text
    assert 'checkSystemOverlayPrompts' not in resp.text

def test_the_card_size_is_set_from_the_percentages_and_the_old_keys_are_gone(client):
    core = client.get("/static/js/kiosk/core.js").text
    assert "data.width_percent" in core and "data.height_percent" in core and "data.opacity_percent" in core
    assert "data-fixed-width" in core and "data-preferred-width" in core and "data.preferred_width" in core and "data.overflow" in core
    overflow = client.get("/static/js/kiosk/overflow.js").text
    assert "data-overflowing" in overflow and "ResizeObserver" in overflow and "function watchOverflow" in overflow
    assert "watchOverflow" not in core.replace("watchOverflow(card);", "") and "${OVERFLOW_CSS}" in core
    assert "/static/js/kiosk/overflow.js" in client.get("/static/index.html").text
    for old in ("max_width", "max_height", "z_index", "zIndex", "data.opacity ", "style.maxWidth"):
        assert old not in core
    css = client.get("/static/style.css").text
    assert "data-preferred-width" in css and "--md-preferred-width" in css
    assert "--md-card-max-width" in css and "--md-card-max-height" in css and "container-type: inline-size" in css
