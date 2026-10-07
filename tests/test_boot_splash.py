"""The Plymouth theme written by setup_appliance.sh, and the boot images it shows.

Plymouth can't run here, so this guards what broke before: the image must be drawn in every mode
(pix.script skipped it at shutdown), the script must stay as simple as pix.script, and the images
must be small and pure black so Plymouth never scales them or shows a box around them.
"""
import re
import struct
from pathlib import Path

ROOT = Path(__file__).parent.parent
SETUP = (ROOT / "scripts" / "setup_appliance.sh").read_text()
STATIC = ROOT / "mirrordash_core" / "static"
IMAGES = ("splash.png", "restart.png", "shutdown.png")


def heredoc(name: str) -> str:
    return re.search(rf"cat << 'EOF' > \"\$THEME/{re.escape(name)}\"\n(.*?)\nEOF\n", SETUP, re.S).group(1)


def test_theme_file_points_at_the_script():
    theme = heredoc("mirrordash.plymouth")
    assert "ModuleName=script" in theme
    assert "ImageDir=/usr/share/plymouth/themes/mirrordash" in theme
    assert "ScriptFile=/usr/share/plymouth/themes/mirrordash/mirrordash.script" in theme


def test_script_draws_one_image_per_mode_in_every_mode():
    script = heredoc("mirrordash.script")
    assert script.count("{") == script.count("}") and script.count("(") == script.count(")")
    assert all(f'"{image}"' in script for image in IMAGES)
    assert '!= "shutdown"' not in script  # pix.script never drew its image when shutting down
    assert "Image.Text" not in script and "#" not in script  # no fonts needed, nothing pix.script doesn't do
    assert script.rstrip().endswith("sprite.SetPosition (image_x, image_y, -100);")  # drawn unconditionally


def test_boot_images_are_small_and_rendered_on_black():
    for name in IMAGES:
        header = (STATIC / name).read_bytes()[:24]
        assert header[:8] == b"\x89PNG\r\n\x1a\n", name
        width, height = struct.unpack(">II", header[16:24])
        assert width <= 640 and height <= 360, name  # shown at their own size on any normal screen
        assert name in SETUP  # installed by the setup script
    render = (ROOT / "scripts" / "render_boot_images.py").read_text()
    assert "background: #000" in render  # pure black, so no box shows around the image
