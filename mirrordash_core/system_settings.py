# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
import os
import re
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from mirrordash_core.admin import require_api_key
from mirrordash_core.config import load_config, save_config
from mirrordash_core.features.hardware import display
from mirrordash_core.features.hardware.display import apply_brightness, apply_system_settings, get_available_resolutions
from fastapi import APIRouter, Depends, HTTPException, Request
from mirrordash_core.admin import notify, require_api_key
from mirrordash_core.config import load_config, save_config

logger = logging.getLogger("mirrordash.core.system_settings")
router = APIRouter(prefix="/admin")


@router.get("/system", dependencies=[Depends(require_api_key)])
async def get_system_settings() -> dict:
    config = load_config()
    system_cfg = config.get("system", {})
    resolutions = await get_available_resolutions()
    from mirrordash_core.features.settings.ssh import get_ssh_status
    ssh_active = await get_ssh_status()
    return {
        "settings": {
            "rotation": system_cfg.get("rotation", "normal"),
            "resolution": system_cfg.get("resolution", "auto"),
            "brightness": system_cfg.get("brightness", 100),
            "brightness_supported": display.brightness_supported is not False,
            "volume": system_cfg.get("volume", 80),
            "ssh": ssh_active,
            "prerelease": system_cfg.get("prerelease", False),
            "display_control": system_cfg.get("display_control", {
                "mode": "manual",
                "interval": {"start": "07:00", "end": "22:00"},
            }),
        },
        "resolutions": resolutions
    }


@router.post("/system", dependencies=[Depends(require_api_key)])
async def update_system_settings(settings: dict = Body(...)) -> dict:
    config = load_config()
    system_cfg = config.setdefault("system", {})

    # Use existing values as defaults if not provided in the incoming request
    rotation = settings.get("rotation", system_cfg.get("rotation", "normal"))
    resolution = settings.get("resolution", system_cfg.get("resolution", "auto"))
    brightness = settings.get("brightness", system_cfg.get("brightness", 100))
    volume = settings.get("volume", system_cfg.get("volume", 80))
    ssh_enabled = settings.get("ssh", system_cfg.get("ssh", True))
    prerelease = settings.get("prerelease", system_cfg.get("prerelease", False))
    
    # Merge display_control securely
    from mirrordash_core.features.power.display_power import DEFAULT_WAKE
    current_dc = system_cfg.get("display_control", {
        "mode": "manual",
        "interval": {"start": "07:00", "end": "22:00"},
        "wake": dict(DEFAULT_WAKE),
    })
    incoming_dc = settings.get("display_control", {})
    
    display_control = dict(current_dc)
    if incoming_dc:
        if "mode" in incoming_dc:
            display_control["mode"] = incoming_dc["mode"]
        if "interval" in incoming_dc:
            display_control["interval"] = {**display_control.get("interval", {}), **incoming_dc["interval"]}
        if "wake" in incoming_dc:
            display_control["wake"] = {**display_control.get("wake", {}), **incoming_dc["wake"]}

    # Validation
    if rotation not in ("normal", "left", "right", "inverted"):
        raise HTTPException(status_code=400, detail="Invalid rotation value")
    if not isinstance(brightness, int) or brightness < 10 or brightness > 100:
        raise HTTPException(status_code=400, detail="Brightness must be between 10 and 100")
    if not isinstance(volume, int) or volume < 0 or volume > 100:
        raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")

    mode = display_control.get("mode", "manual")
    if mode not in ("manual", "interval", "wake"):
        raise HTTPException(status_code=400, detail="Invalid display power mode")
    wake = display_control.get("wake", {})
    timeout = wake.get("timeout_minutes", DEFAULT_WAKE["timeout_minutes"])
    if not isinstance(timeout, int) or not 1 <= timeout <= 1440:
        raise HTTPException(status_code=400, detail="The screen timeout must be 1 to 1440 minutes")
    if not all(isinstance(wake.get(k, True), bool) for k in ("extend", "presence")):
        raise HTTPException(status_code=400, detail="Invalid wake settings")
    if not isinstance(prerelease, bool):
        raise HTTPException(status_code=400, detail="Test versions must be on or off")

    if mode == "interval":
        interval = display_control.get("interval", {})
        start = interval.get("start", "07:00")
        end = interval.get("end", "22:00")
        if not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
            raise HTTPException(status_code=400, detail="Invalid interval time format (HH:MM)")

    system_cfg["rotation"] = rotation
    system_cfg["resolution"] = resolution
    system_cfg["brightness"] = brightness
    system_cfg["volume"] = volume
    system_cfg["display_control"] = display_control
    system_cfg["ssh"] = ssh_enabled
    system_cfg["prerelease"] = prerelease

    # Only touch SSH when this request is about it: the Power tab saves just the display
    # schedule, and re-checking SSH there failed whenever the service state differed.
    if "ssh" in settings:
        # Apply SSH state; if enabling SSH, require and apply new password for pi user
        from mirrordash_core.features.settings.ssh import set_ssh_status, get_ssh_status
        current_ssh_active = await get_ssh_status()
        if ssh_enabled:
            if not current_ssh_active:
                pi_password = settings.get("pi_password")
                if not pi_password or len(pi_password) < 8:
                    raise HTTPException(
                        status_code=400,
                        detail="A password of at least 8 characters is required to enable SSH."
                    )
                # Update the pi user's password using chpasswd
                try:
                    chpasswd_input = f"pi:{pi_password}\n".encode()
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "-n", "chpasswd",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate(input=chpasswd_input)
                    if proc.returncode != 0:
                        err_msg = stderr.decode(errors="replace").strip()
                        logger.error(f"chpasswd failed: {err_msg}")
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to update system password: {err_msg}"
                        )
                    logger.info("Password for user 'pi' updated successfully.")

                    # Generate secure SHA-512 crypt hash using openssl
                    proc_hash = await asyncio.create_subprocess_exec(
                        "openssl", "passwd", "-6", "-stdin",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout_hash, stderr_hash = await proc_hash.communicate(input=pi_password.encode())
                    if proc_hash.returncode != 0:
                        err_msg = stderr_hash.decode(errors="replace").strip()
                        logger.error(f"openssl hash failed: {err_msg}")
                        raise HTTPException(status_code=500, detail="Failed to hash system password.")
                    pwd_hash = stdout_hash.decode().strip()

                    # Save password hash persistently
                    try:
                        hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
                        with open(hash_path, "w", encoding="utf-8") as f:
                            f.write(pwd_hash)
                        os.chmod(hash_path, 0o600)
                    except Exception as io_err:
                        logger.error(f"Failed to write password hash to disk: {io_err}")

                except HTTPException:
                    raise
                except Exception as exc:
                    logger.error(f"Unexpected error running chpasswd: {exc}")
                    raise HTTPException(status_code=500, detail="Unexpected error updating system password.")
        else:
            # Delete persistent password hash if SSH is disabled
            try:
                hash_path = "/home/pi/.mirrordash/data/pi_password.hash"
                if os.path.exists(hash_path):
                    os.remove(hash_path)
            except Exception as io_err:
                logger.error(f"Failed to remove password hash: {io_err}")

        await set_ssh_status(ssh_enabled)

    # Saved only now: a refused SSH password must not leave "ssh: on" in the config
    save_config(config)

    # Queue display/audio settings to apply asynchronously (re-applying an unchanged
    # rotation/resolution can make the screen flicker, so only when they were sent)
    if any(k in settings for k in ("rotation", "resolution", "volume")):
        asyncio.create_task(apply_system_settings(rotation, resolution, brightness, volume))
    elif "brightness" in settings:  # the slider, or Home Assistant: only the brightness
        asyncio.create_task(apply_brightness(brightness))

    return {"status": "success", "message": "System settings saved and applied successfully"}


@router.post("/panels/power/save", dependencies=[Depends(require_api_key)])
@router.post("/panels/system/save", dependencies=[Depends(require_api_key)])
async def save_system_settings_route(request: Request):
    form_data = await request.form()
    flat_data = {}
    for k, v in form_data.multi_items():
        if k in flat_data:
            if isinstance(flat_data[k], list):
                flat_data[k].append(v)
            else:
                flat_data[k] = [flat_data[k], v]
        else:
            flat_data[k] = v

    from mirrordash_core.forms import parse_flat_form_data
    parsed = parse_flat_form_data(flat_data)

    # Format times back to HH:MM strings expected by update_system_settings
    display_control = parsed.get("display_control", {})
    interval = display_control.get("interval", {})
    if "start_h" in interval and "start_m" in interval:
        h = int(interval["start_h"])
        m = interval["start_m"]
        ampm = interval.get("start_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        display_control["interval"] = {
            "start": f"{h:02d}:{m}"
        }
    if "end_h" in interval and "end_m" in interval:
        h = int(interval["end_h"])
        m = interval["end_m"]
        ampm = interval.get("end_ampm")
        if ampm:
            if ampm == "PM" and h != 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
        if "interval" not in display_control:
            display_control["interval"] = {}
        display_control["interval"]["end"] = f"{h:02d}:{m}"

    if "brightness" in parsed:
        try:
            parsed["brightness"] = int(parsed["brightness"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Brightness must be an integer")
    if "volume" in parsed:
        try:
            parsed["volume"] = int(parsed["volume"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Volume must be an integer")

    if "wake" in display_control:
        wake = display_control["wake"]
        try:
            wake["timeout_minutes"] = int(wake.get("timeout_minutes", 5))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="The screen timeout must be a whole number of minutes")
        for key in ("extend", "presence"):
            if key in wake:  # parse_flat_form_data already turns "true"/"false" (and checkbox pairs) into bools
                wake[key] = wake[key] in (True, "true")

    await update_system_settings(settings=parsed)
    if "ssh" in parsed:  # only the Developer card sends it
        on = parsed["ssh"] in (True, "true")
        return notify("SSH is on. Log in with: ssh pi@mirrordash.local" if on else "SSH is off.",
                      **{"md-ssh": {"on": on}})
    return notify("Saved.")
