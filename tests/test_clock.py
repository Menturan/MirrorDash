import pytest
import asyncio
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from mirrordash_clock.plugin import ClockModule, format_localized_date

def test_format_localized_date_english():
    # Thursday, June 4, 2026
    dt = datetime.datetime(2026, 6, 4, 12, 0, 0)
    
    assert format_localized_date(dt, "full", "en") == "Thursday, June 4, 2026"
    assert format_localized_date(dt, "long", "en") == "June 4, 2026"
    assert format_localized_date(dt, "medium", "en") == "Jun 4, 2026"
    assert format_localized_date(dt, "short", "en") == "6/4/26"
    assert format_localized_date(dt, "yyyy-MM-dd", "en") == "2026-06-04"

def test_format_localized_date_swedish():
    # Thursday, June 4, 2026 (torsdag, 4 juni 2026)
    dt = datetime.datetime(2026, 6, 4, 12, 0, 0)
    
    assert format_localized_date(dt, "full", "sv") == "Torsdag 4 juni 2026"
    assert format_localized_date(dt, "long", "sv") == "4 juni 2026"
    assert format_localized_date(dt, "medium", "sv") == "4 juni 2026"
    assert format_localized_date(dt, "short", "sv") == "2026-06-04"
    assert format_localized_date(dt, "yyyy-MM-dd", "sv") == "2026-06-04"

def test_format_localized_date_other_languages():
    dt = datetime.datetime(2026, 6, 4, 12, 0, 0)
    # German (Donnerstag, Juni)
    assert "Donnerstag" in format_localized_date(dt, "full", "de")
    assert "Juni" in format_localized_date(dt, "full", "de")
    # Spanish (jueves, junio)
    assert "Jueves" in format_localized_date(dt, "full", "es")
    assert "junio" in format_localized_date(dt, "full", "es")

@pytest.mark.asyncio
async def test_clock_module_run_loop():
    config = {
        "globals": {
            "time_format": "24h",
            "language": "sv",
            "timezone": "Europe/Stockholm"
        },
        "show_seconds": False,
        "show_header": True,
        "date_format": "full"
    }
    
    # Mock template rendering
    clock = ClockModule(config)
    clock.render_template = MagicMock(return_value="<div>Clock Rendered</div>")
    
    # Mock datetime.now
    fixed_time = datetime.datetime(2026, 6, 4, 14, 30, 0)
    
    # Patch datetime inside the clock module
    with patch("mirrordash_clock.plugin.datetime") as mock_dt, \
         patch("mirrordash_clock.plugin.asyncio.sleep", side_effect=asyncio.CancelledError):
        
        mock_dt.now.return_value = fixed_time
        
        broadcast_mock = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await clock.run_loop(broadcast_mock)
            
        broadcast_mock.assert_called_once_with("mirrordash_clock", "<div>Clock Rendered</div>")
        clock.render_template.assert_called_once_with(
            "clock.html",
            hm_str="14:30",
            s_str="",
            ampm_str="",
            date_str="Torsdag 4 juni 2026",
            show_header=True
        )
