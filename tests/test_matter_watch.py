"""Matter: the bell hears of a node that stays down, not of one crash that systemd recovers from."""
from unittest.mock import patch

import pytest

from mirrordash_core import notifications
from mirrordash_core.features.matter import watch

pytestmark = pytest.mark.asyncio


async def looks(watcher, *unit_looks, enabled=True):
    """One look per answer of `systemctl show`: (ActiveState, NRestarts), or None for no answer."""
    answers = list(unit_looks)

    async def read_unit():
        return answers.pop(0)

    with patch.object(watch, "read_unit", read_unit), patch.object(watch, "matter_enabled", return_value=enabled):
        for _ in unit_looks:
            await watcher.check()


def told():
    return [n["level"] for n in notifications.items()]


async def test_three_looks_at_a_node_that_is_down_tell_once():
    watcher = watch.MatterWatch()
    await looks(watcher, ("activating", 1), ("failed", 1), ("inactive", 1))
    assert told() == ["error"]
    await looks(watcher, ("failed", 1), ("failed", 1))
    assert told() == ["error"]  # not again


async def test_one_crash_that_came_back_says_nothing():
    watcher = watch.MatterWatch()
    await looks(watcher, ("active", 0), ("activating", 0), ("active", 1), ("active", 1), ("failed", 1), ("active", 2))
    assert told() == []


async def test_a_healthy_look_starts_the_count_again():
    watcher = watch.MatterWatch()
    await looks(watcher, ("failed", 0), ("failed", 0), ("active", 0), ("failed", 0), ("failed", 0))
    assert told() == []


async def test_restarts_that_keep_growing_count_as_down_though_it_looks_active():
    watcher = watch.MatterWatch()
    await looks(watcher, ("active", 0), ("active", 1), ("active", 2), ("active", 3))
    assert told() == ["error"]


async def test_nothing_is_said_when_matter_is_off_or_systemctl_gave_no_answer():
    watcher = watch.MatterWatch()
    await looks(watcher, ("failed", 0), ("failed", 0), ("failed", 0), ("failed", 0), enabled=False)
    await looks(watcher, None, None, None, None)
    assert told() == []


async def test_the_minute_after_a_start_is_not_counted():
    now = [0.0]
    watcher = watch.MatterWatch(clock=lambda: now[0])
    watcher.hold_off()
    await looks(watcher, ("activating", 0), ("activating", 0), ("activating", 0))
    assert told() == []
    now[0] += watch.QUIET_AFTER_START
    await looks(watcher, ("failed", 0), ("failed", 0), ("failed", 0))
    assert told() == ["error"]


async def test_systemctl_show_is_read_by_name_not_by_order():
    async def run_command(*command, **kwargs):
        assert command[:2] == ("systemctl", "show") and "sudo" not in command
        return 0, "NRestarts=4\nActiveState=active\n", ""

    with patch.object(watch, "run_command", run_command):
        assert await watch.read_unit() == ("active", 4)
