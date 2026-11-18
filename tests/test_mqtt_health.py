"""MQTT: what the owner is told (the bell) when the bridge's task dies or the broker stays away."""
import asyncio
from unittest.mock import patch

import pytest

from mirrordash_core import notifications
from mirrordash_core.features.mqtt import bridge as mqtt
from mirrordash_core.features.mqtt.bridge import MqttBridge
from mirrordash_core.features.mqtt.health import TELL_AFTER, ConnectionHealth

ON = {"mqtt": {"enabled": True, "host": "h", "id": "m1"}}


@pytest.mark.asyncio
async def test_a_dead_task_is_logged_and_put_on_the_bell(caplog):
    bridge = MqttBridge()

    async def dies():
        raise AttributeError("no MqttError here")  # what a wrong aiomqtt did

    with patch.object(mqtt, "load_config", return_value=ON), patch.object(bridge, "_run", dies):
        await bridge.restart()
        await asyncio.gather(bridge._task, return_exceptions=True)
        await asyncio.sleep(0)  # the done-callback runs on the next turn of the loop
    assert bridge.status == "stopped: an error, see Logs"
    assert "MQTT stopped" in caplog.text and "AttributeError" in caplog.text
    assert [n["level"] for n in notifications.items()] == ["error"]


@pytest.mark.asyncio
async def test_stopping_is_not_a_death():
    bridge = MqttBridge()

    async def forever():
        await asyncio.sleep(3600)

    with patch.object(mqtt, "load_config", return_value=ON), patch.object(bridge, "_run", forever):
        await bridge.restart()
        await bridge.stop()
        await asyncio.sleep(0)
    assert bridge.status == "off" and notifications.items() == []


def test_ten_minutes_without_a_connection_is_told_once():
    now = [1000.0]
    health = ConnectionHealth(clock=lambda: now[0])
    health.failed("not connected: refused")
    now[0] += TELL_AFTER - 1
    health.failed("not connected: refused")
    assert notifications.items() == []  # not yet
    now[0] += 1
    health.failed("not connected: refused")
    now[0] += 5000
    health.failed("not connected: refused")  # still down: not told again
    items = notifications.items()
    assert len(items) == 1 and items[0]["level"] == "warning"
    assert "10 minutes: not connected: refused" in items[0]["text"]


def test_a_connection_starts_the_count_again():
    now = [0.0]
    health = ConnectionHealth(clock=lambda: now[0])
    health.failed("down")
    now[0] += TELL_AFTER - 1
    health.reset()  # connected
    now[0] += 1000
    health.failed("down again")  # a new outage starts here
    assert notifications.items() == []
    now[0] += TELL_AFTER
    health.failed("down again")
    assert len(notifications.items()) == 1
