"""Runs the real mirrordash-gpio-overlays helper (extracted from setup_appliance.sh) on a temp config.txt."""
import re
import subprocess
from pathlib import Path

SETUP = Path(__file__).parent.parent / "scripts" / "setup_appliance.sh"
ORIGINAL = "dtparam=audio=on\n[all]\ndtoverlay=disable-bt\n"


def run_helper(tmp_path: Path, *args: str) -> tuple[int, str]:
    body = re.search(r"cat << 'EOF' > /usr/local/bin/mirrordash-gpio-overlays\n(.*?)\nEOF\n", SETUP.read_text(), re.S).group(1)
    config = tmp_path / "config.txt"
    if not config.exists():
        config.write_text(ORIGINAL)
    script = tmp_path / "helper.sh"
    script.write_text(body.replace("CONFIG=/boot/firmware/config.txt", f"CONFIG={config}"))
    result = subprocess.run(["bash", str(script), *args], capture_output=True, text=True)
    return result.returncode, config.read_text()


def test_writes_one_managed_block_and_replaces_it(tmp_path):
    code, text = run_helper(tmp_path, "17", "4")
    assert code == 0
    assert text.startswith(ORIGINAL)  # existing settings untouched
    assert "dtoverlay=gpio-key,gpio=17,active_low=1,gpio_pull=up,keycode=148" in text
    assert "dtoverlay=dht11,gpiopin=4" in text

    code, text = run_helper(tmp_path, "none", "22")  # change: old lines replaced, not appended
    assert code == 0
    assert text.count("MirrorDash GPIO (managed") == 1
    assert "gpio-key" not in text and "gpiopin=4" not in text
    assert "dtoverlay=dht11,gpiopin=22" in text

    code, text = run_helper(tmp_path, "none", "none")
    assert "dtoverlay=" not in text.replace("dtoverlay=disable-bt", "")
    assert text.startswith(ORIGINAL)


def test_rejects_invalid_pins_without_touching_config(tmp_path):
    for args in (("1", "none"), ("28", "none"), ("17", "17"), ("4;reboot", "none"), ("17",)):
        code, text = run_helper(tmp_path, *args)
        assert code == 2, args
        assert text == ORIGINAL
