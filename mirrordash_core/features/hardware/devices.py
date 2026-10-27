# SPDX-License-Identifier: AGPL-3.0-only
"""Sensors and inputs on the GPIO header, all through Raspberry Pi kernel drivers.

The app's Python (uv, 3.14) has no GPIO library, and lgpio can't be built on the device, so
every device is a device-tree overlay instead (written to config.txt by the root helper
/usr/local/bin/mirrordash-gpio-overlays; the firmware applies them at the next boot):

- gpio-key (buttons, PIR, mmWave): the kernel debounces the pin and exposes it as an input
  device, read here as raw evdev events.
- dht11, i2c-sensor (BH1750): the kernel does the sensor protocol and exposes the readings
  under /sys/bus/iio.
- gpio-fan, pwm-gpio-fan: the kernel's thermal framework switches the fan by CPU temperature;
  we only read its state from /sys/class/thermal.

The devices are a list in config (system.devices), at most one of each type:
  {"type": "button", "pin": 17, "actions": {...}, "long_press": 1.5}, {"type": "light", "address": "0x23"}, ...
Up to four buttons, as the types button, button_2, button_3 and button_4.
"""

import asyncio
import glob
import logging
import os
import struct
import time

from mirrordash_core.config import load_config
from mirrordash_core.event_bus import event_bus
from mirrordash_core.host import run_command, run_in_background

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
BUTTON_WIRING = "Connect the button between the GPIO and a GND pin."

DEVICE_TYPES = {
    "button": {"label": "Push button", "pin": True, "wiring": BUTTON_WIRING},
    "button_2": {"label": "Push button 2", "pin": True, "wiring": BUTTON_WIRING},
    "button_3": {"label": "Push button 3", "pin": True, "wiring": BUTTON_WIRING},
    "button_4": {"label": "Push button 4", "pin": True, "wiring": BUTTON_WIRING},
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
    "fan": {"label": "Fan, on/off (2 or 3 wires)", "pin": True, "temperature": True,
            "wiring": "Never power the fan from the GPIO itself. The GPIO switches a transistor or "
                      "logic-level MOSFET between the fan's black wire and GND; the red wire goes to 5V, "
                      "and a diode across the fan protects the transistor. Leave a yellow (speed) wire "
                      "unconnected. Turns on at the temperature below and off again 5 °C lower."},
    "pwm_fan": {"label": "PWM fan with speed control (4 wires)", "pin": True, "temperature": True,
                "wiring": "The blue (PWM) wire to the GPIO, black to GND, red to the voltage the fan "
                          "needs (most 4-pin fans are 12V, some are 5V), yellow (speed) unconnected. "
                          "Starts slowly at the temperature below and speeds up in steps "
                          "(+5, +12.5 and +20 °C)."},
}

FAN_TYPES = ("fan", "pwm_fan")

FAN_TEMPERATURE_RANGE = (40, 80)  # °C; the Pi throttles at 80

BUTTON_TYPES = ("button", "button_2", "button_3", "button_4")

# KEY_PROG1..3 and KEY_MACRO1..3: keys the kiosk ignores
KEYCODES = {"button": 148, "pir": 149, "mmwave": 150, "button_2": 656, "button_3": 657, "button_4": 658}

PRESENCE_TYPES = ("pir", "mmwave")

BUTTON_ACTIONS = {
    "none": "Do nothing",
    "wake": "Wake the screen (for the screen timeout)",
    "toggle_display": "Turn screen on/off",
    "restart": "Restart MirrorDash",
    "reboot": "Restart the mirror",
    "shutdown": "Shut down the mirror",
}

PRESS_TYPES = ("single", "double", "triple", "long")

LONG_PRESS_CHOICES = (1.0, 1.5, 2.0, 3.0)  # seconds a button is held for a long press

DEFAULT_LONG_PRESS = 1.5


def get_devices(system_cfg: dict) -> list[dict]:
    return system_cfg.get("devices", [])


def find_device(system_cfg: dict, device_type: str) -> dict | None:
    return next((device for device in get_devices(system_cfg) if device.get("type") == device_type), None)


def validate_devices(devices: list[dict]) -> str | None:
    """Error message if the list can't be wired like this, else None."""
    device_label_by_pin: dict[int, str] = {}
    types = [device.get("type") for device in devices]
    for device in devices:
        spec = DEVICE_TYPES.get(device.get("type"))
        if not spec:
            return f"Unknown device type: {device.get('type')}."
        if types.count(device["type"]) > 1:
            return f"Only one {spec['label']} can be connected."
        if spec.get("pin"):
            pin = device.get("pin")
            if pin not in GPIO_HEADER_PINS:
                return f"GPIO {pin} can't be used for the {spec['label']}."
            if pin in device_label_by_pin:
                return f"The {device_label_by_pin[pin]} already uses GPIO {pin}."
            device_label_by_pin[pin] = spec["label"]
        elif device.get("address") not in spec["addresses"]:
            return f"The {spec['label']} needs address {' or '.join(spec['addresses'])}."
    if all(fan_type in types for fan_type in FAN_TYPES):
        return "Only one fan can be connected."
    lowest, highest = FAN_TEMPERATURE_RANGE
    for fan in (device for device in devices if device["type"] in FAN_TYPES):
        if not isinstance(fan.get("temperature"), int) or not lowest <= fan["temperature"] <= highest:
            return f"The fan's temperature must be between {lowest} and {highest} °C."
    if any("addresses" in DEVICE_TYPES[device_type] for device_type in types):
        for pin in I2C_PINS:
            if pin in device_label_by_pin:
                return f"GPIO {pin} is needed for I²C (the light sensor); the {device_label_by_pin[pin]} must use another GPIO."
    return None


def overlay_args(devices: list[dict]) -> list[str]:
    """Helper arguments, e.g. ["button:17", "light:0x23", "fan:14:60"] (fans: GPIO:°C)."""
    def where_and_how(device):
        if device["type"] in FAN_TYPES:
            return f"{device['pin']}:{device['temperature']}"
        return device["pin"] if "pin" in device else device["address"]
    return [f"{device['type']}:{where_and_how(device)}" for device in devices]


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
    exit_code, _, error = await run_command("sudo", "-n", GPIO_HELPER, *args, timeout=30)
    if exit_code != 0:
        logger.error(f"GPIO overlay helper failed ({exit_code}): {error}")
        if "unknown device type" in error:  # e.g. button_2 on an image from before it existed
            return "This mirror's OS image is too old for this device. Flash the latest MirrorDash OS image."
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
    elif action == "restart":
        await run_restart()
    elif action == "reboot":
        await reboot_system(delay_sec=1.0)
    elif action == "shutdown":
        await poweroff_system(delay_sec=1.0)


def _find_iio_dir(driver_name: str) -> str | None:
    """The sysfs folder of the IIO sensor this driver runs, or None."""
    for sensor_dir in glob.glob("/sys/bus/iio/devices/iio:device*"):
        try:
            with open(os.path.join(sensor_dir, "name")) as f:
                if driver_name in f.read():
                    return sensor_dir
        except OSError:
            continue
    return None


def _read_number(folder: str, name: str) -> float:
    with open(os.path.join(folder, name)) as f:
        return float(f.read())


def _read_dht11(sensor_dir: str) -> dict:
    # The kernel gives thousandths of a degree and of a percent
    return {"temperature_c": round(_read_number(sensor_dir, "in_temp_input") / 1000, 1),
            "humidity": round(_read_number(sensor_dir, "in_humidityrelative_input") / 1000)}


def _read_bh1750(sensor_dir: str) -> dict:
    lux = _read_number(sensor_dir, "in_illuminance_raw") * _read_number(sensor_dir, "in_illuminance_scale")
    return {"lux": round(lux, 1)}

# device type -> (IIO driver name, reader)
IIO_SENSORS = {"dht11": ("dht11", _read_dht11), "light": ("bh1750", _read_bh1750)}


def _read_sensor_blocking(device_type: str) -> dict | None:
    driver_name, reader = IIO_SENSORS[device_type]
    sensor_dir = _find_iio_dir(driver_name)
    if not sensor_dir:
        return None
    for attempt in range(3):  # the DHT11 regularly fails a read with EIO/ETIMEDOUT; retry
        try:
            return reader(sensor_dir)
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(1.1)  # the DHT11 needs about 1 s between reads
    return None

# ponytail: readings are cached for 30 s per sensor, so the dashboard, every viewer and the
# event loop together cost at most one read per 30 s (the DHT11 allows about one per second).
SENSOR_CACHE_SECONDS = 30.0


_sensor_cache: dict[str, tuple[float, dict]] = {}  # device type -> (when, the reading)


async def read_sensor(device_type: str) -> dict | None:
    """Latest reading of an IIO sensor ("dht11" or "light"), or None if it never answered.

    When a read fails, the last good reading is returned (the DHT11 misses reads regularly).
    """
    read_at, last_reading = _sensor_cache.get(device_type, (None, None))
    if read_at is not None and time.monotonic() - read_at < SENSOR_CACHE_SECONDS:
        return last_reading
    reading = await asyncio.to_thread(_read_sensor_blocking, device_type)
    if reading:
        _sensor_cache[device_type] = (time.monotonic(), reading)
        return reading
    return last_reading


def read_fan_state() -> dict | None:
    """{"level", "max_level", "cpu_temperature_c"} of the gpio-fan / pwm-fan, or None if not loaded."""
    for cooling_device in glob.glob("/sys/class/thermal/cooling_device*"):
        try:
            with open(os.path.join(cooling_device, "type")) as f:
                if f.read().strip() not in ("gpio-fan", "pwm-fan"):
                    continue
            state = {"level": int(_read_number(cooling_device, "cur_state")),
                     "max_level": int(_read_number(cooling_device, "max_state"))}
            state["cpu_temperature_c"] = round(_read_number("/sys/class/thermal/thermal_zone0", "temp") / 1000, 1)
            return state
        except (OSError, ValueError):
            continue
    return None


gpio_inputs = GpioInputs()
