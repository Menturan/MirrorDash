# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
import re
import glob

logger = logging.getLogger("mirrordash.core.system.display")

async def get_available_resolutions() -> list[str]:
    """Scans xrandr or wlr-randr for display resolutions, falling back to defaults if headless."""
    resolutions = {"auto"}

    # Try xrandr
    try:
        proc = await asyncio.create_subprocess_exec(
            "xrandr",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            lines = stdout.decode("utf-8", errors="ignore").splitlines()
            for line in lines:
                match = re.search(r"^\s*(\d+x\d+)", line)
                if match:
                    resolutions.add(match.group(1))
    except Exception as e:
        logger.debug(f"Failed to query resolutions via xrandr: {e}")

    # Try wlr-randr
    try:
        proc = await asyncio.create_subprocess_exec(
            "wlr-randr",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            lines = stdout.decode("utf-8", errors="ignore").splitlines()
            for line in lines:
                match = re.search(r"^\s*(\d+x\d+)", line)
                if match:
                    resolutions.add(match.group(1))
    except Exception as e:
        logger.debug(f"Failed to query resolutions via wlr-randr: {e}")

    # If no resolutions found (e.g. headless dev machine), return standard common resolutions
    if len(resolutions) <= 1:
        return ["auto", "1920x1080", "1280x720", "1024x768", "800x600", "1080x1920", "720x1280"]

    # Sort resolutions numerically by total pixels
    res_list = list(resolutions)
    res_list.remove("auto")

    def res_key(r):
        try:
            w, h = map(int, r.split('x'))
            return w * h
        except Exception:
            return 0

    res_list.sort(key=res_key, reverse=True)
    return ["auto"] + res_list

async def apply_system_settings(rotation: str, resolution: str, brightness: int, volume: int) -> None:
    """Asynchronously applies screen rotation, screen mode/resolution, display brightness, and system audio volume."""
    logger.info(f"Applying system settings: rotation={rotation}, resolution={resolution}, brightness={brightness}%, volume={volume}%")

    # A. Apply Brightness
    # 1. Try Linux /sys/class/backlight
    backlight_paths = glob.glob("/sys/class/backlight/*/brightness")
    max_backlight_paths = glob.glob("/sys/class/backlight/*/max_brightness")
    applied_hardware = False
    if backlight_paths and max_backlight_paths:
        try:
            # Read max brightness
            with open(max_backlight_paths[0], "r") as f:
                max_val = int(f.read().strip())
            # Calculate target value (0 to max_val based on brightness 0-100)
            target_val = int((brightness / 100.0) * max_val)
            # Write target value using sudo tee
            proc = await asyncio.create_subprocess_exec(
                "sudo", "tee", backlight_paths[0],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate(input=f"{target_val}\n".encode())
            logger.info(f"Applied hardware brightness {target_val}/{max_val} via {backlight_paths[0]}")
            applied_hardware = True
        except Exception as e:
            logger.warning(f"Failed to write to backlight device: {e}")

    # 2. Try xrandr software brightness (fallback or X11 utility)
    if not applied_hardware:
        try:
            proc = await asyncio.create_subprocess_exec(
                "xrandr", "--verbose",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                displays = re.findall(r"(\S+)\s+connected", stdout.decode("utf-8", errors="ignore"))
                for display in displays:
                    software_val = max(0.1, brightness / 100.0)
                    proc_brightness = await asyncio.create_subprocess_exec(
                        "xrandr", "--output", display, "--brightness", f"{software_val:.2f}",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await proc_brightness.wait()
                    logger.info(f"Applied xrandr software brightness {software_val:.2f} for {display}")
        except Exception as e:
            logger.debug(f"xrandr brightness setting skipped or failed: {e}")

    # B. Apply Volume
    # Try PulseAudio / PipeWire first via pactl
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{volume}%",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
        if proc.returncode == 0:
            logger.info(f"Applied system volume {volume}% via pactl")
        else:
            # Try ALSA fallback via amixer
            alsa_controls = ["Master", "Speaker", "HDMI", "Headphone", "Audio Out"]
            for ctrl in alsa_controls:
                proc_alsa = await asyncio.create_subprocess_exec(
                    "amixer", "sset", ctrl, f"{volume}%",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc_alsa.wait()
                if proc_alsa.returncode == 0:
                    logger.info(f"Applied system volume {volume}% via amixer control '{ctrl}'")
                    break
    except Exception as e:
        logger.debug(f"Audio volume setting failed or skipped: {e}")

    # C. Apply Rotation & Resolution
    # On X11 (xrandr)
    try:
        proc = await asyncio.create_subprocess_exec(
            "xrandr",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            displays = re.findall(r"(\S+)\s+connected", stdout.decode("utf-8", errors="ignore"))
            if displays:
                display = displays[0]
                res_args = []
                if resolution and resolution != "auto":
                    res_args = ["--mode", resolution]

                cmd = ["xrandr", "--output", display, "--rotate", rotation] + res_args
                proc_rot = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc_rot.wait()
                logger.info(f"Applied xrandr display configuration for {display}: rotate={rotation}, resolution={resolution}")
    except Exception as e:
        logger.debug(f"xrandr display settings skipped or failed: {e}")

    # On Wayland (wlr-randr)
    try:
        wlr_rot_map = {
            "normal": "normal",
            "left": "90",
            "inverted": "180",
            "right": "270"
        }
        wlr_rot = wlr_rot_map.get(rotation, "normal")

        # Retry query up to 60 times to handle compositor startup timing
        for attempt in range(60):
            proc = await asyncio.create_subprocess_exec(
                "wlr-randr",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                displays = re.findall(r"^(\S+)\s+", stdout.decode("utf-8", errors="ignore"), re.MULTILINE)
                if displays:
                    display = displays[0]
                    res_args = []
                    if resolution and resolution != "auto":
                        res_args = ["--mode", resolution]

                    cmd = ["wlr-randr", "--output", display, "--transform", wlr_rot] + res_args
                    proc_wlr = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await proc_wlr.wait()
                    logger.info(f"Applied wlr-randr display configuration for {display}: transform={wlr_rot}, resolution={resolution}")
                    break
            else:
                logger.debug(f"wlr-randr query failed on attempt {attempt + 1}/60 (Wayland might not be ready yet)")
                if attempt < 59:
                    await asyncio.sleep(1)
    except Exception as e:
        logger.debug(f"wlr-randr display settings skipped or failed: {e}")

async def set_screen_power(on: bool) -> None:
    """Asynchronously controls display power state (ON or OFF) via xrandr/xset, wlr-randr, or vcgencmd."""
    state_str = "on" if on else "off"
    logger.info(f"Setting screen power state: {state_str}")

    # 1. Try wlr-randr (Wayland)
    try:
        proc = await asyncio.create_subprocess_exec(
            "wlr-randr", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            displays = re.findall(r"^(\S+)\s+", stdout.decode("utf-8", errors="ignore"), re.MULTILINE)
            if displays:
                display = displays[0]
                action = "--on" if on else "--off"
                cmd = ["wlr-randr", "--output", display, action]
                proc_w = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc_w.wait()
                logger.info(f"Set wlr-randr output {display} to {action}")
                return
    except Exception as e:
        logger.debug(f"wlr-randr power control failed or skipped: {e}")

    # 2. Try xset DPMS (highly compatible X11 method)
    try:
        cmd = ["xset", "dpms", "force", state_str]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
        if proc.returncode == 0:
            logger.info(f"Turned screen {state_str} via xset dpms")
            return
    except Exception as e:
        logger.debug(f"xset dpms control failed or skipped: {e}")

    # 3. Try xrandr output display off/on (X11)
    try:
        proc = await asyncio.create_subprocess_exec(
            "xrandr", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            displays = re.findall(r"(\S+)\s+connected", stdout.decode("utf-8", errors="ignore"))
            if displays:
                display = displays[0]
                action = "--auto" if on else "--off"
                cmd = ["xrandr", "--output", display, action]
                proc_x = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await proc_x.wait()
                logger.info(f"Set xrandr output {display} to {action}")
                return
    except Exception as e:
        logger.debug(f"xrandr power control failed or skipped: {e}")

    # 4. Try vcgencmd (Raspberry Pi legacy firmware)
    try:
        val = "1" if on else "0"
        proc = await asyncio.create_subprocess_exec(
            "vcgencmd", "display_power", val,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.wait()
        if proc.returncode == 0:
            logger.info(f"Set Raspberry Pi screen power to {val} via vcgencmd")
    except Exception as e:
        logger.debug(f"vcgencmd display power control failed or skipped: {e}")
