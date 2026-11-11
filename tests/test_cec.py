"""HDMI-CEC with the screen (features/hardware/cec.py): the TV off and on, a TV on another input left alone.
cec-ctl is faked here; its output's shape is as on the Pi 3, the TV's answers as cec-ctl prints them (not
yet seen from a real TV)."""
import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from mirrordash_core.app import app
from mirrordash_core.features.hardware import cec

INFO = "Driver Info:\n\tPhysical Address           : 1.0.0.0\n"
# The question as cec-ctl prints it on the Pi 3 (2026-11-06), then a device's answer
ASKED = ("Transmit from Playback Device 1 to all (4 to 15):\nREQUEST_ACTIVE_SOURCE (0x85)\n"
         "\tSequence: 29 Tx Timestamp: 5244.759081s\n\tTx, OK, Rx, Timeout\n")
ANOTHER_SOURCE = ASKED + "Received from Playback Device 2 to all (8 to 15):\nACTIVE_SOURCE (0x82):\n\tphys-addr: 2.0.0.0\n"
MIRROR_SOURCE = ASKED + "Received from Playback Device 1 to all (4 to 15):\nACTIVE_SOURCE (0x82):\n\tphys-addr: 1.0.0.0\n"
NOBODY = ASKED + "Received from Audio System to all (5 to 15):\nREPORT_PHYSICAL_ADDR (0x84):\n\tphys-addr: 3.0.0.0\n"
TV_ON = "Received from TV to Playback Device 1 (0 to 4): REPORT_POWER_STATUS (0x90):\n\tpwr-state: on (0x00)\n"
TV_STANDBY = "Received from TV to Playback Device 1 (0 to 4): REPORT_POWER_STATUS (0x90):\n\tpwr-state: standby (0x01)\n"


ASKING = ("--poll", "--request-active-source", "--give-device-power-status")


def run(on: bool, active: str = "", power: str = "", devices=("/dev/cec0",), tv=True, info=INFO) -> list[str]:
    """set_tv_power with a fake cec-ctl: the messages it sent (its questions left out), as their arguments
    after the claim. tv: whether a TV acknowledges the poll."""
    sent = []

    async def cec_ctl(*command, **_):
        arguments = command[command.index("--allow-unreg-fallback") + 1:] if "--allow-unreg-fallback" in command else []
        if not arguments:
            return 0, info, ""
        sent.append(" ".join(arguments))
        if "--poll" in arguments:
            return 0, "POLL\n\tTx, OK\n" if tv else "POLL\n\tTx, Not Acknowledged (2), Max Retries\n", ""
        return 0, active if "--request-active-source" in arguments else power if "--give-device-power-status" in arguments else "", ""

    with patch.object(cec.glob, "glob", return_value=list(devices)), patch.object(cec, "run_command", cec_ctl):
        asyncio.run(cec.set_tv_power(on))
    return [message for message in sent if not any(question in message for question in ASKING)]


def test_off_asks_the_tv_alone_to_turn_off_unless_it_shows_another_input():
    assert run(False) == ["-t 0 --standby"]  # nothing says what's on: the mirror's own TV
    assert run(False, MIRROR_SOURCE) == ["-t 0 --standby"]
    assert run(False, NOBODY) == ["-t 0 --standby"]  # a soundbar's address isn't an input on the TV
    assert run(False, ANOTHER_SOURCE) == []  # someone watches another input


def test_on_wakes_the_tv_and_shows_the_mirror_unless_it_is_on_with_another_input():
    woken = ["-t 0 --image-view-on", "-t 15 --active-source phys-addr=1.0.0.0"]
    assert run(True, power=TV_STANDBY) == woken
    assert run(True, ANOTHER_SOURCE, TV_STANDBY) == woken  # a player left as the source, the TV itself off
    assert run(True, ANOTHER_SOURCE, TV_ON) == []
    assert run(True) == woken  # a TV that doesn't say its power state


def test_a_monitor_or_no_adapter_gets_nothing_and_a_tv_without_hotplug_is_only_woken():
    assert run(False, tv=False) == [] and run(True, tv=False) == []  # no TV answers the poll: a monitor
    assert run(False, devices=()) == [] and run(True, devices=()) == []
    no_address = INFO.replace("1.0.0.0", "f.f.f.f")  # a TV in standby that dropped its hotplug line
    assert run(False, info=no_address) == [] and run(True, info=no_address) == ["-t 0 --image-view-on"]


def test_the_setting_is_on_unless_turned_off_and_skips_cec_then():
    from mirrordash_core.admin import require_api_key
    from mirrordash_core.features.power.display_power import DisplayPowerManager
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with patch("mirrordash_core.features.settings.system_settings.update_system_settings", new_callable=AsyncMock) as update:
            TestClient(app).post("/admin/panels/power/save", data={"display_control[cec]": ["false"]})
        assert update.call_args.kwargs["settings"]["display_control"]["cec"] is False
    finally:
        app.dependency_overrides.clear()

    for saved, asked in (({}, True), ({"cec": False}, False)):
        with patch("mirrordash_core.features.power.display_power.load_config", return_value={"system": {"display_control": saved}}), \
             patch("mirrordash_core.features.power.display_power.set_screen_power", new_callable=AsyncMock, return_value=True), \
             patch("mirrordash_core.features.power.display_power.set_tv_power", new_callable=AsyncMock) as tv, \
             patch("mirrordash_core.features.modules.client.module_host"):
            asyncio.run(DisplayPowerManager().set_state(False))
        assert tv.called is asked
