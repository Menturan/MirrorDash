"""Phone clips of the real app (one .webm each) and stills of the mirror's own screens, for the concept videos.

    uv run python scripts/video/record.py build_workspace/video/clips
    uv run --with pillow --with fonttools --with brotli python scripts/video/compose.py build_workspace/video .

The app runs in this process on a throwaway HOME (stage.py); nothing touches this computer, and the install
of a module is stopped before it starts. compose.py's scene times follow the clips: after recording again,
check where Confirm is tapped in p-modules.webm and move the fade_at times to match. The site's loop is
build_workspace/video/out/mirrordash-loop.mp4 scaled to 1280 px into docs/video/loop.mp4.
"""
import shutil
import sys
from pathlib import Path

import stage
from playwright.sync_api import sync_playwright

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
url = stage.start()
PHONE = {"width": 390, "height": 844}

# A soft white dot where the finger taps, so a viewer sees what was pressed
TAPS = """
addEventListener('pointerdown', e => {
  const d = document.createElement('div');
  d.style.cssText = `position:fixed;left:${e.clientX-22}px;top:${e.clientY-22}px;width:44px;height:44px;border-radius:50%;
    background:rgba(255,255,255,.35);border:2px solid rgba(255,255,255,.8);z-index:2147483647;pointer-events:none;
    transition:transform .5s ease-out,opacity .5s ease-out`;
  document.body.append(d);
  requestAnimationFrame(() => { d.style.transform = 'scale(1.6)'; d.style.opacity = '0'; });
  setTimeout(() => d.remove(), 600);
}, true);
"""


def clip(browser, name, steps):
    ctx = browser.new_context(viewport=PHONE, record_video_dir=str(out / "raw"), record_video_size=PHONE,
                              has_touch=False, is_mobile=False)
    ctx.add_init_script(TAPS)
    page = ctx.new_page()
    steps(page)
    path = page.video.path()
    ctx.close()
    shutil.move(path, out / f"{name}.webm")
    print("clip", name)


def tap(page, selector, wait=700):
    page.locator(selector).first.click()
    page.wait_for_timeout(wait)


def wifi(page):
    page.route("**/api/wifi/scan", lambda r: r.fulfill(json={"networks": ["Hemma", "Hemma-5G", "Grannen"]}))
    page.route("**/api/wifi/setup", lambda r: r.fulfill(json={"status": "success"}))
    page.goto(url + "/wifi-setup")
    page.wait_for_timeout(1800)
    tap(page, "#network-list button")
    page.locator("#password").type("summer-evening-42", delay=90)
    page.wait_for_timeout(500)
    tap(page, "#connect-btn", 2500)


def admin(page):
    page.goto(url + "/admin")
    page.wait_for_timeout(1500)
    page.locator("#auth-password-input").type("my-mirror-pass", delay=90)
    page.wait_for_timeout(400)
    tap(page, "#auth-submit-btn", 3000)          # the recovery code
    tap(page, "#auth-submit-btn", 2500)          # on to the dashboard


GLIDE = """(e, ms) => new Promise(done => {
  let box = e.parentElement;                       // the nearest part of the page that scrolls
  while (box && !(box.scrollHeight > box.clientHeight && /auto|scroll/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
  box = box || document.scrollingElement;
  const r = e.getBoundingClientRect(), from = box.scrollTop;
  const to = Math.max(0, Math.min(box.scrollHeight - box.clientHeight, from + r.top + r.height / 2 - innerHeight / 2));
  const t0 = performance.now();
  const step = now => {
    const t = Math.min(1, (now - t0) / ms), k = t < .5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;   // ease in and out
    box.scrollTop = from + (to - from) * k;
    t < 1 ? requestAnimationFrame(step) : done();
  };
  requestAnimationFrame(step);
})"""


def scroll_to(page, locator, ms=1100):
    """Glide the element to the middle of the screen, as a thumb would, not in jumps."""
    locator.evaluate(GLIDE, ms)
    page.wait_for_timeout(400)


def login(page):
    page.request.post(url + "/admin/auth/login", data={"password": "my-mirror-pass"})
    page.goto(url + "/admin")
    page.wait_for_timeout(1500)


def modules(page):
    page.route("**/admin/panels/modules/install", lambda r: None)   # stays "Installing...": nothing is installed
    login(page)
    page.mouse.move(195, 500)
    tap(page, "#page-tab-modules", 800)
    page.locator("button:has-text('Install')").first.wait_for(timeout=30000)
    page.on("dialog", lambda d: d.accept())                          # "Install Weather?"
    install = page.locator("button[hx-confirm^='Install Weather']")
    scroll_to(page, install)
    page.wait_for_timeout(1200)
    install.click()
    page.wait_for_timeout(1300)
    tap(page, "button:has-text('Confirm')", 2500)                   # the admin page's own "Are you sure?"


def download(page):
    page.route("**/releases/download/**", lambda r: r.abort())       # the tap shows; the 1 GB file doesn't come
    page.goto("https://mirrordash.net/download")
    page.locator("#download:has-text('Download 0')").wait_for(timeout=20000)
    page.wait_for_timeout(1500)
    tap(page, "#download", 1500)


def settings(page):
    login(page)
    page.mouse.move(195, 500)
    tap(page, "#page-tab-power", 1500)
    page.locator("#sys-display-mode").select_option("interval")      # the screen on a schedule
    page.wait_for_timeout(2500)
    for tab, words in [("#page-tab-config", ["Matter"]),
                       ("#page-tab-system", ["Sensors"]), ("#page-tab-backup", ["Backup"])]:
        tap(page, tab, 1500)
        for word in words:
            target = page.get_by_text(word).first
            if target.count():
                scroll_to(page, target)
                break
        page.wait_for_timeout(1800)


with sync_playwright() as p:
    b = p.chromium.launch()
    m = b.new_page(viewport={"width": 1600, "height": 968})
    for hotspot, name in ((True, "m-wifi.png"), (False, "m-admin.png")):   # the Wi-Fi prompt, then the admin prompt
        stage.hotspot = hotspot
        m.goto(url + "/")
        m.wait_for_timeout(1500)
        m.screenshot(path=out / name)
    clip(b, "p-download", download)
    clip(b, "p-wifi", wifi)
    clip(b, "p-admin", admin)
    clip(b, "p-modules", modules)
    clip(b, "p-settings", settings)
    b.close()
shutil.rmtree(out / "raw", ignore_errors=True)
