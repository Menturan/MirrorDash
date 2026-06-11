import pytest
import asyncio
import httpx
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from zoneinfo import ZoneInfo
from mirrordash_calendar.plugin import CalendarModule, format_date

@pytest.fixture(autouse=True)
def mock_plugin_datetime():
    class DatetimeMeta(type):
        def __instancecheck__(cls, instance):
            return isinstance(instance, datetime.datetime)

    class MockDatetime(datetime.datetime, metaclass=DatetimeMeta):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2026, 6, 3, 8, 0, 0, tzinfo=tz)

    with patch("mirrordash_calendar.plugin.datetime", MockDatetime):
        yield

MOCK_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp//Example Calendar//EN
BEGIN:VEVENT
UID:event1@example.com
DTSTART:20260603T100000Z
DTEND:20260603T110000Z
SUMMARY:Test Event 1
END:VEVENT
BEGIN:VEVENT
UID:event-all-day@example.com
DTSTART;VALUE=DATE:20260604
DTEND;VALUE=DATE:20260605
SUMMARY:Test All Day
END:VEVENT
BEGIN:VEVENT
UID:event-recurring@example.com
DTSTART:20260603T140000Z
DTEND:20260603T150000Z
RRULE:FREQ=DAILY;COUNT=3
SUMMARY:Daily Meeting
END:VEVENT
END:VCALENDAR"""

@pytest.mark.asyncio
async def test_format_date_swedish_and_english():
    d = datetime.date(2026, 6, 5) # Friday
    
    # Test Swedish formatting (uppercase)
    sv_formatted = format_date(d, "sv")
    assert "FREDAG" in sv_formatted
    assert "5" in sv_formatted
    assert "JUN" in sv_formatted
    
    # Test English formatting (uppercase)
    en_formatted = format_date(d, "en")
    assert "FRIDAY" in en_formatted
    assert "JUN" in en_formatted
    assert "5" in en_formatted

@pytest.mark.asyncio
async def test_calendar_timezone_handling_and_parsing():
    config = {
        "globals": {
            "timezone": "Europe/Stockholm",
            "language": "en",
            "time_format": "24h"
        },
        "calendars": [
            {"name": "Work", "url": "http://example.com/work.ics", "color": "#ff0000", "icon": "briefcase"}
        ],
        "max_events": 10,
        "maximum_days": 7
    }
    
    module = CalendarModule(config)
    
    # June 3, 2026 in Europe/Stockholm is UTC+2
    today_date = datetime.date(2026, 6, 3)
    start_dt = datetime.datetime(2026, 6, 3, 0, 0, 0, tzinfo=module.tz)
    end_dt = datetime.datetime(2026, 6, 10, 23, 59, 59, tzinfo=module.tz)
    
    events = module.process_calendar_data(
        MOCK_ICS.encode('utf-8'),
        config["calendars"][0],
        today_date,
        start_dt,
        end_dt
    )
    
    # Events expected:
    # 1. Test Event 1: June 3, Stockholm time: 12:00 - 13:00
    # 2. Daily Meeting (Instance 1): June 3, Stockholm time: 16:00 - 17:00
    # 3. Test All Day: June 4, Stockholm time: All Day
    # 4. Daily Meeting (Instance 2): June 4, Stockholm time: 16:00 - 17:00
    # 5. Daily Meeting (Instance 3): June 5, Stockholm time: 16:00 - 17:00
    
    assert len(events) == 5
    
    # Verify first timed event details
    ev1 = [e for e in events if e["summary"] == "Test Event 1"][0]
    assert ev1["date"] == datetime.date(2026, 6, 3)
    assert ev1["time_str"] == "12:00 - 13:00"
    assert ev1["all_day"] is False
    assert ev1["calendar_name"] == "Work"
    assert ev1["calendar_color"] == "#ff0000"
    assert ev1["calendar_icon"] == "briefcase"
    
    # Verify all-day event details
    ev_ad = [e for e in events if e["summary"] == "Test All Day"][0]
    assert ev_ad["date"] == datetime.date(2026, 6, 4)
    assert ev_ad["all_day"] is True
    assert ev_ad["time_str"] == "All Day"
    
    # Verify recurrence expansion
    meetings = sorted([e for e in events if e["summary"] == "Daily Meeting"], key=lambda x: x["date"])
    assert len(meetings) == 3
    assert meetings[0]["date"] == datetime.date(2026, 6, 3)
    assert meetings[1]["date"] == datetime.date(2026, 6, 4)
    assert meetings[2]["date"] == datetime.date(2026, 6, 5)
    for m in meetings:
        assert m["time_str"] == "16:00 - 17:00"

@pytest.mark.asyncio
async def test_time_format_12h():
    config = {
        "globals": {
            "timezone": "Europe/Stockholm",
            "language": "en",
            "time_format": "12h"
        },
        "calendars": [
            {"name": "Work", "url": "http://example.com/work.ics"}
        ],
        "max_events": 10,
        "maximum_days": 7
    }
    
    module = CalendarModule(config)
    today_date = datetime.date(2026, 6, 3)
    start_dt = datetime.datetime(2026, 6, 3, 0, 0, 0, tzinfo=module.tz)
    end_dt = datetime.datetime(2026, 6, 10, 23, 59, 59, tzinfo=module.tz)
    
    events = module.process_calendar_data(
        MOCK_ICS.encode('utf-8'),
        config["calendars"][0],
        today_date,
        start_dt,
        end_dt
    )
    
    # Check format in 12h: Test Event 1 (10:00Z -> 12:00 Stockholm) -> 12:00 PM - 1:00 PM
    ev1 = [e for e in events if e["summary"] == "Test Event 1"][0]
    assert ev1["time_str"] == "12:00 PM - 1:00 PM"
    
    # Daily Meeting (14:00Z -> 16:00 Stockholm) -> 4:00 PM - 5:00 PM
    meeting = [e for e in events if e["summary"] == "Daily Meeting"][0]
    assert meeting["time_str"] == "4:00 PM - 5:00 PM"

@pytest.mark.asyncio
async def test_resilience_to_feed_fetching_errors():
    config = {
        "globals": {"timezone": "Europe/Stockholm", "language": "en"},
        "calendars": [
            {"name": "Valid Feed", "url": "http://example.com/valid.ics"},
            {"name": "Failing Feed", "url": "http://example.com/fail.ics"}
        ],
        "max_events": 10,
        "maximum_days": 7
    }
    
    module = CalendarModule(config)
    module.render_template = MagicMock(return_value="<div>Rendered</div>")
    
    # Mock HTTP client responses: Valid Feed succeeds, Failing Feed raises exception
    async def mock_get(url, *args, **kwargs):
        mock_resp = MagicMock()
        if "valid.ics" in url:
            mock_resp.status_code = 200
            mock_resp.content = MOCK_ICS.encode('utf-8')
            return mock_resp
        else:
            raise httpx.RequestError("Connection timeout")
            
    broadcast_mock = AsyncMock()
    
    with patch("httpx.AsyncClient.get", side_effect=mock_get):
        # We patch asyncio.sleep to raise CancelledError on second iteration to break the loop
        async def mock_sleep(secs):
            raise asyncio.CancelledError()
            
        with patch("asyncio.sleep", side_effect=mock_sleep):
            try:
                await module.run_loop(broadcast_mock)
            except asyncio.CancelledError:
                pass
                
    # Verify that template rendering was still called (with events from Valid Feed)
    # and that the broadcast function was called
    assert module.render_template.called
    assert broadcast_mock.called
    
    # Inspect arguments passed to render_template
    kwargs = module.render_template.call_args[1]
    grouped = kwargs["grouped_events"]
    # Total events processed should be 5 from the Valid Feed, despite Failing Feed timeout
    total_events = sum(len(g["events"]) for g in grouped)
    assert total_events == 5

@pytest.mark.asyncio
async def test_cache_fallback():
    config = {
        "globals": {"timezone": "Europe/Stockholm", "language": "en"},
        "calendars": [
            {"name": "Test Cache", "url": "http://example.com/cache_test.ics"}
        ],
        "max_events": 10,
        "maximum_days": 7,
        "cache_dir": "/tmp/mock_cache_dir"
    }
    
    module = CalendarModule(config)
    
    # Mocking os/file operations and requests
    async def mock_get_fail(*args, **kwargs):
        raise httpx.RequestError("Network down")
        
    mock_exists = MagicMock(return_value=True)
    mock_open_ctx = MagicMock()
    mock_open_ctx.__enter__.return_value = MagicMock(read=MagicMock(return_value=MOCK_ICS.encode('utf-8')))
    
    with patch("httpx.AsyncClient.get", side_effect=mock_get_fail):
        with patch("os.path.exists", mock_exists):
            with patch("builtins.open", return_value=mock_open_ctx):
                client = MagicMock()
                data = await module.fetch_feed(client, config["calendars"][0]["url"])
                assert data == MOCK_ICS.encode('utf-8')

@pytest.mark.asyncio
async def test_calendars_config_robustness():
    # Case 1: "[object Object]" invalid string
    config1 = {
        "globals": {"timezone": "Europe/Stockholm"},
        "calendars": "[object Object]"
    }
    mod1 = CalendarModule(config1)
    assert mod1.calendars_cfg == []

    # Case 2: Single string URL
    config2 = {
        "globals": {"timezone": "Europe/Stockholm"},
        "calendars": "https://example.com/single.ics"
    }
    mod2 = CalendarModule(config2)
    assert len(mod2.calendars_cfg) == 1
    assert mod2.calendars_cfg[0]["url"] == "https://example.com/single.ics"
    assert mod2.calendars_cfg[0]["name"] == "Calendar"

    # Case 3: List of mixed strings and dicts
    config3 = {
        "globals": {"timezone": "Europe/Stockholm"},
        "calendars": [
            "https://example.com/string-url.ics",
            {"name": "Dict Calendar", "url": "https://example.com/dict-url.ics", "color": "#00ff00"}
        ]
    }
    mod3 = CalendarModule(config3)
    assert len(mod3.calendars_cfg) == 2
    assert mod3.calendars_cfg[0]["url"] == "https://example.com/string-url.ics"
    assert mod3.calendars_cfg[0]["name"] == "Calendar"
    assert mod3.calendars_cfg[1]["url"] == "https://example.com/dict-url.ics"
    assert mod3.calendars_cfg[1]["name"] == "Dict Calendar"
    assert mod3.calendars_cfg[1]["color"] == "#00ff00"

    # Case 4: JSON string array
    config4 = {
        "globals": {"timezone": "Europe/Stockholm"},
        "calendars": '[{"name": "JSON Cal", "url": "https://example.com/json.ics"}]'
    }
    mod4 = CalendarModule(config4)
    assert len(mod4.calendars_cfg) == 1
    assert mod4.calendars_cfg[0]["name"] == "JSON Cal"
    assert mod4.calendars_cfg[0]["url"] == "https://example.com/json.ics"

