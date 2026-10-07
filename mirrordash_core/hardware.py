# Licensed under the PolyForm Noncommercial License 1.0.0.

"""Sensors and inputs on the GPIO header, all through Raspberry Pi kernel drivers.

The app's Python (uv, 3.14) has no GPIO library, and lgpio can't be built on the device, so
every device is a device-tree overlay instead (written to config.txt by the root helper
/usr/local/bin/mirrordash-gpio-overlays; the firmware applies them at the next boot):

- gpio-key (button, PIR, mmWave): the kernel debounces the pin and exposes it as an input
  device, read here as raw evdev events.
- dht11, i2c-sensor (BH1750): the kernel does the sensor protocol and exposes the readings
  under /sys/bus/iio.

The devices are a list in config (system.devices), at most one of each type:
  {"type": "button", "pin": 17, "actions": {...}}, {"type": "light", "address": "0x23"}, ...
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

# BCM GPIO number -> physical header pin, for the usable GPIOs (0/1 are reserved for the HAT EEPROM)
GPIO_HEADER_PINS = {
    2: 3, 3: 5, 4: 7, 17: 11, 27: 13, 22: 15, 10: 19, 9: 21, 11: 23, 5: 29, 6: 31, 13: 33,
    19: 35, 26: 37, 14: 8, 15: 10, 18: 12, 23: 16, 24: 18, 25: 22, 8: 24, 7: 26, 12: 32,
    16: 36, 20: 38, 21: 40,
}
I2C_PINS = (2, 3)  # SDA, SCL: taken as soon as an I2C device is added

# Everything the Hardware tab can add. "pin": wired to one GPIO; "addresses": an I2C device.
# The overlay lines themselves live in the root helper, which only accepts these types.
DEVICE_TYPES = {
    "button": {"label": "Push button", "pin": True,
               "wiring": "Connect the button between the GPIO and a GND pin."},
    "pir": {"label": "PIR motion sensor", "pin": True,
            "wiring": "Sensor output to the GPIO; power from a 5V pin and GND."},
    "mmwave": {"label": "mmWave presence sensor (e.g. LD2410)", "pin": True,
               "wiring": "The sensor's OUT pin to the GPIO; power from a 5V pin and GND. "
                         "Unlike PIR it also notices someone standing still."},
    "dht11": {"label": "DHT11 temperature & humidity sensor", "pin": True,
              "wiring": "Data pin to the GPIO; power from a 3.3V pin and GND."},
    "light": {"label": "BH1750 light sensor (I²C)", "addresses": ("0x23", "0x5c"),
              "wiring": "SDA to GPIO 2 (pin 3), SCL to GPIO 3 (pin 5); power from a 3.3V pin and GND. "
                        "Address 0x23 with ADDR unconnected or to GND, 0x5c with ADDR to 3.3V."},
}
KEYCODES = {"button": 148, "pir": 149, "mmwave": 150}  # KEY_PROG1..3: ignored by the kiosk
PRESENCE_TYPES = ("pir", "mmwave")

BUTTON_ACTIONS = {
    "none": "Do nothing",
    "toggle_display": "Turn screen on/off",
    "restart": "Restart MirrorDash",
    "reboot": "Restart the mirror",
    "shutdown": "Shut down the mirror",
}
PRESS_TYPES = ("single", "double", "triple", "long")


# --- Device list -------------------------------------------------------------------------

def get_devices(system_cfg: dict) -> list[dict]:
    return system_cfg.get("devices", [])


def find_device(system_cfg: dict, device_type: str) -> dict | None:
    return next((d for d in get_devices(system_cfg) if d.get("type") == device_type), None)


def validate_devices(devices: list[dict]) -> str | None:
    """Error message if the list can't be wired like this, else None."""
    pins: dict[int, str] = {}
    types = [d.get("type") for d in devices]
    for d in devices:
        spec = DEVICE_TYPES.get(d.get("type"))
        if not spec:
            return f"Unknown device type: {d.get('type')}."
        if types.count(d["type"]) > 1:
            return f"Only one {spec['label']} can be connected."
        if spec.get("pin"):
            pin = d.get("pin")
            if pin not in GPIO_HEADER_PINS:
                return f"GPIO {pin} can't be used for the {spec['label']}."
            if pin in pins:
                return f"The {pins[pin]} already uses GPIO {pin}."
            pins[pin] = spec["label"]
        elif d.get("address") not in spec["addresses"]:
            return f"The {spec['label']} needs address {' or '.join(spec['addresses'])}."
    if any("addresses" in DEVICE_TYPES[t] for t in types):
        for pin in I2C_PINS:
            if pin in pins:
                return f"GPIO {pin} is needed for I²C (the light sensor); the {pins[pin]} must use another GPIO."
    return None


def overlay_args(devices: list[dict]) -> list[str]:
    """Helper arguments, e.g. ["button:17", "light:0x23"]."""
    return [f"{d['type']}:{d['pin'] if 'pin' in d else d['address']}" for d in devices]


# --- Writing config.txt ------------------------------------------------------------------

def kernel_boot_id() -> str:
    """Changes on every OS boot (unlike the app's BOOT_ID, which changes on app restarts)."""
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


async def write_gpio_overlays(args: list[str]) -> str | None:
    """Write the overlays to config.txt. Returns an error message, or None on success.

    ponytail: changes need a reboot. Overlays the firmware applied at boot can't be removed
    at runtime, so loading a new pin live would leave the old one active too.
    """
    if not os.path.exists(GPIO_HELPER):
        return "This mirror's OS image is too old for GPIO settings. Flash the latest MirrorDash OS image."
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", GPIO_HELPER, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.error(f"GPIO overlay helper failed ({proc.returncode}): {stderr.decode(errors='replace')}")
        return "Could not save the GPIO settings. See the logs for details."
    return None


async def sync_gpio_overlays(system_cfg: dict) -> tuple[bool, str | None]:
    """Write the overlays if the device wiring changed. Updates system_cfg (the caller saves it).

    Returns (restart_needed, error_message).
    """
    devices = get_devices(system_cfg)
    wanted = overlay_args(devices)
    if system_cfg.get("gpio_overlays", []) == wanted:
        return False, None
    error = validate_devices(devices) or await write_gpio_overlays(wanted)
    if error:
        return False, error
    system_cfg["gpio_overlays"] = wanted
    # Active after the next OS boot; remember which boot the change was made in
    system_cfg["gpio_pending_boot_id"] = kernel_boot_id()
    return True, None


# --- Inputs: button and presence sensors -------------------------------------------------

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
    """/dev/input/eventN of every gpio-keys device (one per gpio-key overlay)."""
    devices = []
    for ev in sorted(glob.glob("/sys/class/input/event*")):
        driver = os.path.realpath(os.path.join(ev, "device", "device", "driver"))
        if driver.endswith("/gpio-keys"):
            devices.append("/dev/input/" + os.path.basename(ev))
    return devices


class GpioInputs:
    """Reads the gpio-keys input devices and polls the IIO sensors.

    Publishes on the event bus, for modules:
      hardware.button   {"press": "single"|"double"|"triple"|"long", "action": str}
      hardware.motion   {"motion": bool, "sensor": "pir"|"mmwave"}   when presence starts / stops
      hardware.climate  {"temperature_c": float, "humidity": int}    every SENSOR_INTERVAL s
      hardware.light    {"lux": float}                               every SENSOR_INTERVAL s
    """

    SENSOR_INTERVAL = 30.0
    EVENT = struct.Struct("llHHi")  # struct input_event on 64-bit: timeval, type, code, value
    EV_KEY = 1

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.sensor_task: asyncio.Task | None = None
        self.fds: dict[str, int] = {}
        self.classifier = PressClassifier()
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
            if code == KEYCODES["button"]:
                self._on_button(value == 1)
            for sensor in PRESENCE_TYPES:
                if code == KEYCODES[sensor]:
                    self.presence[sensor] = value == 1
                    self.last_motion_at = time.monotonic()
                    event_bus.publish("hardware.motion", {"motion": self.motion_active, "sensor": sensor})

    async def _publish_sensors(self) -> None:
        events = {"dht11": "hardware.climate", "light": "hardware.light"}
        while True:
            system_cfg = load_config().get("system", {})
            for device_type, event in events.items():
                if find_device(system_cfg, device_type):
                    reading = await read_sensor(device_type)
                    if reading:
                        event_bus.publish(event, dict(reading))
            await asyncio.sleep(self.SENSOR_INTERVAL)

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
        button = find_device(load_config().get("system", {}), "button") or {}
        action = button.get("actions", {}).get(press, "none")
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


# --- Sensors: IIO readings ---------------------------------------------------------------

def _find_iio_dir(driver_name: str) -> str | None:
    for d in glob.glob("/sys/bus/iio/devices/iio:device*"):
        try:
            with open(os.path.join(d, "name")) as f:
                if driver_name in f.read():
                    return d
        except OSError:
            continue
    return None


def _read_number(d: str, name: str) -> float:
    with open(os.path.join(d, name)) as f:
        return float(f.read())


def _read_dht11(d: str) -> dict:
    return {"temperature_c": round(_read_number(d, "in_temp_input") / 1000, 1),
            "humidity": round(_read_number(d, "in_humidityrelative_input") / 1000)}


def _read_bh1750(d: str) -> dict:
    lux = _read_number(d, "in_illuminance_raw") * _read_number(d, "in_illuminance_scale")
    return {"lux": round(lux, 1)}


# device type -> (IIO driver name, reader)
IIO_SENSORS = {"dht11": ("dht11", _read_dht11), "light": ("bh1750", _read_bh1750)}


def _read_sensor_blocking(device_type: str) -> dict | None:
    driver_name, reader = IIO_SENSORS[device_type]
    d = _find_iio_dir(driver_name)
    if not d:
        return None
    for attempt in range(3):  # the DHT11 regularly fails a read with EIO/ETIMEDOUT; retry
        try:
            return reader(d)
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(1.1)  # the DHT11 needs about 1 s between reads
    return None


# ponytail: readings are cached for 30 s per sensor, so the dashboard, every viewer and the
# event loop together cost at most one read per 30 s (the DHT11 allows about one per second).
SENSOR_CACHE_SECONDS = 30.0
_sensor_cache: dict[str, tuple[float, dict]] = {}


async def read_sensor(device_type: str) -> dict | None:
    """Latest reading of an IIO sensor ("dht11" or "light"), or None if it never answered.

    When a read fails, the last good reading is returned (the DHT11 misses reads regularly).
    """
    cached = _sensor_cache.get(device_type)
    if cached and time.monotonic() - cached[0] < SENSOR_CACHE_SECONDS:
        return cached[1]
    value = await asyncio.to_thread(_read_sensor_blocking, device_type)
    if value:
        _sensor_cache[device_type] = (time.monotonic(), value)
        return value
    return cached[1] if cached else None


gpio_inputs = GpioInputs()
