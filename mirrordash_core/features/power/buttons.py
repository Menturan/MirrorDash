# SPDX-License-Identifier: AGPL-3.0-only
"""What a press of the mirror's button does: the action the owner chose for it on the Hardware tab
(BUTTON_ACTIONS, features/hardware/devices.py). The press comes as the event "hardware.button"
(features/hardware/inputs.py); app.py subscribes on_button to it."""

from mirrordash_core.features.power.display_power import display_power_manager
from mirrordash_core.features.power.privacy import toggle_privacy
from mirrordash_core.host import poweroff_system, reboot_system, run_restart


async def on_button(event: dict) -> None:
    action = event.get("action")
    if action == "wake":
        display_power_manager.wake()
    elif action == "toggle_display":
        display_power_manager.toggle()
    elif action == "privacy":
        await toggle_privacy()
    elif action == "restart":
        await run_restart()
    elif action == "reboot":
        await reboot_system(delay_sec=1.0)
    elif action == "shutdown":
        await poweroff_system(delay_sec=1.0)
