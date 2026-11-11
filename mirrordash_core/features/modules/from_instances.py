# SPDX-License-Identifier: AGPL-3.0-only
"""What a module instance sends the core, checked like any other input from outside (client.py reads it):
its log lines, its public events and its GPIO requests. An instance's connection is who it is."""

import collections
import json
import logging
import os
import re
import socket
import struct
import time

from mirrordash_core.config import load_config
from mirrordash_core.features.hardware.gpio import free_pins, header_chip, request_lines
from mirrordash_core.features.modules.connection import LOG_BURST, Connection

logger = logging.getLogger("mirrordash.core.modules.client")

EVENT_NAME = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_.:-]{1,100}")  # a public event's: "<module>.<what>"
EVENT_LIMIT = 64 * 1024  # a public event's data, as JSON
LOG_WINDOW = 10  # seconds: an instance may send connection.LOG_BURST log lines in them
LOG_LINE_LIMIT = 4000  # characters of one log message from a module
LOG_NAME = re.compile(r"^[\w.-]{1,100}$")
GPIO_OPTIONS = ("output", "initial", "pull", "edge", "active_low", "debounce_ms")  # features/hardware/gpio.py request_lines


def is_over_rate_limit(recent_times: collections.deque, window_seconds: float, now: float) -> bool:
    """Whether one more would be too many: recent_times (a deque with a maxlen, the limit) is full and its
    oldest is still inside the window. The caller appends now when it lets one through."""
    is_full = len(recent_times) == recent_times.maxlen
    return is_full and now - recent_times[0] < window_seconds


def gpio_request_problem(pins, options: dict) -> str | None:
    """Why a module's GPIO request is malformed, or None. Whether the pins are free is checked after."""
    if not isinstance(pins, list) or not pins or len(set(pins)) != len(pins) or any(type(p) is not int for p in pins):
        return "pins must be a list of GPIO numbers"
    flags_are_booleans = all(type(options.get(flag, False)) is bool for flag in ("output", "active_low"))
    initial_is_a_level = options.get("initial", 0) in (0, 1)
    debounce_is_a_number = type(options.get("debounce_ms", 0)) is int
    if not (flags_are_booleans and initial_is_a_level and debounce_is_a_number):
        return "output and active_low are true or false, initial 0 or 1, debounce_ms a number"
    return None


def log_line(message: dict, conn: Connection) -> None:
    """A log line from the host: under the instance's own name (or the host's), at most LOG_BURST in
    LOG_WINDOW seconds, so it can't pass for the core's line or flood the core's log."""
    now = time.monotonic()
    if is_over_rate_limit(conn.logged, LOG_WINDOW, now):
        conn.dropped += 1  # a flood would rotate the core's own lines out of its log, and wear the card
        return
    conn.logged.append(now)
    name = str(message.get("name", ""))
    if conn.instance:
        name = f"mirrordash.modules.{conn.instance}"
    elif not (LOG_NAME.match(name) and name.startswith("mirrordash.modules")):
        name = "mirrordash.modules.host"
    level = message.get("level") if isinstance(message.get("level"), int) else logging.INFO
    level = min(max(level, logging.DEBUG), logging.CRITICAL)
    # Each further line (a traceback) carries the name too: a line of its own can't pass for the core's
    text = str(message.get("text", ""))[:LOG_LINE_LIMIT].replace("\r", "").replace("\n", f"\n    {name}: ")
    if conn.dropped:
        logging.getLogger(name).warning(f"{conn.dropped} log lines left out: more than {LOG_BURST} in {LOG_WINDOW} s")
        conn.dropped = 0
    logging.getLogger(name).log(level, text)


def public_event(message: dict, conn: Connection) -> dict | None:
    """A module's public event as the message every instance gets (its own too, never the core's own
    subscribers), or None for one that is dropped: named after its module, so it can't pass for the
    hardware's or another module's."""
    name, data, now = message.get("name"), message.get("data"), time.monotonic()
    if is_over_rate_limit(conn.published, 1, now):
        return None  # dropped without a log line, which a flood would flood too
    conn.published.append(now)
    is_named_after_its_module = isinstance(name, str) and name.startswith(conn.module + ".") and EVENT_NAME.fullmatch(name)
    if not is_named_after_its_module:
        logger.warning(f"{conn.instance}: a public event's name must start with '{conn.module}.': {str(name)[:100]!r}")
    elif len(json.dumps(data)) > EVENT_LIMIT:
        logger.warning(f"{conn.instance}: public event {name} is over {EVENT_LIMIT // 1024} kB, dropped")
    else:
        return {"t": "event", "name": name, "data": data}
    return None


def hand_over_gpio_lines(message: dict, conn: Connection) -> str | None:
    """An instance's GPIO request (features/hardware/gpio.py): its lines' file descriptor sent to its
    process over its own descriptor socket, or why not. Only free header pins, whatever it asks."""
    instance_id, pins = conn.instance, message.get("pins")
    options = {option: message[option] for option in GPIO_OPTIONS if option in message}
    if problem := gpio_request_problem(pins, options):
        return problem
    if taken := sorted(set(pins) - free_pins(load_config())):
        return f"GPIO {', '.join(map(str, taken))} isn't free for modules (not on the header, or a device in the Hardware tab uses it)"
    if not (chip := header_chip()):
        return "this machine has no GPIO header"
    try:
        fd = request_lines(chip, pins, f"mirrordash:{instance_id}", **options)
    except (OSError, ValueError) as e:
        return str(e)
    try:
        socket.send_fds(conn.descriptor_socket, [struct.pack("<Q", message["id"])], [fd])
    except OSError as e:
        return f"couldn't hand the pins over: {e}"
    finally:
        os.close(fd)  # the instance's copy holds the lines now
    logger.info(f"{instance_id} has GPIO {', '.join(map(str, pins))}")
    return None
