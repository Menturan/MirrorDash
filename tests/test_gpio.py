"""GPIO for modules (features/hardware/gpio.py, ARCHITECTURE #41): free header pins only, as the kernel's
handle to exactly those lines, handed from the core to the module host."""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_module_loader import _until

from mirrordash_core.features.hardware import gpio

CONFIG = {"system": {"devices": [{"type": "button", "pin": 17}, {"type": "light", "address": "0x23"}]}}


def test_free_pins_leave_out_the_cores_devices_i2c_and_everything_off_the_header():
    free = gpio.free_pins(CONFIG)
    assert 27 in free and 22 in free
    assert not {17, 2, 3, 0, 1, 48} & free  # a button, I²C (a light sensor), the HAT EEPROM, the SD card
    assert {2, 3} <= gpio.free_pins({})


def test_a_request_asks_the_kernel_for_exactly_those_lines():
    sent = {}

    def ioctl(fd, request, data):
        sent["request"], sent["data"] = request, data
        return gpio.REQUEST.pack(*gpio.REQUEST.unpack(data)[:-1], 42)  # the kernel fills in the line fd

    with patch.object(gpio.os, "open", return_value=7), patch.object(gpio.os, "close"), \
         patch.object(gpio.fcntl, "ioctl", side_effect=ioctl):
        fd = gpio.request_lines("/dev/gpiochip0", [27, 22], "mirrordash:garage", output=True, initial=1, active_low=True)
    fields = gpio.REQUEST.unpack(sent["data"])
    assert fd == 42 and sent["request"] == gpio.GET_LINE and len(sent["data"]) == 592
    assert fields[:3] == (27, 22, 0) and fields[64].rstrip(b"\0") == b"mirrordash:garage"
    assert fields[65] == gpio.OUTPUT | gpio.ACTIVE_LOW and fields[66] == 1  # flags, one attribute
    assert fields[72:76] == (gpio.ATTR_OUTPUT_VALUES, 0, 0b11, 0b11)  # both start high
    assert fields[112] == 2  # num_lines

    with patch.object(gpio.os, "open", return_value=7), patch.object(gpio.os, "close"), \
         patch.object(gpio.fcntl, "ioctl", side_effect=ioctl):
        gpio.request_lines("/dev/gpiochip0", [5], "m", pull="up", edge="both", debounce_ms=20)
    fields = gpio.REQUEST.unpack(sent["data"])
    assert fields[65] == gpio.INPUT | gpio.PULL["up"] | gpio.RISING | gpio.FALLING
    assert fields[72:76] == (gpio.ATTR_DEBOUNCE, 0, 20000, 0b1)
    with pytest.raises(ValueError):
        gpio.request_lines("/dev/gpiochip0", [5], "m", output=True, edge="both")


@pytest.mark.asyncio
async def test_a_module_gets_free_pins_from_the_core_and_nothing_else(tmp_path, monkeypatch):
    """A real module host and the core's client: the module's request goes to the core, which checks it
    and hands over the lines' file descriptor (a pipe here, there is no GPIO chip in a test)."""
    import mirrordash_core.features.modules.loader
    from mirrordash_core.features.modules import client as client_module
    from mirrordash_core.features.modules import host as host_module

    results = {}
    read_end, write_end = os.pipe()
    requested = []

    class Relay:
        def __init__(self, config):
            pass

        async def run_loop(self, broadcast_fn):
            if "lines" in results:  # started again after the crash below: asks again
                results["again"] = await self.gpio([27], output=True)
                await asyncio.Event().wait()
            for pins in ([48], [17], [2], [17, 17]):  # the SD card, a button, I²C, twice
                try:
                    await self.gpio(pins, output=True)
                except RuntimeError as e:
                    results[str(pins)] = str(e)
            results["lines"] = await self.gpio([27], output=True)
            os.write(write_end, b"x")
            results["read"] = os.read(results["lines"].fd, 1)  # the very descriptor the core requested
            raise RuntimeError("crash")

    class EntryPoint:
        name = "md_relay"
        module = "md_relay.plugin"

        def load(self):
            return Relay

    def fake_request(chip, pins, consumer, **options):
        requested.append((chip, pins, consumer, options))
        return os.dup(read_end)

    socket_path = str(tmp_path / "host.sock")
    monkeypatch.setenv("MIRRORDASH_MODULE_SOCKET", socket_path)
    monkeypatch.setenv("MIRRORDASH_MODULE_DIR", str(tmp_path / "modules"))
    monkeypatch.setattr(mirrordash_core.features.modules.loader, "module_entry_points", lambda: [EntryPoint()])
    monkeypatch.setattr(host_module.LogForward, "emit", lambda self, record: None)
    monkeypatch.setattr(client_module, "LAN_HELPER", str(tmp_path / "no-helper"))
    monkeypatch.setattr(client_module, "load_config", lambda: {
        "modules": {"relay": {"module": "md_relay"}}, "globals": {}, **CONFIG})
    monkeypatch.setattr(client_module, "manager", MagicMock(broadcast=AsyncMock()))
    monkeypatch.setattr(client_module, "header_chip", lambda: "/dev/gpiochip0")
    monkeypatch.setattr(client_module, "request_lines", fake_request)
    core = client_module.ModuleHost()
    server = asyncio.create_task(host_module.serve(socket_path))
    await _until(lambda: (tmp_path / "host.sock").exists())
    await core.start_modules()
    try:
        await _until(lambda: "again" in results)
        assert "GPIO 48 isn't free" in results["[48]"] and "GPIO 17 isn't free" in results["[17]"]
        assert "GPIO 2 isn't free" in results["[2]"] and "list of GPIO numbers" in results["[17, 17]"]
        assert requested == [("/dev/gpiochip0", [27], "mirrordash:relay", {"output": True})] * 2
        assert results["read"] == b"x"
        # The crash let the lines go (the kernel would refuse them as busy otherwise), the new start got its own
        assert results["lines"].fd == -1 and results["again"].fd >= 0
        # Asked from outside its own instance, the core refuses as well
        assert core._gpio({"id": 99, "instance": "elsewhere", "pins": [27]}) == "not an instance of this mirror"
    finally:
        await core.stop_modules()
        await asyncio.wait_for(server, 5)
        os.close(read_end)
        os.close(write_end)
