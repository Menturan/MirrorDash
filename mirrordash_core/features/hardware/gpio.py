# Licensed under the PolyForm Noncommercial License 1.0.0.
"""GPIO pins for modules (ARCHITECTURE #41), through the kernel's GPIO character device (linux/gpio.h, v2).

The module host can't open a GPIO chip: the whole chip would be its, the SD card's and the core's pins too
(the Pi's pin control lets a line be taken from I²C or the SD card). The core requests exactly the lines a
module asks for, if they are free header pins (free_pins), and hands the module host the request's file
descriptor (client.py): it reads and drives those lines and no other, and they are let go when it closes.
Pure Python (fcntl, struct), since the app's Python has no GPIO library (devices.py).
"""

import asyncio
import fcntl
import glob
import os
import struct
from types import SimpleNamespace

from mirrordash_core.features.hardware.devices import (
    DEVICE_TYPES,
    GPIO_HEADER_PINS,
    I2C_PINS,
    get_devices,
)

# The ioctl numbers and structs of linux/gpio.h (the v2 line API), as the kernel lays them out
CHIPINFO = 0x8044B401  # GPIO_GET_CHIPINFO_IOCTL: struct gpiochip_info (68 bytes)
GET_LINE = 0xC250B407  # GPIO_V2_GET_LINE_IOCTL: struct gpio_v2_line_request (592 bytes)
GET_VALUES = 0xC010B40E  # GPIO_V2_LINE_GET_VALUES_IOCTL: struct gpio_v2_line_values (16 bytes)
SET_VALUES = 0xC010B40F  # GPIO_V2_LINE_SET_VALUES_IOCTL
MAX_LINES, MAX_ATTRIBUTES, CONSUMER_SIZE = 64, 10, 32
# struct gpio_v2_line_request: offsets[64], consumer[32], then struct gpio_v2_line_config (flags,
# num_attrs, 5 padding, attrs[10] of {id, padding, value, mask}), num_lines, event_buffer_size,
# 5 padding, and fd, which the kernel fills in
REQUEST = struct.Struct(f"<{MAX_LINES}I{CONSUMER_SIZE}sQI5I" + "IIQQ" * MAX_ATTRIBUTES + "II5Ii")
VALUES = struct.Struct("<QQ")  # bits, mask
EVENT = struct.Struct("<QIIII6I")  # timestamp_ns, id (1 rising, 2 falling), offset, seqno, line_seqno
ACTIVE_LOW, INPUT, OUTPUT, RISING, FALLING = 1 << 1, 1 << 2, 1 << 3, 1 << 4, 1 << 5
PULL = {None: 0, "up": 1 << 8, "down": 1 << 9, "off": 1 << 10}
EDGE = {None: 0, "rising": RISING, "falling": FALLING, "both": RISING | FALLING}
ATTR_OUTPUT_VALUES, ATTR_DEBOUNCE = 2, 3
# The header's chip on a Pi Zero/1/2/3, a Pi 4 and a Pi 5 (where the header is the RP1's): its line
# offsets are the BCM GPIO numbers
HEADER_CHIPS = ("pinctrl-bcm2835", "pinctrl-bcm2711", "pinctrl-rp1")


def free_pins(config: dict) -> set[int]:
    """The header GPIOs a module may have: not one the core's devices (Hardware tab) use, nor I²C's pins
    while an I²C device is set up. The kernel refuses a pin an overlay holds anyway; this also covers one
    that is set up but waits for the next boot."""
    devices = get_devices(config.get("system", {}))
    used = {device.get("pin") for device in devices}
    if any("addresses" in DEVICE_TYPES.get(device.get("type"), {}) for device in devices):
        used |= set(I2C_PINS)
    return set(GPIO_HEADER_PINS) - used


def header_chip() -> str | None:
    """/dev/gpiochipN of the GPIO header, or None (not a Pi)."""
    for path in sorted(glob.glob("/dev/gpiochip*")):
        try:
            chip_descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except OSError:
            continue
        try:
            chip_info = fcntl.ioctl(chip_descriptor, CHIPINFO, bytes(68))
        except OSError:
            continue
        finally:
            os.close(chip_descriptor)
        chip_label = chip_info[32:64].rstrip(b"\0").decode(errors="replace")  # after the 32-byte name
        if chip_label in HEADER_CHIPS:
            return path
    return None


def request_lines(chip: str, pins: list[int], consumer: str, *, output: bool = False, initial: int = 0,
                  pull: str | None = None, edge: str | None = None, active_low: bool = False,
                  debounce_ms: int = 0) -> int:
    """Request these lines of the chip as one input or output group: the request's file descriptor.
    Raises ValueError for options that don't go together, OSError when the kernel refuses (a busy line)."""
    if output and edge:
        raise ValueError("an output has no edges to wait for")
    if pull not in PULL or edge not in EDGE or not 0 <= debounce_ms <= 1000:
        raise ValueError("pull is up, down or off; edge rising, falling or both; debounce_ms 0-1000")
    flags = (OUTPUT if output else INPUT) | PULL[pull] | EDGE[edge] | (ACTIVE_LOW if active_low else 0)
    all_lines_mask = (1 << len(pins)) - 1  # an attribute's mask: which of the requested lines it is for
    attributes = []  # (id, padding, value, mask)
    if output:
        attributes.append((ATTR_OUTPUT_VALUES, 0, all_lines_mask if initial else 0, all_lines_mask))
    if debounce_ms:
        attributes.append((ATTR_DEBOUNCE, 0, debounce_ms * 1000, all_lines_mask))  # the kernel takes µs
    attribute_count = len(attributes)
    attributes += [(0, 0, 0, 0)] * (MAX_ATTRIBUTES - attribute_count)
    request = REQUEST.pack(*pins, *[0] * (MAX_LINES - len(pins)), consumer.encode()[:CONSUMER_SIZE - 1],
                           flags, attribute_count, *[0] * 5,
                           *[field for attribute in attributes for field in attribute],
                           len(pins), 0, *[0] * 5, 0)
    chip_descriptor = os.open(chip, os.O_RDONLY | os.O_CLOEXEC)
    try:
        filled_in_request = fcntl.ioctl(chip_descriptor, GET_LINE, request)
        return REQUEST.unpack(filled_in_request)[-1]  # the last field: the lines' own file descriptor
    finally:
        os.close(chip_descriptor)


class GpioLines:
    """The lines a module got (self.gpio in loader.py), in the module host: get/set by GPIO number, and
    `async for event in lines.events()` for an input with edges (event.pin, event.rising, event.ns)."""

    def __init__(self, fd: int, pins: list[int]):
        self.fd, self.pins = fd, list(pins)

    def _bit(self, pin: int) -> int:
        """The pin's bit in the request's values: its place among the requested pins, not its number."""
        return 1 << self.pins.index(pin)

    def get(self, pin: int) -> int:
        bits, _ = VALUES.unpack(fcntl.ioctl(self.fd, GET_VALUES, VALUES.pack(0, self._bit(pin))))
        return int(bool(bits & self._bit(pin)))

    def set(self, pin: int, value) -> None:
        fcntl.ioctl(self.fd, SET_VALUES, VALUES.pack(self._bit(pin) if value else 0, self._bit(pin)))

    async def events(self):
        loop, ready = asyncio.get_running_loop(), asyncio.Event()
        os.set_blocking(self.fd, False)
        loop.add_reader(self.fd, ready.set)
        try:
            while True:
                await ready.wait()
                ready.clear()
                try:
                    events = os.read(self.fd, EVENT.size * 16)
                except BlockingIOError:
                    continue
                for start in range(0, len(events) - EVENT.size + 1, EVENT.size):
                    timestamp_ns, edge_kind, offset, *_ = EVENT.unpack_from(events, start)
                    yield SimpleNamespace(pin=offset, rising=edge_kind == 1, ns=timestamp_ns)
        finally:
            loop.remove_reader(self.fd)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
