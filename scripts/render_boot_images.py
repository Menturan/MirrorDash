"""Render the Plymouth boot images from the MD monogram (run after changing the monogram or texts).

    uv run python scripts/render_boot_images.py

splash.png is only the monogram, exactly as static/loading.html draws it, so the handover from
Plymouth to the kiosk is seamless. The images are small and pure black: Plymouth shows them at
their own size on any normal screen, so they are never scaled (scaling is what made the old
1280x1024 splash blurry) and there is no visible box around them.
"""
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parent.parent / "mirrordash_core" / "static"
MONOGRAM = ('<svg width="160" height="160" viewBox="0 0 100 100"><path d="M11.75 75.25V24.75L37.25 50L62.75 '
            '24.75V75.25H75.25A13.25 13.25 0 0 0 88.5 62V38A13.25 13.25 0 0 0 75.25 24.75" stroke="#FFFFFF" '
            'stroke-width="2.75" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')
IMAGES = {"splash.png": None, "restart.png": "Restarting", "shutdown.png": "Shutting down"}

PAGE = """<!doctype html><html><head><style>
@font-face {{ font-family: Inter; src: url('{font}'); font-weight: 100 900; }}
html, body {{ margin: 0; background: #000; }}
#image {{ display: inline-flex; flex-direction: column; align-items: center; background: #000; padding: 0 40px; }}
svg {{ display: block; }}
p {{ margin: 8px 0 32px; color: #999999; font: 300 22px/1.2 Inter; letter-spacing: 0.01em; }}
</style></head><body><div id="image">{monogram}{text}</div></body></html>"""


def main() -> None:
    font = (STATIC / "fonts" / "Inter-Variable.woff2").as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for name, text in IMAGES.items():
            # A real file, so the page may load the local font (about:blank pages can't read file://)
            with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
                f.write(PAGE.format(font=font, monogram=MONOGRAM, text=f"<p>{text}</p>" if text else ""))
            page.goto(Path(f.name).as_uri())
            page.evaluate("document.fonts.ready")
            Path(f.name).unlink()
            page.locator("#image").screenshot(path=str(STATIC / name))
            print(f"Wrote {STATIC / name}")
        browser.close()


if __name__ == "__main__":
    main()
