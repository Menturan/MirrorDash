# SPDX-License-Identifier: AGPL-3.0-only
"""The screen and the sound: resolution, rotation, brightness, volume and power.

The mirror runs Wayland (labwc), where wlr-randr sets the screen; the xrandr and xset steps are for a
development machine on X11. Every step tries its tools in turn and gives up quietly when none is there."""

import asyncio
import glob
import logging
import re

from mirrordash_core.host import run_command

logger = logging.getLogger("mirrordash.core.hardware")

# What a headless development machine offers, where no tool lists the screen's own modes
COMMON_RESOLUTIONS = ["1920x1080", "1280x720", "1024x768", "800x600", "1080x1920", "720x1280"]
# The ALSA mixer controls tried in turn when there is no PipeWire/PulseAudio: names differ per sound card
ALSA_VOLUME_CONTROLS = ["Master", "Speaker", "HDMI", "Headphone", "Audio Out"]
# The admin page's rotation names as wlr-randr's --transform values
WAYLAND_TRANSFORM_FOR_ROTATION = {"normal": "normal", "left": "90", "inverted": "180", "right": "270"}
# At boot the settings are applied while the compositor may still be starting: wait this long for it
SECONDS_TO_WAIT_FOR_WAYLAND = 60


async def get_available_resolutions() -> list[str]:
    """The screen's resolutions, largest first, after "auto"; common ones when no screen tool answers."""
    resolutions = set()
    for screen_tool in ("xrandr", "wlr-randr"):
        exit_code, output, _ = await run_command(screen_tool)
        if exit_code == 0:
            resolutions |= set(re.findall(r"^\s*(\d+x\d+)", output, re.MULTILINE))
    if not resolutions:
        return ["auto", *COMMON_RESOLUTIONS]

    def pixel_count(resolution: str) -> int:
        width, height = resolution.split("x")
        return int(width) * int(height)

    return ["auto", *sorted(resolutions, key=pixel_count, reverse=True)]


# False once neither a backlight nor DDC/CI took the brightness (the admin page then says so).
# None: not tried yet since the app started.
brightness_supported: bool | None = None


async def apply_brightness(brightness: int) -> bool:
    """Set the screen brightness (10-100 %). A DSI screen has a backlight device; an HDMI screen
    can only be asked over DDC/CI, which most monitors understand and most TVs don't."""
    global brightness_supported
    brightness_supported = await _set_backlight(brightness) or await _set_ddc_brightness(brightness)
    return brightness_supported


async def _set_backlight(brightness: int) -> bool:
    brightness_files = glob.glob("/sys/class/backlight/*/brightness")
    max_brightness_files = glob.glob("/sys/class/backlight/*/max_brightness")
    if not (brightness_files and max_brightness_files):
        return False
    try:
        with open(max_brightness_files[0]) as f:
            max_value = int(f.read().strip())
        target_value = int((brightness / 100.0) * max_value)
        # Writable for the video group (udev: 90-mirrordash-backlight.rules), so no sudo
        with open(brightness_files[0], "w") as f:
            f.write(f"{target_value}\n")
    except Exception as e:
        logger.warning(f"Failed to write to backlight device {brightness_files[0]}: {e}")
        return False
    logger.info(f"Applied hardware brightness {target_value}/{max_value} via {brightness_files[0]}")
    return True


async def _set_ddc_brightness(brightness: int) -> bool:
    # ponytail: VCP 0x10 is written as a percentage; nearly every monitor's maximum is 100. One
    # with another maximum would need `ddcutil getvcp 10` first to scale the value.
    # --noverify: no read-back, so a slider step is quick and a screen that reads back late or rounded
    # isn't taken for one without DDC/CI. ddcutil 2.1/2.2 verifies anyway and logs "Both --verify and
    # --noverify specified" (its own bug, fixed upstream after 2.2).
    exit_code, _, error_output = await run_command(
        "sudo", "-n", "ddcutil", "--noverify", "setvcp", "10", str(brightness), timeout=10)
    if exit_code != 0:
        reason = (error_output.strip().splitlines() or ["no answer"])[-1]  # ddcutil's verdict comes last
        logger.warning(f"The screen doesn't take brightness over HDMI (DDC/CI): {reason}")
        return False
    logger.info(f"Applied HDMI brightness {brightness}% via DDC/CI")
    return True


async def apply_system_settings(rotation: str, resolution: str, brightness: int, volume: int) -> None:
    """Apply the screen's brightness, the sound's volume and the screen's rotation and resolution."""
    logger.info(f"Applying system settings: rotation={rotation}, resolution={resolution}, brightness={brightness}%, volume={volume}%")
    await apply_brightness(brightness)
    await _apply_volume(volume)
    await _apply_x11_rotation_and_resolution(rotation, resolution)
    await _apply_wayland_rotation_and_resolution(rotation, resolution)


async def _apply_volume(volume: int) -> None:
    """PipeWire/PulseAudio's default output, or else the first ALSA control the sound card has."""
    if (await run_command("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%"))[0] == 0:
        logger.info(f"Applied system volume {volume}% via pactl")
        return
    for control in ALSA_VOLUME_CONTROLS:
        if (await run_command("amixer", "sset", control, f"{volume}%"))[0] == 0:
            logger.info(f"Applied system volume {volume}% via amixer control '{control}'")
            return
    logger.debug("No sound tool took the volume")


def _mode_arguments(resolution: str) -> list[str]:
    """xrandr's and wlr-randr's resolution argument; none for "auto" (the screen's preferred mode)."""
    return ["--mode", resolution] if resolution and resolution != "auto" else []


async def _first_x11_screen() -> str | None:
    """The first connected output xrandr lists (e.g. HDMI-1), or None without X11."""
    exit_code, output, _ = await run_command("xrandr")
    screens = re.findall(r"(\S+)\s+connected", output) if exit_code == 0 else []
    return screens[0] if screens else None


async def _first_wayland_screen() -> str | None:
    """The first output wlr-randr lists (its lines that don't start with a space), or None without Wayland."""
    exit_code, output, _ = await run_command("wlr-randr")
    screens = re.findall(r"^(\S+)\s+", output, re.MULTILINE) if exit_code == 0 else []
    return screens[0] if screens else None


async def _apply_x11_rotation_and_resolution(rotation: str, resolution: str) -> None:
    screen = await _first_x11_screen()
    if not screen:
        return
    await run_command("xrandr", "--output", screen, "--rotate", rotation, *_mode_arguments(resolution))
    logger.info(f"Applied xrandr display configuration for {screen}: rotate={rotation}, resolution={resolution}")


async def _apply_wayland_rotation_and_resolution(rotation: str, resolution: str) -> None:
    transform = WAYLAND_TRANSFORM_FOR_ROTATION.get(rotation, "normal")
    for attempt in range(1, SECONDS_TO_WAIT_FOR_WAYLAND + 1):
        screen = await _first_wayland_screen()
        if screen:
            await run_command("wlr-randr", "--output", screen, "--transform", transform, *_mode_arguments(resolution))
            logger.info(f"Applied wlr-randr display configuration for {screen}: transform={transform}, resolution={resolution}")
            return
        logger.debug(f"No Wayland screen yet, attempt {attempt}/{SECONDS_TO_WAIT_FOR_WAYLAND} (the compositor may still be starting)")
        if attempt < SECONDS_TO_WAIT_FOR_WAYLAND:
            await asyncio.sleep(1)


async def set_screen_power(on: bool) -> bool:
    """Turn the screen on or off with the first tool that works: wlr-randr (the mirror's Wayland),
    xset or xrandr (X11), or vcgencmd (a Pi's older firmware)."""
    logger.info(f"Setting screen power state: {'on' if on else 'off'}")

    wayland_screen = await _first_wayland_screen()
    if wayland_screen:
        switch = "--on" if on else "--off"
        exit_code, _, error_output = await run_command("wlr-randr", "--output", wayland_screen, switch)
        if exit_code != 0:
            logger.warning(f"wlr-randr couldn't set {wayland_screen} {switch}: {error_output.strip()}")
            return False
        logger.info(f"Set wlr-randr output {wayland_screen} to {switch}")
        return True

    if (await run_command("xset", "dpms", "force", "on" if on else "off"))[0] == 0:
        logger.info(f"Turned screen {'on' if on else 'off'} via xset dpms")
        return True

    x11_screen = await _first_x11_screen()
    if x11_screen:
        switch = "--auto" if on else "--off"
        await run_command("xrandr", "--output", x11_screen, switch)
        logger.info(f"Set xrandr output {x11_screen} to {switch}")
        return True

    if (await run_command("vcgencmd", "display_power", "1" if on else "0"))[0] == 0:
        logger.info(f"Set Raspberry Pi screen power to {'on' if on else 'off'} via vcgencmd")
        return True
    return False
