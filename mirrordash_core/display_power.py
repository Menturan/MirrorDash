# Licensed under the PolyForm Noncommercial License 1.0.0.

import asyncio
import logging
from datetime import datetime, time
import time as time_mod
from zoneinfo import ZoneInfo

from mirrordash_core.config import load_config
from mirrordash_core.system import set_screen_power

logger = logging.getLogger("mirrordash.core.display_power")

class DisplayPowerManager:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.is_on: bool = True
        self.last_motion_time: float = 0.0

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

    async def _run_loop(self) -> None:
        self.last_motion_time = time_mod.time()

        while True:
            try:
                config = load_config()
                system_cfg = config.get("system", {})
                display_cfg = system_cfg.get("display_control", {})

                mode = display_cfg.get("mode", "manual")

                if mode == "manual":
                    # manual control is handled via HTTP endpoints
                    await asyncio.sleep(1.0)
                    continue

                elif mode == "interval":
                    interval_cfg = display_cfg.get("interval", {})
                    start_str = interval_cfg.get("start", "07:00")
                    end_str = interval_cfg.get("end", "22:00")

                    globals_cfg = config.get("globals", {})
                    tz_name = globals_cfg.get("timezone", "Europe/Stockholm")
                    try:
                        tz = ZoneInfo(tz_name)
                    except Exception as e:
                        logger.warning(f"Invalid timezone '{tz_name}' in globals: {e}. Using local system time.")
                        tz = None

                    now = datetime.now(tz) if tz else datetime.now()
                    should_be_on = self._is_time_in_range(start_str, end_str, now.time())

                    if should_be_on != self.is_on:
                        logger.info(f"Schedule mismatch (should_be_on={should_be_on}). Toggling display.")
                        await self.set_state(should_be_on)

                    await asyncio.sleep(5.0) # Check schedule every 5 seconds

                elif mode == "pir":
                    # The sensor is read by hardware.gpio_inputs (kernel gpio-key overlay)
                    from mirrordash_core.hardware import gpio_inputs
                    timeout_mins = display_cfg.get("pir", {}).get("timeout_minutes", 5)

                    now_ts = time_mod.time()
                    if gpio_inputs.motion_active:
                        self.last_motion_time = now_ts
                        if not self.is_on:
                            logger.info("Motion detected! Turning display ON.")
                            await self.set_state(True)
                    elif self.is_on and now_ts - self.last_motion_time > timeout_mins * 60:
                        logger.info(f"No motion detected for {timeout_mins} minutes. Turning display OFF.")
                        await self.set_state(False)

                    await asyncio.sleep(0.5)

                else:
                    # Unknown mode: behave like manual instead of spinning without a sleep.
                    # (The GPIO button is handled by hardware.GpioInputs.)
                    await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Error in display power loop: {e}", exc_info=True)
                await asyncio.sleep(5.0)

    async def set_state(self, on: bool) -> None:
        success = await set_screen_power(on)
        if success:
            self.is_on = on

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
