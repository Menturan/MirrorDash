# Licensed under the PolyForm Noncommercial License 1.0.0.

"""GPIO push button and DHT11 sensor, both through Raspberry Pi kernel drivers.

The app's Python (uv, 3.14) has no GPIO library, and lgpio can't be built on the device, so
both parts use device-tree overlays instead (written to config.txt by the root helper
/usr/local/bin/mirrordash-gpio-overlays; the firmware applies them at the next boot):

- gpio-key: the kernel debounces the button and the PIR motion sensor and exposes them as
  input devices that we read as raw evdev events.
- dht11: the kernel does the timing-critical sensor protocol and exposes the readings in sysfs.
"""

import asyncio
import glob
import logging
import os
import struct
import time

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus

logger = logging.getLogger("mirrordash.core.hardware")

GPIO_HELPER = "/usr/local/bin/mirrordash-gpio-overlays"
BUTTON_KEYCODE = 148  # KEY_PROG1: no meaning to the compositor or the kiosk browser
PIR_KEYCODE = 149     # KEY_PROG2

BUTTON_ACTIONS = {
    "none": "Do nothing",
    "toggle_display": "Turn screen on/off",
    "restart": "Restart MirrorDash",
    "reboot": "Restart the mirror",
    "shutdown": "Shut down the mirror",
}
PRESS_TYPES = ("single", "double", "triple", "long")

# BCM GPIO number -> physical header pin, for the usable GPIOs (0/1 are reserved for the HAT EEPROM)
GPIO_HEADER_PINS = {
    2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 5: 29, 6: 31, 13: 33,
    19: 35, 26: 37, 14: 8, 15: 10, 18: 12, 23: 16, 24: 18, 25: 22, 8: 24, 7: 26, 12: 32,
    16: 36, 20: 38, 21: 40,
}


class PressClassifier:
    """Turns key down/up timestamps into single/double/triple/long presses.

    Pure logic (times are passed in), so it is testable without hardware. The caller feeds
    down()/up() and calls poll() after LONG_PRESS (while held) and MULTI_PRESS_WINDOW
    (after a release) to collect the finished press.
    """

    LONG_PRESS = 1.0          # held at least this long -> "long"
    MULTI_PRESS_WINDOW = 0.4  # next press must start within this time to count as double/triple

    def __init__(self):
        self.count = 0
        self.down_at: float | None = None
        self.last_up = 0.0
        self.long_fired = False

    def down(self, t: float) -> None:
        self.down_at = t
        self.long_fired = False

    def up(self, t: float) -> str | None:
        if self.down_at is None:
            return None
        held = t - self.down_at
        self.down_at = None
        if self.long_fired:
            return None
        if held >= self.LONG_PRESS:  # poll() didn't run in time; still a long press
            self.count = 0
            return "long"
        self.count += 1
        self.last_up = t
        return None

    def poll(self, t: float) -> str | None:
        if self.down_at is not None:
            if not self.long_fired and t - self.down_at >= self.LONG_PRESS:
                self.long_fired = True
                self.count = 0
                return "long"
            return None
        if self.count and t - self.last_up >= self.MULTI_PRESS_WINDOW:
            n, self.count = min(self.count, 3), 0
            return ("single", "double", "triple")[n - 1]
        return None


def find_gpio_key_devices() -> list[str]:
    """/dev/input/eventN of every gpio-keys device (one per gpio-key overlay: button, PIR)."""
    devices = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        driver = os.path.realpath(os.path.join(ev, "device", "device", "driver"))
        if driver.endswith("/gpio-keys"):
            devices.append("/dev/input/" + os.path.basename(ev))
    return devices


class GpioInputs:
    """Reads the gpio-keys input devices: the push button (KEY_PROG1) and the PIR motion
    sensor (KEY_PROG2, "key down" while the sensor reports motion), and polls the DHT11.

    Publishes on the event bus, for modules:
      hardware.button   {"press": "single"|"double"|"triple"|"long", "action": str}
      hardware.motion   {"motion": bool}                        when motion starts / stops
      hardware.climate  {"temperature_c": float, "humidity": int}  every CLIMATE_INTERVAL s
    """

    CLIMATE_INTERVAL = 60.0

    EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit: timeval, type, code, value
    EV_KEY = 1

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.climate_task: asyncio.Task | None = None
        self.fds: dict[str, int] = {}
        self.classifier = PressClassifier()
        self.button_seen = False
        self.motion_active = False
        self.last_motion_at = 0.0  # time.monotonic()

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._watch())
            self.climate_task = asyncio.create_task(self._publish_climate())

    async def stop(self) -> None:
        for task in (self.task, self.climate_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.task = self.climate_task = None
        for device in list(self.fds):
            self._close(device)

    async def _watch(self) -> None:
        # ponytail: looks for new gpio-keys devices every 5 s; they only appear at boot (the
        # overlays are applied by the firmware), so a udev monitor would gain little.
        while True:
            for device in find_gpio_key_devices():
                if device not in self.fds:
                    self._open(device)
            await asyncio.sleep(5)

    def _open(self, device: str) -> None:
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            logger.warning(f"Cannot open GPIO input device {device}: {e}")
            return
        self.fds[device] = fd
        self.button_seen = self.button_seen or self._has_key(device, BUTTON_KEYCODE)
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
        fd = self.fds.pop(device, None)
        if fd is not None:
            asyncio.get_running_loop().remove_reader(fd)
            os.close(fd)

    def _on_readable(self, device: str) -> None:
        try:
            data = os.read(self.fds[device], self.EVENT.size * 32)
        except BlockingIOError:
            return
        except OSError:  # device gone
            self._close(device)
            return
        for i in range(0, len(data) - self.EVENT.size + 1, self.EVENT.size):
            _, _, ev_type, code, value = self.EVENT.unpack_from(data, i)
            if ev_type != self.EV_KEY or value == 2:  # 2 = auto-repeat
                continue
            if code == BUTTON_KEYCODE:
                self._on_button(value == 1)
            elif code == PIR_KEYCODE:
                self.motion_active = value == 1
                self.last_motion_at = time.monotonic()
                event_bus.publish("hardware.motion", {"motion": self.motion_active})

    async def _publish_climate(self) -> None:
        while True:
            if load_config().get("system", {}).get("dht11", {}).get("pin") is not None:
                reading = await read_dht11()
                if reading:
                    event_bus.publish("hardware.climate", dict(reading))
            await asyncio.sleep(self.CLIMATE_INTERVAL)

    def _on_button(self, pressed: bool) -> None:
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        if pressed:
            self.classifier.down(now)
            loop.call_later(PressClassifier.LONG_PRESS, self._poll)
        else:
            self._dispatch(self.classifier.up(now))
            loop.call_later(PressClassifier.MULTI_PRESS_WINDOW, self._poll)

    def _poll(self) -> None:
        self._dispatch(self.classifier.poll(time.monotonic()))

    def _dispatch(self, press: str | None) -> None:
        if not press:
            return
        action = load_config().get("system", {}).get("button", {}).get("actions", {}).get(press, "none")
        logger.info(f"Button {press} press -> {action}")
        event_bus.publish("hardware.button", {"press": press, "action": action})
        asyncio.create_task(run_button_action(action))


async def run_button_action(action: str) -> None:
    from mirrordash_core.display_power import display_power_manager
    from mirrordash_core.system import poweroff_system, reboot_system, run_restart

    if action == "toggle_display":
        await display_power_manager.set_state(not display_power_manager.is_on)
    elif action == "restart":
        await run_restart()
    elif action == "reboot":
        await reboot_system(delay_sec=1.0)
    elif action == "shutdown":
        await poweroff_system(delay_sec=1.0)


def kernel_boot_id() -> str:
    """Changes on every OS boot (unlike the app's BOOT_ID, which changes on app restarts)."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


async def write_gpio_overlays(button_pin: int | None, dht11_pin: int | None, pir_pin: int | None = None) -> str | None:
    """Write the overlays to config.txt. Returns an error message, or None on success.

    ponytail: pin changes need a reboot. Overlays the firmware applied at boot can't be
    removed at runtime, so loading the new pin live would leave the old one active too.
    """
    if not os.path.exists(GPIO_HELPER):
        return "This mirror's OS image is too old for GPIO settings. Flash the latest MirrorDash OS image."
    args = [str(pin) if pin is not None else "none" for pin in (button_pin, dht11_pin, pir_pin)]
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", GPIO_HELPER, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"GPIO overlay helper failed ({proc.returncode}): {stderr.decode(errors='replace')}")
        return "Could not save the GPIO settings. See the logs for details."
    return None


def wanted_gpio_pins(system_cfg: dict) -> list:
    """[button, dht11, pir] GPIO numbers (or None) that the overlays should use."""
    dc = system_cfg.get("display_control", {})
    pir = dc.get("pir", {}).get("pin") if dc.get("mode") == "pir" else None
    return [system_cfg.get("button", {}).get("pin"), system_cfg.get("dht11", {}).get("pin"), pir]


def validate_gpio_pins(pins: list) -> str | None:
    """Error message if a pin can't be used or two parts share one, else None."""
    names = ("button", "DHT11 sensor", "motion sensor")
    used = {}
    for name, pin in zip(names, pins):
        if pin is None:
            continue
        if pin not in GPIO_HEADER_PINS:
            return f"GPIO {pin} can't be used for the {name}."
        if pin in used:
            return f"The {used[pin]} and the {name} can't use the same GPIO ({pin})."
        used[pin] = name
    return None


async def sync_gpio_overlays(system_cfg: dict) -> tuple[bool, str | None]:
    """Write the overlays if the wanted pins changed. Updates system_cfg (the caller saves it).

    Returns (restart_needed, error_message).
    """
    wanted = wanted_gpio_pins(system_cfg)
    if system_cfg.get("gpio_overlays", [None, None, None]) == wanted:
        return False, None
    error = validate_gpio_pins(wanted) or await write_gpio_overlays(*wanted)
    if error:
        return False, error
    system_cfg["gpio_overlays"] = wanted
    # The new pins are active after the next OS boot; remember which boot they were set in
    system_cfg["gpio_pending_boot_id"] = kernel_boot_id()
    return True, None


# ponytail: readings are cached for 30 s, so the dashboard's polling (and every viewer)
# costs at most one sensor read per 30 s; the DHT11 itself only allows about one per second.
DHT11_CACHE_SECONDS = 30.0
_dht11_cache: dict = {"at": 0.0, "value": None}


def _find_dht11_dir() -> str | None:
    for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
        try:
            with open(os.path.join(d, "name")) as f:
                if "dht11" in f.read():
                    return d
        except OSError:
            continue
    return None


def _read_dht11_once(d: str) -> dict:
    with open(os.path.join(d, "in_temp_input")) as f:
        temp = int(f.read()) / 1000
    with open(os.path.join(d, "in_humidityrelative_input")) as f:
        humidity = int(f.read()) / 1000
    return {"temperature_c": round(temp, 1), "humidity": round(humidity)}


def _read_dht11_blocking() -> dict | None:
    d = _find_dht11_dir()
    if not d:
        return None
    for attempt in range(3):  # the DHT11 regularly fails a read with EIO/ETIMEDOUT; retry
        try:
            return _read_dht11_once(d)
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(1.1)  # the sensor needs about 1 s between reads
    return None


async def read_dht11() -> dict | None:
    """Latest temperature (°C) and humidity (%), or None if no sensor answers."""
    if time.monotonic() - _dht11_cache["at"] < DHT11_CACHE_SECONDS and _dht11_cache["value"]:
        return _dht11_cache["value"]
    value = await asyncio.to_thread(_read_dht11_blocking)
    if value:
        _dht11_cache.update(at=time.monotonic(), value=value)
    return value or _dht11_cache["value"]


gpio_inputs = GpioInputs()
