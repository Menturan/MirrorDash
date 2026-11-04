# SPDX-License-Identifier: AGPL-3.0-only
"""Buttons and presence sensors on the GPIO header: what was pressed, and what it does.

The kernel's gpio-keys driver debounces each pin and shows it as an input device; GpioInputs reads those
devices, turns a button's down/up times into single, double, triple and long presses (PressClassifier),
and publishes them on the event bus with the action the owner chose (run_button_action carries it out). It
also polls the sensors (sensors.py) and publishes their readings. gpio_inputs is the one running copy.
"""

import asyncio
import glob
import logging
import os
import struct
import time

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.features.hardware.devices import (
    BUTTON_TYPES,
    DEFAULT_LONG_PRESS,
    FAN_TYPES,
    KEYCODES,
    PRESENCE_TYPES,
    find_device,
)
from mirrordash_core.features.hardware.sensors import read_fan_state, read_sensor
from mirrordash_core.host import run_in_background

logger = logging.getLogger("mirrordash.core.hardware")


class PressClassifier:
    """Turns key down/up timestamps into single/double/triple/long presses.

    Pure logic (times are passed in), so it is testable without hardware. The caller feeds
    down()/up() and calls poll() after long_press (while held) and MULTI_PRESS_WINDOW
    (after a release) to collect the finished press.
    """

    MULTI_PRESS_WINDOW = 0.4  # next press must start within this time to count as double/triple
    # A contact that opens and closes again within this is one press: gpio-keys debounces only 5 ms (the
    # overlay has no setting), and the test mirror's button bounced for ~30 ms
    BOUNCE = 0.05

    def __init__(self, long_press: float = DEFAULT_LONG_PRESS):
        self.long_press = long_press  # held at least this long -> "long"
        self.count = 0
        self.down_at: float | None = None
        self.last_down = 0.0
        self.last_up = 0.0
        self.long_fired = False

    def down(self, now: float) -> None:
        """The button went down at `now` (seconds, any monotonic clock)."""
        if self.count and now - self.last_up < self.BOUNCE:  # the release was a bounce: the same press goes on
            self.count -= 1
            self.down_at = self.last_down
        else:
            self.down_at = now
        self.long_fired = False

    def up(self, now: float) -> str | None:
        """The button came up: "long" if it was held long enough and poll() didn't say so yet, else None."""
        if self.down_at is None:
            return None
        held = now - self.down_at
        self.last_down, self.down_at = self.down_at, None
        if self.long_fired:
            return None
        if held >= self.long_press:  # poll() didn't run in time; still a long press
            self.count = 0
            return "long"
        self.count += 1
        self.last_up = now
        return None

    def poll(self, now: float) -> str | None:
        """The press that is finished by `now`, if any: "long" while held, or the count of short ones."""
        if self.down_at is not None:
            if not self.long_fired and now - self.down_at >= self.long_press:
                self.long_fired = True
                self.count = 0
                return "long"
            return None
        if self.count and now - self.last_up >= self.MULTI_PRESS_WINDOW:
            press_count, self.count = min(self.count, 3), 0
            return ("single", "double", "triple")[press_count - 1]
        return None

def find_gpio_key_devices() -> list[str]:
    """/dev/input/eventN of every gpio-keys device (one per gpio-key overlay)."""
    devices = []
    for input_device in sorted(glob.glob("/sys/class/input/event*")):
        driver = os.path.realpath(os.path.join(input_device, "device", "device", "driver"))
        if driver.endswith("/gpio-keys"):
            devices.append("/dev/input/" + os.path.basename(input_device))
    return devices

class GpioInputs:
    """Reads the gpio-keys input devices and polls the IIO sensors.

    Publishes on the event bus, for modules:
      hardware.button   {"press": "single"|"double"|"triple"|"long", "action": str,
                         "button": "button"|"button_2"|"button_3"|"button_4"}
      hardware.motion   {"motion": bool, "sensor": "pir"|"mmwave"}   when presence starts / stops
      hardware.climate  {"temperature_c": float, "humidity": int}    every SENSOR_INTERVAL s
      hardware.light    {"lux": float}                               every SENSOR_INTERVAL s
      hardware.fan      {"level": int, "max_level": int, "cpu_temperature_c": float}
                        every SENSOR_INTERVAL s (level 0 = off; an on/off fan has max_level 1)
    """

    SENSOR_INTERVAL = 30.0
    # The kernel's struct input_event: time (seconds, microseconds), type, code, value. "l" is the
    # platform's long, so the size fits both the 64-bit OS and a 32-bit one
    EVENT = struct.Struct("llHHi")
    EV_KEY = 1

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.sensor_task: asyncio.Task | None = None
        self.file_descriptors: dict[str, int] = {}  # per open /dev/input/eventN
        self.classifiers: dict[str, PressClassifier] = {}  # per button type
        self.detected: set[str] = set()       # device types whose input device was found
        self.presence: dict[str, bool] = {}   # per presence sensor type
        self.last_motion_at = 0.0             # time.monotonic()

    @property
    def motion_active(self) -> bool:
        return any(self.presence.values())

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._watch())
            self.sensor_task = asyncio.create_task(self._publish_sensors())

    async def stop(self) -> None:
        for task in (self.task, self.sensor_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.task = self.sensor_task = None
        for device in list(self.file_descriptors):
            self._close(device)

    async def _watch(self) -> None:
        # ponytail: looks for new gpio-keys devices every 5 s; they only appear at boot (the
        # overlays are applied by the firmware), so a udev monitor would gain little.
        while True:
            try:
                for device in find_gpio_key_devices():
                    if device not in self.file_descriptors:
                        self._open(device)
            except Exception as e:  # one failure must not end the watch
                logger.warning(f"GPIO input watch failed: {e}")
            await asyncio.sleep(5)

    def _open(self, device: str) -> None:
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            logger.warning(f"Cannot open GPIO input device {device}: {e}")
            return
        self.file_descriptors[device] = fd
        self.detected |= {t for t, code in KEYCODES.items() if self._has_key(device, code)}
        asyncio.get_running_loop().add_reader(fd, self._on_readable, device)
        logger.info(f"Listening for GPIO input on {device}")

    @staticmethod
    def _has_key(device: str, code: int) -> bool:
        """Whether the input device can report this key (from its sysfs capability bitmap)."""
        try:
            path = f"/sys/class/input/{os.path.basename(device)}/device/capabilities/key"
            with open(path) as f:
                return bool(int(f.read().replace(" ", ""), 16) >> code & 1)
        except (OSError, ValueError):
            return False

    def _close(self, device: str) -> None:
        fd = self.file_descriptors.pop(device, None)
        if fd is not None:
            asyncio.get_running_loop().remove_reader(fd)
            os.close(fd)

    def _on_readable(self, device: str) -> None:
        try:
            events = os.read(self.file_descriptors[device], self.EVENT.size * 32)
        except BlockingIOError:
            return
        except OSError:  # device gone
            self._close(device)
            return
        for offset in range(0, len(events) - self.EVENT.size + 1, self.EVENT.size):
            _, _, event_type, code, value = self.EVENT.unpack_from(events, offset)
            if event_type != self.EV_KEY or value == 2:  # value 1 = down, 0 = up, 2 = auto-repeat
                continue
            for button in BUTTON_TYPES:
                if code == KEYCODES[button]:
                    self._on_button(button, value == 1)
            for sensor in PRESENCE_TYPES:
                if code == KEYCODES[sensor]:
                    self.presence[sensor] = value == 1
                    self.last_motion_at = time.monotonic()
                    event_bus.publish("hardware.motion", {"motion": self.motion_active, "sensor": sensor})

    async def _publish_sensors(self) -> None:
        events = {"dht11": "hardware.climate", "light": "hardware.light"}
        while True:
            try:
                system_cfg = load_config().get("system", {})
                for device_type, event in events.items():
                    if find_device(system_cfg, device_type):
                        reading = await read_sensor(device_type)
                        if reading:
                            event_bus.publish(event, dict(reading))
                if any(find_device(system_cfg, fan_type) for fan_type in FAN_TYPES):
                    fan = read_fan_state()
                    if fan:
                        event_bus.publish("hardware.fan", fan)
            except Exception as e:  # one failed reading must not end the sensor events
                logger.warning(f"Sensor reading failed: {e}")
            await asyncio.sleep(self.SENSOR_INTERVAL)

    def _on_button(self, button: str, pressed: bool) -> None:
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        classifier = self.classifiers.setdefault(button, PressClassifier())
        if pressed:
            device = find_device(load_config().get("system", {}), button) or {}
            classifier.long_press = device.get("long_press", DEFAULT_LONG_PRESS)  # a change applies on the next press
            classifier.down(now)
            loop.call_later(classifier.long_press, self._poll, button)
        else:
            self._dispatch(button, classifier.up(now))
            loop.call_later(PressClassifier.MULTI_PRESS_WINDOW, self._poll, button)

    def _poll(self, button: str) -> None:
        self._dispatch(button, self.classifiers[button].poll(time.monotonic()))

    def _dispatch(self, button: str, press: str | None) -> None:
        if not press:
            return
        device = find_device(load_config().get("system", {}), button) or {}
        action = device.get("actions", {}).get(press, "none")
        logger.info(f"{button} {press} press -> {action}")
        event_bus.publish("hardware.button", {"press": press, "action": action, "button": button})
        run_in_background(run_button_action(action))

async def run_button_action(action: str) -> None:
    """Do what the owner chose for a press (BUTTON_ACTIONS)."""
    from mirrordash_core.features.power.display_power import display_power_manager
    from mirrordash_core.host import poweroff_system, reboot_system, run_restart

    if action == "wake":
        display_power_manager.wake()
    elif action == "toggle_display":
        display_power_manager.toggle()
    elif action == "privacy":
        from mirrordash_core.features.power.privacy import toggle_privacy
        await toggle_privacy()
    elif action == "restart":
        await run_restart()
    elif action == "reboot":
        await reboot_system(delay_sec=1.0)
    elif action == "shutdown":
        await poweroff_system(delay_sec=1.0)

gpio_inputs = GpioInputs()
