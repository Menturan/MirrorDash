# SPDX-License-Identifier: AGPL-3.0-only
"""Readings from the sensors and the fan on the GPIO header, through the kernel's own drivers.

The kernel runs the sensor protocols and shows the values as files: the DHT11 (temperature, humidity)
and the BH1750 (light) under /sys/bus/iio, the fan's speed step under /sys/class/thermal. read_sensor reads
one, at most once in SENSOR_CACHE_SECONDS, and gives the last good reading when a read fails.
"""

import asyncio
import glob
import logging
import os
import time

logger = logging.getLogger("mirrordash.core.hardware")


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
