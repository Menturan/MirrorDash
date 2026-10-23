"""USB on or off (Power → USB): the real mirrordash-usb (scripts/image/rootfs/usr/local/bin/), run against a
temporary /storage and a fake /sys laid out like a Pi 3's."""
import os
import subprocess

import pytest
from test_configure_image import unit

SCRIPT = unit("/usr/local/bin/mirrordash-usb")


@pytest.fixture
def usb(tmp_path):
    """run(*args) -> CompletedProcess; .written() is what went to the driver's bind/unbind, .bound(flag) fakes the kernel."""
    script = tmp_path / "usb"
    script.write_text(SCRIPT)
    sys_dir, storage = tmp_path / "sys", tmp_path / "storage"
    controller = sys_dir / "devices/platform/soc/3f980000.usb"
    eth = controller / "usb1/1-1/1-1.1/1-1.1:1.0"
    eth.mkdir(parents=True)
    (sys_dir / "bus/platform/devices").mkdir(parents=True)
    (sys_dir / "bus/platform/devices/3f980000.usb").symlink_to(controller)
    driver = sys_dir / "bus/platform/drivers/dwc_otg"
    driver.mkdir(parents=True)
    (sys_dir / "class/net/eth0").mkdir(parents=True)
    (sys_dir / "class/net/eth0/device").symlink_to(eth)
    (sys_dir / "class/net/eth0/operstate").write_text("down\n")
    model = tmp_path / "model"
    model.write_text("Raspberry Pi 3 Model B Rev 1.2\0")
    env = {**os.environ, "MIRRORDASH_STORAGE": str(storage), "MIRRORDASH_SYS": str(sys_dir), "MIRRORDASH_MODEL_FILE": str(model)}

    def run(*args):
        return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)

    def bound(flag: bool):
        (driver / "3f980000.usb").unlink(missing_ok=True)
        (driver / "bind").unlink(missing_ok=True)
        (driver / "unbind").unlink(missing_ok=True)
        if flag:
            (driver / "3f980000.usb").symlink_to(controller)
    run.bound = bound
    run.written = lambda: {f.name: f.read_text().strip() for f in driver.glob("*bind")}
    run.flag = storage / "usb-off"
    run.model, run.operstate = model, sys_dir / "class/net/eth0/operstate"
    bound(True)
    return run


def test_off_unbinds_the_controller_now_and_at_every_boot(usb):
    assert usb("off").returncode == 0 and usb.flag.exists()
    assert usb.written() == {"unbind": "3f980000.usb"}
    assert usb("status").stdout == "off\n"
    usb.bound(False)  # the kernel let go; a restart brings it back, and apply lets go again
    assert usb("apply").returncode == 0 and usb.written() == {}
    usb.bound(True)
    usb("apply")
    assert usb.written() == {"unbind": "3f980000.usb"}


def test_on_binds_it_again(usb):
    usb("off")
    usb.bound(False)
    assert usb("on").returncode == 0 and not usb.flag.exists()
    assert usb.written() == {"bind": "3f980000.usb"}
    assert usb("status").stdout == "on\n"


def test_off_refuses_while_the_mirror_is_on_ethernet(usb):
    usb.operstate.write_text("up\n")
    result = usb("off")
    assert result.returncode == 1 and "Ethernet" in result.stderr
    assert not usb.flag.exists() and usb.written() == {}


def test_a_pi_4_is_not_touched(usb):
    usb.model.write_text("Raspberry Pi 4 Model B Rev 1.4\0")
    assert usb("status").stdout == "unsupported\n"
    assert usb("apply").returncode == 0
    assert usb("off").returncode == 1 and not usb.flag.exists() and usb.written() == {}
