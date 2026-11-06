# SPDX-License-Identifier: AGPL-3.0-only
"""A TV turned off and on with the screen, over HDMI-CEC (ARCHITECTURE #55).

Turning the HDMI output off stops the signal: a monitor goes to standby, but many TVs only show "No
signal". CEC is the TV's own off, as its remote's. A TV showing another input is left alone, and a
monitor without CEC never answers, so nothing happens there. cec-ctl (v4l-utils) talks to the
kernel's adapter (/dev/cec0, the video group). Not yet tried on a real TV (2026-11-06): the Pi 3's
monitor has no CEC; the messages went out with the HDMI output on and off."""

import glob
import logging
import re

from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.hardware")

TV = "0"  # CEC's logical address of the TV; never 15 (everyone): a soundbar or receiver would turn off too
NO_ADDRESS = "f.f.f.f"  # no screen, or a TV that drops its hotplug line in standby


async def _cec(device: str, *arguments: str, timeout: float = 5) -> str:
    """cec-ctl's output, as the playback device "MirrorDash" (claimed again each time, which follows a
    replugged screen); with no logical address left it still may wake the TV, from Unregistered."""
    code, output, error = await run_command("cec-ctl", "-d", device, "-s", "--playback", "-o", "MirrorDash",
                                            "--allow-unreg-fallback", *arguments, timeout=timeout)
    if code != 0:
        logger.info(f"cec-ctl {' '.join(arguments)} on {device}: {error.strip() or code}")
    return output


async def _adapter() -> tuple[str, str] | None:
    """The CEC adapter of the connected screen and its physical address: the first with one (a Pi 4/5 has
    two HDMI ports), else the first there is. None without CEC."""
    devices = sorted(glob.glob("/dev/cec*"))
    for device in devices:
        address = re.search(r"Physical Address\s*:\s*(\S+)", (await run_command("cec-ctl", "-d", device))[1])
        if address and address.group(1) != NO_ADDRESS:
            return device, address.group(1)
    return (devices[0], NO_ADDRESS) if devices else None


async def _active_sources(device: str) -> set[str]:
    """The physical addresses that say they are on the TV now (a broadcast question, a second for answers)."""
    output = await _cec(device, "-t", "15", "--request-active-source", "-W", "--monitor-time", "1")
    # Only an <Active Source> answer: not the question itself (REQUEST_ACTIVE_SOURCE, which cec-ctl prints
    # too) nor another device's address that happens to follow it
    return set(re.findall(r"(?<!_)ACTIVE_SOURCE \(0x82\):\s*phys-addr:\s*(\S+)", output))


async def set_tv_power(on: bool) -> None:
    """Turn the TV off or on with the screen, unless it shows another input: off only when the mirror is
    on the TV or nothing says it is, on (and switched to the mirror) only when the TV is off or shows it."""
    adapter = await _adapter()
    if not adapter:
        return
    device, own = adapter
    if own == NO_ADDRESS:  # a TV in standby that dropped its hotplug line can still be woken, nothing more
        if on:
            await _cec(device, "-t", TV, "--image-view-on")
        return
    if "Tx, OK" not in await _cec(device, "-t", TV, "--poll"):  # no TV on the bus: a monitor, 0.7 s on a Pi 3
        return
    others = await _active_sources(device) - {own}
    if not on:
        if others:
            logger.info(f"The TV shows another input ({', '.join(others)}): left on")
            return
        await _cec(device, "-t", TV, "--standby")
        logger.info("Asked the TV to turn off (HDMI-CEC)")
        return
    power = await _cec(device, "-t", TV, "--give-device-power-status")
    if others and re.search(r"pwr-state:\s*on", power):
        logger.info(f"The TV is on and shows another input ({', '.join(others)}): not switched")
        return
    await _cec(device, "-t", TV, "--image-view-on")
    await _cec(device, "-t", "15", "--active-source", f"phys-addr={own}")
    logger.info("Asked the TV to turn on and show the mirror (HDMI-CEC)")
