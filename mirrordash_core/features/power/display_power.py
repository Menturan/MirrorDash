# Licensed under the PolyForm Noncommercial License 1.0.0.
"""Decides when the screen is on.

Base mode (system.display_control.mode):
  manual   - always on
  interval - on inside the daily schedule
  wake     - off until something wakes it

Wake timer (system.display_control.wake): when the base mode has the screen off, it can be
woken for `timeout_minutes` by presence (if `presence` is on), the button, the admin page or
the open API (POST /admin/screen {"state": "on", "timeout_minutes": optional}).
  extend=True  - every new wake restarts the countdown, and it stands still while someone
                 is there; the screen goes off `timeout_minutes` after the last activity.
  extend=False - the screen goes off `timeout_minutes` after it was woken, whatever happens.
An explicit "off" turns the screen off and keeps it off until something wakes it again or the
base mode changes (e.g. the schedule starts).

While the screen is off the modules sleep: their fetch_json waits on `awake` (see
modules/loader.py), so a due fetch happens once when the screen comes back on and the module's
own schedule is kept. A module with `keep_running = True` is never held.
"""

import asyncio
import logging
import time as time_mod
from datetime import datetime, time
from zoneinfo import ZoneInfo
from mirrordash_core.config import load_config
from mirrordash_core.features.hardware.display import set_screen_power

logger = logging.getLogger("mirrordash.core.power")


DEFAULT_WAKE = {"timeout_minutes": 5, "extend": True, "presence": True}


def wake_settings(display_cfg: dict) -> dict:
    return {**DEFAULT_WAKE, **display_cfg.get("wake", {})}


class DisplayPowerManager:
    RETRY_AFTER_FAILURE = 30.0  # seconds; e.g. no wlr-randr on a development machine

    def __init__(self):
        self.task: asyncio.Task | None = None
        self.is_on: bool = True
        self.wake_until = 0.0        # time.monotonic() until which the screen is woken
        self.forced_off = False      # an explicit "off" wins until a wake or base change
        self.last_base: bool | None = None
        self.was_present = False
        self.changed = asyncio.Event()
        self.awake = asyncio.Event()  # set while the screen is on; modules' fetch_json waits on it
        self.awake.set()

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run_loop())
            logger.info("DisplayPowerManager started.")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        logger.info("DisplayPowerManager stopped.")

    # --- Commands (API, admin page, button) ---------------------------------------------

    def wake(self, timeout_minutes: float | None = None, now: float | None = None) -> None:
        """Turn the screen on for a while (or keep it on longer, depending on `extend`)."""
        settings = wake_settings(load_config().get("system", {}).get("display_control", {}))
        now = time_mod.monotonic() if now is None else now
        until = now + (timeout_minutes or settings["timeout_minutes"]) * 60
        self.forced_off = False
        if settings["extend"]:
            self.wake_until = max(self.wake_until, until)
        elif now >= self.wake_until:  # fixed time: a wake while already awake changes nothing
            self.wake_until = until
        self.changed.set()

    def turn_off(self) -> None:
        self.wake_until = 0.0
        self.forced_off = True
        self.changed.set()

    def toggle(self) -> None:
        self.turn_off() if self.is_on else self.wake()

    # --- Decision ------------------------------------------------------------------------

    def desired_state(self, display_cfg: dict, now: float, local_time: time, present: bool) -> bool:
        """Whether the screen should be on now. Pure apart from the timer state it updates."""
        mode = display_cfg.get("mode", "manual")
        interval = display_cfg.get("interval", {})
        base_on = mode == "manual" or (
            mode == "interval" and self._is_time_in_range(interval.get("start", "07:00"), interval.get("end", "22:00"), local_time)
        )
        if base_on != self.last_base:  # e.g. the schedule starts or ends: a manual "off" no longer applies
            self.forced_off = False
            self.last_base = base_on

        settings = wake_settings(display_cfg)
        if settings["presence"] and present:
            if not self.was_present:  # someone arrived: wakes (and cancels a manual "off")
                self.wake(now=now)
            elif settings["extend"]:  # still there: hold the countdown
                self.wake_until = max(self.wake_until, now + settings["timeout_minutes"] * 60)
        self.was_present = present

        return not self.forced_off and (base_on or now < self.wake_until)

    def status(self) -> dict:
        left = max(0.0, self.wake_until - time_mod.monotonic())
        return {"on": self.is_on, "base_on": bool(self.last_base), "wake_seconds_left": left, "forced_off": self.forced_off}

    async def _run_loop(self) -> None:
        from mirrordash_core.features.hardware.devices import gpio_inputs

        while True:
            try:
                config = load_config()
                display_cfg = config.get("system", {}).get("display_control", {})
                desired = self.desired_state(display_cfg, time_mod.monotonic(), self._local_time(config),
                                             gpio_inputs.motion_active)
                if desired != self.is_on:
                    logger.info(f"Turning the screen {'on' if desired else 'off'}.")
                    if not await self.set_state(desired):
                        await asyncio.sleep(self.RETRY_AFTER_FAILURE)
                # Re-check every second, or right away when a command arrives
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in display power loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    @staticmethod
    def _local_time(config: dict) -> time:
        tz_name = config.get("globals", {}).get("timezone", "Europe/Stockholm")
        try:
            tz = ZoneInfo(tz_name)
        except Exception as e:
            logger.warning(f"Invalid timezone '{tz_name}' in globals: {e}. Using local system time.")
            tz = None
        return (datetime.now(tz) if tz else datetime.now()).time()

    async def set_state(self, on: bool) -> bool:
        success = await set_screen_power(on)
        if success:
            self.is_on = on
            self.awake.set() if on else self.awake.clear()
        return bool(success)

    def _is_time_in_range(self, start_str: str, end_str: str, current_time: time) -> bool:
        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start = time(start_h, start_m)
            end = time(end_h, end_m)

            if start <= end:
                return start <= current_time <= end
            else:
                return current_time >= start or current_time <= end
        except Exception as e:
            logger.error(f"Error checking time range {start_str}-{end_str}: {e}")
            return True


display_power_manager = DisplayPowerManager()
