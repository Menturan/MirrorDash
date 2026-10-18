"""The Offline badge's source (features/kiosk/internet.py): offline only after 2 failed checks in a
row, online at the first good one, and the screens hear only changes."""
import asyncio
from unittest.mock import AsyncMock, patch

from mirrordash_core.features.kiosk import internet


def run_checks(results):
    """Run one check per result (True: the internet answered); returns (monitor, broadcasts)."""
    monitor = internet.InternetMonitor()
    answers = iter(results)
    with patch.object(internet, "reachable", AsyncMock(side_effect=lambda: next(answers))), \
         patch("mirrordash_core.features.kiosk.ws.manager.broadcast", new_callable=AsyncMock) as broadcast:
        for _ in results:
            asyncio.run(monitor.check())
    return monitor, [c.args[0] for c in broadcast.call_args_list]


def test_one_failed_check_is_not_offline():
    monitor, sent = run_checks([True, False, True])
    assert monitor.online and sent == []


def test_two_failed_checks_in_a_row_are_offline_and_one_good_one_is_online_again():
    monitor, sent = run_checks([False, False, False, True])
    assert monitor.online
    assert sent == [{"type": "internet", "online": False}, {"type": "internet", "online": True}]


def test_reachable_tries_the_second_address_when_the_first_fails():
    tried = []

    async def fake_open(host, port):
        tried.append(host)
        if host == "1.1.1.1":
            raise OSError("unreachable")
        writer = AsyncMock()
        writer.close = lambda: None
        return None, writer

    with patch("asyncio.open_connection", fake_open):
        assert asyncio.run(internet.reachable()) is True
    assert tried == ["1.1.1.1", "8.8.8.8"]
