"""Runs the real mirrordash-gpio-overlays helper (extracted from setup_appliance.sh) on a temp config.txt."""
import re
import subprocess
from pathlib import Path

SETUP = Path(__file__).parent.parent / "scripts" / "setup_appliance.sh"
ORIGINAL = "dtparam=audio=on\n[pi4]\narm_boost=1\n"


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
    code, text = run_helper(tmp_path, "button:17", "dht11:4", "pir:18", "mmwave:22", "light:0x23")
    assert code == 0
    assert text.startswith(ORIGINAL)  # existing settings untouched
    block = text[len(ORIGINAL):]
    assert block.splitlines()[1] == "[all]"  # not inside the [pi4] section the file ended in
    for line in ("dtoverlay=gpio-key,gpio=17,active_low=1,gpio_pull=up,keycode=148",
                 "dtoverlay=gpio-key,gpio=18,active_low=0,gpio_pull=down,keycode=149",
                 "dtoverlay=gpio-key,gpio=22,active_low=0,gpio_pull=down,keycode=150",
                 "dtoverlay=dht11,gpiopin=4", "dtparam=i2c_arm=on", "dtoverlay=i2c-sensor,bh1750,addr=0x23"):
        assert line in block, line

    code, text = run_helper(tmp_path, "dht11:22")  # changes replace the block, never append
    assert code == 0
    assert text.count("MirrorDash GPIO (managed") == 1
    assert "gpio-key" not in text and "i2c" not in text
    assert "dtoverlay=dht11,gpiopin=22" in text

    code, text = run_helper(tmp_path)  # nothing connected
    assert code == 0
    assert "dtoverlay" not in text and text.startswith(ORIGINAL)


def test_rejects_invalid_arguments_without_touching_config(tmp_path):
    for args in (("button:1",), ("button:28",), ("button:17", "dht11:17"), ("button:17", "button:22"),
                 ("light:0x40",), ("light:0x23", "button:3"), ("button:4;reboot",), ("fan:12",), ("button",)):
        code, text = run_helper(tmp_path, *args)
        assert code == 2, args
        assert text == ORIGINAL


def test_fans(tmp_path):
    code, text = run_helper(tmp_path, "fan:14:60")
    assert code == 0
    assert "dtoverlay=gpio-fan,gpiopin=14,temp=60000,hyst=5000" in text
    code, text = run_helper(tmp_path, "pwm_fan:18:55")
    assert code == 0
    assert ("dtoverlay=pwm-gpio-fan,fan_gpio=18,fan_temp0=55000,fan_temp1=60000,"
            "fan_temp2=67500,fan_temp3=75000") in text
    assert "gpio-fan,gpiopin" not in text  # the previous fan is replaced


def test_rejects_bad_fans(tmp_path):
    for args in (("fan:14",), ("fan:14:39",), ("fan:14:81",), ("fan:14:6x",), ("fan:14:60", "pwm_fan:18:60"),
                 ("fan:14:60", "button:14")):
        code, text = run_helper(tmp_path, *args)
        assert code == 2, args
        assert text == ORIGINAL
