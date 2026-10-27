# Licensed under the PolyForm Noncommercial License 1.0.0.

"""The machine the core runs on: running its programs, restarting, rebooting, and the system settings
that need sudo (time zone, Wi-Fi country, the pi user's password)."""

import asyncio
import logging
import os
import re
import signal
import zoneinfo
from collections.abc import Coroutine

logger = logging.getLogger("mirrordash.core.host")

# Exit codes run_command gives when the program never got to answer, the same the shell uses
EXIT_CODE_PROGRAM_NOT_FOUND = 127
EXIT_CODE_TIMED_OUT = 124


async def run_command(*command: str, timeout: float | None = 10, input_text: str | None = None) -> tuple[int, str, str]:
    """Run a program and wait for it: (exit_code, output, error_output). Never raises: a program that
    isn't installed gives 127 and one still running after `timeout` seconds is killed and gives 124
    (timeout None: wait as long as it takes).

    The program gets this process's environment (wlr-randr needs WAYLAND_DISPLAY); for one that must
    not see the server's secrets, use venv.run, which strips it."""
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({"stdin": asyncio.subprocess.PIPE} if input_text is not None else {}))
    except OSError as error:
        return EXIT_CODE_PROGRAM_NOT_FOUND, "", f"{command[0]}: {error.strerror}"
    try:
        output, error_output = await asyncio.wait_for(
            process.communicate(input=input_text.encode() if input_text is not None else None), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return EXIT_CODE_TIMED_OUT, "", f"{command[0]} took more than {timeout:.0f} s"
    return process.returncode, output.decode(errors="replace"), error_output.decode(errors="replace")


# asyncio keeps only a weak reference to a task: one nobody holds can be garbage collected before it
# finishes (the asyncio docs' warning on create_task). The background tasks are held here until done.
_background_tasks: set[asyncio.Task] = set()


def run_in_background(coroutine: Coroutine, name: str | None = None) -> asyncio.Task:
    """Start a coroutine without waiting for it (a restart after the HTTP answer, a slow setting),
    and keep it alive until it finishes."""
    task = asyncio.create_task(coroutine, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def run_restart() -> None:
    """Restart the app: SIGTERM to this process lets uvicorn stop cleanly, and systemd starts it again."""
    logger.info("Restarting application via SIGTERM...")
    await asyncio.sleep(1)  # Allow HTTP responses to flush
    os.kill(os.getpid(), signal.SIGTERM)


async def _schedule_power_command(command: str, delay_sec: float) -> None:
    """Run `sudo -n <command>` (reboot/poweroff) after a delay, so the HTTP response gets out first."""
    logger.info(f"Scheduling system {command} in {delay_sec} seconds...")

    async def wait_then_run_power_command():
        await asyncio.sleep(delay_sec)
        logger.info(f"Executing sudo {command}...")
        exit_code, _, error_output = await run_command("sudo", "-n", command)
        if exit_code != 0:
            logger.error(f"sudo {command} failed: {error_output.strip()}")

    run_in_background(wait_then_run_power_command())


async def sudo_allowed(command: str) -> bool:
    """True if the sudoers allow-list lets this app run `command` without a password."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", "-l", command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        return await proc.wait() == 0
    except Exception:
        return False


async def reboot_system(delay_sec: float = 2.0) -> None:
    """Asynchronously trigger an OS-level reboot after a delay."""
    await _schedule_power_command("reboot", delay_sec)


async def poweroff_system(delay_sec: float = 2.0) -> None:
    """Asynchronously shut the mirror down after a delay."""
    await _schedule_power_command("poweroff", delay_sec)


def is_known_timezone(timezone: object) -> bool:
    """Whether this is a time zone name the system knows (Europe/Stockholm): the trust boundary for one
    that reaches timedatectl's command line and the modules' settings."""
    return isinstance(timezone, str) and timezone in zoneinfo.available_timezones()


async def apply_system_timezone(timezone: str) -> bool:
    """Set the system time zone with timedatectl, and the Wi-Fi country that goes with it."""
    if not is_known_timezone(timezone):
        logger.warning(f"Not setting the system timezone: {timezone!r} isn't a known time zone")
        return False
    logger.info(f"Applying system timezone: {timezone}")
    exit_code, _, error_output = await run_command("sudo", "-n", "timedatectl", "set-timezone", timezone, timeout=5)
    if exit_code != 0:
        logger.warning(f"Failed to set system timezone to {timezone}: {error_output.strip()}")
        return False
    logger.info(f"System timezone successfully set to {timezone}")
    try:  # its own step: a Wi-Fi country failure is not a time zone failure
        await apply_wifi_country(wifi_country_for(timezone))
    except Exception as e:
        logger.warning(f"Failed to set the Wi-Fi country for {timezone}: {e}")
    return True


# Each time zone's country code (tab separated: code, coordinates, zone name)
ZONE_TAB = "/usr/share/zoneinfo/zone.tab"
# The kernel's command line on the boot partition, where the Wi-Fi country is set at boot
KERNEL_COMMAND_LINE_FILE = "/boot/firmware/cmdline.txt"


def wifi_country_for(timezone: str) -> str | None:
    """The country a timezone belongs to (Europe/Stockholm -> SE), or None (UTC, unknown)."""
    try:
        with open(ZONE_TAB, encoding="utf-8") as f:
            for line in f:
                fields = line.split("\t")
                if len(fields) >= 3 and not line.startswith("#") and fields[2].strip() == timezone:
                    return fields[0]
    except OSError as e:
        logger.debug(f"Cannot read {ZONE_TAB}: {e}")
    return None


async def apply_wifi_country(country: str | None) -> bool:
    """Set the Wi-Fi country (which channels may be used) in cmdline.txt; it applies at the next start.
    Only written when it changes, so the boot partition isn't rewritten at every start."""
    if not country or not re.fullmatch(r"[A-Z]{2}", country):
        return False
    try:
        with open(KERNEL_COMMAND_LINE_FILE, encoding="utf-8") as f:
            if f" cfg80211.ieee80211_regdom={country}" in f" {f.read().strip()} ":
                return True
    except OSError:
        return False  # not a Pi (development machine)
    exit_code, _, error_output = await run_command("sudo", "-n", "/usr/local/bin/mirrordash-wifi-country", country, timeout=15)
    if exit_code != 0:
        logger.warning(f"Setting the Wi-Fi country to {country} failed: {error_output.strip()}")
        return False
    logger.info(f"Wi-Fi country set to {country} (from the time zone); applies at the next start")
    return True


# chpasswd -e for the pi user only: sudo allows this helper, never chpasswd itself
PI_PASSWORD_HELPER = "/usr/local/bin/mirrordash-pi-password"


async def apply_system_password_hash(password_hash: str) -> bool:
    """Apply a crypt hash (openssl passwd -6) as the password of user 'pi'."""
    logger.info("Applying system password hash...")
    exit_code, _, error_output = await run_command("sudo", "-n", PI_PASSWORD_HELPER, input_text=f"{password_hash}\n")
    if exit_code != 0:
        logger.error(f"chpasswd -e failed: {error_output.strip()}")
        return False
    logger.info("System password hash applied successfully.")
    return True
