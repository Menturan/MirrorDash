"""The card's size on the mirror, in WebKit as the mirror's screen runs (ARCHITECTURE: Card size). Like
test_visual_admin.py it is left out of the default run (pyproject addopts): a sync Playwright page leaves an
event loop running that breaks the async tests after it. Run: uv run pytest tests/test_visual_card_size.py"""
from datetime import datetime

import pytest

pytestmark = pytest.mark.only_browser("webkit")


def _mirror_page(page, fake_clock=False):
    """The mirror page without a server behind it: its files served from static/, its socket a stand-in the
    test sends the screen's messages through. fake_clock: its timers and frames stand still until the test
    runs them (page.clock.run_for), so what happens over seconds does not depend on the machine."""
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
    if fake_clock:
        page.clock.install(time=datetime(2026, 3, 4, 10, 30))
    page.add_init_script("window.WebSocket = class { constructor() { window.__ws = this; } send() {} close() {} };")
    page.goto("http://mirror.test/static/index.html")
    page.wait_for_function("window.__ws")
    if fake_clock:
        page.clock.pause_at(datetime(2026, 3, 4, 10, 31))
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


def _width_of(page, module):
    return page.evaluate(f"document.querySelector('[data-module={module}]').getBoundingClientRect().width")


def test_a_preferred_width_on_auto_is_that_wide_with_no_third_and_never_wider_than_the_screen(page):
    send = _mirror_page(page)
    wide = "<div style='width: 5000px'>x</div>"  # the content does not decide it
    placement = {"position": "top_left", "html": wide, "width_percent": 0, "height_percent": 0, "opacity_percent": 100}
    send({**placement, "module": "pref", "preferred_width": "40rem"})
    assert _width_of(page, "pref") == pytest.approx(640, abs=1)  # more than a third of 1160 (386)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-module=pref]')).containerType") == "inline-size"
    send({**placement, "module": "pref", "preferred_width": "20rem"})
    assert _width_of(page, "pref") == pytest.approx(320, abs=1)  # less than the third: still that wide
    send({**placement, "module": "pref", "preferred_width": "200rem"})
    assert _width_of(page, "pref") == pytest.approx(1160, abs=1)  # min(preferred, usable)


def test_without_a_preferred_width_the_third_stays_and_a_custom_share_wins_over_one(page):
    send = _mirror_page(page)
    placement = {"position": "top_left", "html": "<div style='width: 5000px'>x</div>", "height_percent": 0, "opacity_percent": 100}
    send({**placement, "module": "plain", "width_percent": 0, "preferred_width": None})
    assert _width_of(page, "plain") == pytest.approx(1160 / 3, abs=1)
    send({**placement, "module": "plain", "width_percent": 50, "preferred_width": "20rem"})
    assert _width_of(page, "plain") == pytest.approx(580, abs=1)  # Custom: exactly its share
    send({**placement, "module": "plain", "width_percent": 0, "preferred_width": "20rem"})
    assert _width_of(page, "plain") == pytest.approx(320, abs=1)  # back to Auto live


CARD = "document.querySelector('[data-module=tall]')"
TALL = ("<div class='module-header'>Title</div>"
        + "".join(f"<div style='height: 50px'>row {i}</div>" for i in range(14)))  # 700 px in a card of at most 300


def _tall_card(page, overflow, html=TALL, **extra):
    send = _mirror_page(page, fake_clock=True)
    message = {"module": "tall", "position": "top_left", "html": html, "width_percent": 0, "height_percent": 0,
               "opacity_percent": 100, "overflow": overflow, **extra}
    send(message)
    # The observers run on real frames, the page's own timers on the fake clock
    for _ in range(100):
        if page.evaluate(f"{CARD}.hasAttribute('data-overflowing')"):
            break
        page.wait_for_timeout(20)
    return send, message


def _scrolled(page):
    return page.evaluate(f"{CARD}.scrollTop")


def _header_top(page):
    return page.evaluate(f"{CARD}.shadowRoot.querySelector('.module-header').getBoundingClientRect().top")


def test_a_tall_card_scrolls_slowly_with_its_heading_in_place_and_starts_again_at_the_top(page):
    _tall_card(page, "scroll")
    assert page.evaluate(f"{CARD}.hasAttribute('data-overflowing')")
    top = _header_top(page)
    page.clock.run_for(5000)
    assert _scrolled(page) == 0  # the top is shown first, for about 6 s
    page.clock.run_for(3000)  # 2 s of moving, at about 20 px/s
    assert 30 <= _scrolled(page) <= 50
    assert _header_top(page) == pytest.approx(top, abs=1)  # the heading stays
    assert page.evaluate(f"getComputedStyle({CARD}.shadowRoot.querySelector('.module-header')).position") == "sticky"
    assert page.evaluate(f"getComputedStyle({CARD}).maskImage").startswith("linear-gradient")  # more below
    page.clock.run_for(25000)  # the end (about 430 px is 21 s), where the fade is gone and it waits
    assert page.evaluate(f"{CARD}.hasAttribute('data-scroll-end')") and _scrolled(page) > 400
    assert page.evaluate(f"getComputedStyle({CARD}).maskImage") == "none"
    page.clock.run_for(8000)  # waited, dipped and went back to the top
    assert _scrolled(page) < 10 and not page.evaluate(f"{CARD}.hasAttribute('data-scroll-end')")
    page.clock.run_for(8000)
    assert _scrolled(page) > 0  # and again


def test_with_fade_a_tall_card_does_not_move(page):
    _tall_card(page, "fade")
    page.clock.run_for(30000)
    assert _scrolled(page) == 0
    assert page.evaluate(f"getComputedStyle({CARD}).maskImage").startswith("linear-gradient")
    assert page.evaluate(f"getComputedStyle({CARD}.shadowRoot.querySelector('.module-header')).position") != "sticky"


def test_with_reduced_motion_scroll_behaves_like_fade(page):
    page.emulate_media(reduced_motion="reduce")
    _tall_card(page, "scroll")
    page.clock.run_for(30000)
    assert _scrolled(page) == 0 and page.evaluate(f"getComputedStyle({CARD}).maskImage").startswith("linear-gradient")


def test_the_scroll_stops_when_the_card_no_longer_overflows_and_a_redraw_does_not_stack_a_second(page):
    send, message = _tall_card(page, "scroll")
    page.clock.run_for(9000)
    first = _scrolled(page)
    assert first > 0
    send(message)  # a redraw: the same scroll goes on, at the same speed
    page.wait_for_timeout(100)
    page.clock.run_for(2000)
    assert 30 <= _scrolled(page) - first <= 50
    send({**message, "html": "<p>short</p>"})
    for _ in range(100):
        if not page.evaluate(f"{CARD}.hasAttribute('data-overflowing')"):
            break
        page.wait_for_timeout(20)
    page.clock.run_for(1000)
    assert _scrolled(page) == 0 and not page.evaluate(f"{CARD}.hasAttribute('data-overflowing')")
    page.clock.run_for(30000)
    assert _scrolled(page) == 0 and page.evaluate(f"{CARD}.hasAttribute('data-scroll-end')") is False
