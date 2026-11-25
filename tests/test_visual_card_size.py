"""The card's size on the mirror, in WebKit as the mirror's screen runs (ARCHITECTURE: Card size). Like
test_visual_admin.py it is left out of the default run (pyproject addopts): a sync Playwright page leaves an
event loop running that breaks the async tests after it. Run: uv run pytest tests/test_visual_card_size.py"""
import pytest

pytestmark = pytest.mark.only_browser("webkit")


def _mirror_page(page):
    """The mirror page without a server behind it: its files served from static/, its socket a stand-in the
    test sends the screen's messages through."""
    import json
    from pathlib import Path
    static = Path(__file__).parent.parent / "mirrordash_core" / "static"

    def serve(route):
        path = route.request.url.split("mirror.test", 1)[1].split("?")[0]
        if path == "/api/active-modules":
            return route.fulfill(json={"modules": []})
        file = static / path.removeprefix("/static/")
        if not file.is_file():
            return route.fulfill(status=404)
        types = {".html": "text/html", ".css": "text/css"}
        route.fulfill(body=file.read_bytes(), content_type=types.get(file.suffix, "text/javascript"))

    page.set_viewport_size({"width": 1280, "height": 720})  # usable 1160 x 600 inside the 60 px margins
    page.route("http://mirror.test/**", serve)
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    return lambda msg: page.evaluate("msg => window.__ws.onmessage({ data: msg })", json.dumps(msg))


def test_a_huge_card_stays_within_a_third_of_the_width_and_half_of_the_height_and_fades(page):
    send = _mirror_page(page)
    huge = "<div id='big' style='width: 5000px; height: 5000px; background: red'>x</div>"
    send({"module": "huge", "position": "top_left", "html": huge, "width_percent": 0, "height_percent": 0, "opacity_percent": 100})
    box = page.evaluate("(() => { const r = document.querySelector('[data-module=huge]').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert box[0] <= 1160 / 3 + 1 and box[1] <= 600 / 2 + 1
    assert box[0] > 300 and box[1] > 250  # it fills the cap, it is not squeezed
    page.wait_for_function("document.querySelector('[data-module=huge]').hasAttribute('data-overflowing')")
    assert page.evaluate("getComputedStyle(document.querySelector('[data-module=huge]')).maskImage").startswith("linear-gradient")
    assert page.evaluate("getComputedStyle(document.querySelector('[data-module=huge]')).containerType") == "normal"  # Auto: no containment


def test_a_custom_size_is_a_share_of_the_usable_screen_and_a_small_card_does_not_fade(page):
    send = _mirror_page(page)
    send({"module": "wide", "position": "top_left", "html": "<p>hi</p>", "width_percent": 50, "height_percent": 20, "opacity_percent": 40})
    card = "document.querySelector('[data-module=wide]')"
    assert page.evaluate(f"{card}.getBoundingClientRect().width") == pytest.approx(580, abs=1)  # 50 % of 1160, padding inside
    assert page.evaluate(f"getComputedStyle({card}).containerType") == "inline-size"
    assert page.evaluate(f"{card}.style.opacity") == "0.4"  # the computed one is in the slide-in until it ends
    assert page.evaluate(f"{card}.style.getPropertyValue('--md-card-max-height')").startswith("calc(")
    page.wait_for_timeout(100)
    assert not page.evaluate(f"{card}.hasAttribute('data-overflowing')")
    send({"module": "wide", "position": "top_left", "html": "<p>hi</p>", "width_percent": 0, "height_percent": 0, "opacity_percent": 100})
    assert page.evaluate(f"{card}.style.getPropertyValue('--md-card-max-width')") == ""  # back to Auto live
    assert not page.evaluate(f"{card}.hasAttribute('data-fixed-width')")


def test_the_layers_are_not_sized(page):
    send = _mirror_page(page)
    send({"module": "photo", "position": "background", "html": "<p>photo</p>", "width_percent": 0, "height_percent": 0})
    box = page.evaluate("(() => { const r = document.querySelector('[data-module=photo]').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert box == [1280, 720]
