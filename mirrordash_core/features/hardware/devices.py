# SPDX-License-Identifier: AGPL-3.0-only
"""Sensors and inputs on the GPIO header, all through Raspberry Pi kernel drivers.

The app's Python (uv, 3.14) has no GPIO library, and lgpio can't be built on the device, so
every device is a device-tree overlay instead (written to config.txt by the root helper
/usr/local/bin/mirrordash-gpio-overlays; the firmware applies them at the next boot):

- gpio-key (buttons, PIR, mmWave): the kernel debounces the pin and exposes it as an input
  device, read as raw evdev events (inputs.py).
- dht11, i2c-sensor (BH1750): the kernel does the sensor protocol and exposes the readings
  under /sys/bus/iio.
- gpio-fan, pwm-gpio-fan: the kernel's thermal framework switches the fan by CPU temperature;
  we only read its state from /sys/class/thermal.

The devices are a list in config (system.devices), at most one of each type:
  {"type": "button", "pin": 17, "actions": {...}, "long_press": 1.5}, {"type": "light", "address": "0x23"}, ...
Up to four buttons, as the types button, button_2, button_3 and button_4.

This file is that list: the device types, checking a list, and writing its overlays. Beside it:
  inputs.py   buttons and presence sensors: presses, and what they do
  sensors.py  readings from the DHT11, the BH1750 and the fan
  gpio.py     GPIO lines handed to modules
"""

import logging
import os

from mirrordash_core.host import run_command

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
    "privacy": "Privacy mode on/off",
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










