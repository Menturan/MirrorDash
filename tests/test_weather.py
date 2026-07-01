import pytest
import datetime
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock, AsyncMock, patch
from mirrordash_weather.plugin import WeatherModule

MOCK_SMHI_DATA = {
    "referenceTime": "2026-06-03T18:00:00Z",
    # Real SMHI API uses 'timeSeries', with 'time' and 'data' dict
    "timeSeries": [
        {
            "time": "2026-06-03T19:00:00Z",
            "data": {
                "air_temperature": 20.5,
                "wind_speed": 4.5,
                "wind_from_direction": 90.0,
                "symbol_code": 2,
                "precipitation_amount_mean": 0.2
            }
        },
        {
            "time": "2026-06-04T12:00:00Z",
            "data": {
                "air_temperature": 22.0,
                "wind_speed": 5.0,
                "wind_from_direction": 95.0,
                "symbol_code": 1,
                "precipitation_amount_mean": 0.0
            }
        }
    ]
}

MOCK_OPEN_METEO_DATA = {
    "current": {
        "temperature_2m": 20.5,
        "wind_speed_10m": 4.5,
        "wind_direction_10m": 90,
        "precipitation": 0.2,
        "weather_code": 1
    },
    "daily": {
        "time": ["2026-06-03", "2026-06-04"],
        "weather_code": [1, 0],
        "temperature_2m_max": [21.0, 23.0],
        "temperature_2m_min": [15.0, 16.0]
    }
}

MOCK_WEATHERAPI_DATA = {
    "current": {
        "temp_c": 20.5,
        "wind_kph": 16.2,  # 16.2 / 3.6 = 4.5 m/s
        "wind_degree": 90,
        "precip_mm": 0.2,
        "condition": {
            "code": 1003
        }
    },
    "forecast": {
        "forecastday": [
            {
                "date": "2026-06-03",
                "day": {
                    "maxtemp_c": 21.0,
                    "mintemp_c": 15.0,
                    "condition": {
                        "code": 1003
                    }
                }
            },
            {
                "date": "2026-06-04",
                "day": {
                    "maxtemp_c": 23.0,
                    "mintemp_c": 16.0,
                    "condition": {
                        "code": 1000
                    }
                }
            }
        ]
    }
}

MOCK_OPENWEATHER_DATA = {
    "list": [
        {
            "dt": 1780513200,  # 2026-06-03 19:00:00
            "main": {
                "temp": 20.5,
                "temp_min": 15.0,
                "temp_max": 21.0
            },
            "wind": {
                "speed": 4.5,
                "deg": 90
            },
            "rain": {
                "3h": 0.2
            },
            "weather": [
                {
                    "id": 801
                }
            ]
        },
        {
            "dt": 1780574400,  # 2026-06-04 12:00:00
            "main": {
                "temp": 22.0,
                "temp_min": 16.0,
                "temp_max": 23.0
            },
            "wind": {
                "speed": 5.0,
                "deg": 95
            },
            "weather": [
                {
                    "id": 800
                }
            ]
        }
    ]
}

@pytest.fixture
def base_config():
    return {
        "globals": {
            "language": "en",
            "timezone": "Europe/Stockholm",
            "time_format": "24h",
            "temperature_unit": "C",
            "distance_unit": "km",
            "latitude": 59.3293,
            "longitude": 18.0686
        },
        "provider": "smhi",
        "show_header": True,
        "show_wind": True,
        "show_precipitation": True,
        "show_forecast": True,
        "forecast_days": 4
    }

def test_weather_module_init_and_fallbacks(base_config):
    # Test fallback to globals
    module = WeatherModule(base_config)
    assert module.latitude == 59.3293
    assert module.longitude == 18.0686
    assert module.temp_unit == "C"
    assert module.dist_unit == "km"
    assert module.tz == ZoneInfo("Europe/Stockholm")
    
    # Test fallback to hardcoded default if globals are missing
    empty_config = {"globals": {}}
    module_default = WeatherModule(empty_config)
    assert module_default.latitude == 59.3293
    assert module_default.longitude == 18.0686
    assert module_default.tz == ZoneInfo("Europe/Stockholm")

def test_weather_module_unit_conversions(base_config):
    module = WeatherModule(base_config)
    
    # Celsius to Celsius (no conversion)
    assert module.convert_temp(20.5) == 20.5
    
    # Celsius to Fahrenheit conversion
    module.temp_unit = "F"
    assert module.convert_temp(20.0) == 68.0
    assert module.convert_temp(0.0) == 32.0
    
    # Wind speed conversion (m/s to m/s)
    module.dist_unit = "km"
    assert module.convert_wind_speed(5.0) == 5.0
    
    # Wind speed conversion (m/s to mph)
    module.dist_unit = "miles"
    assert module.convert_wind_speed(5.0) == 11.2  # 5 * 2.23694 = 11.1847

def test_parse_smhi(base_config):
    module = WeatherModule(base_config)
    today = datetime.date(2026, 6, 3)
    
    result = module.parse_smhi(MOCK_SMHI_DATA, today)
    assert result["current"]["temp"] == 20.5
    assert result["current"]["icon"] == "cloud-sun"
    assert result["current"]["condition"] == "Partly Cloudy"
    assert result["current"]["wind_speed"] == 4.5
    assert result["current"]["wind_dir"] == "E"  # 90 degrees
    assert result["current"]["precipitation"] == 0.2
    
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["day_name"] == "TOMORROW"
    assert result["forecast"][0]["temp_max"] == 22.0
    assert result["forecast"][0]["icon"] == "sun"

def test_parse_open_meteo(base_config):
    base_config["provider"] = "open_meteo"
    module = WeatherModule(base_config)
    today = datetime.date(2026, 6, 3)
    
    result = module.parse_open_meteo(MOCK_OPEN_METEO_DATA, today)
    assert result["current"]["temp"] == 20.5
    assert result["current"]["icon"] == "cloud-sun"
    assert result["current"]["wind_speed"] == 4.5
    assert result["current"]["wind_dir"] == "E"
    assert result["current"]["precipitation"] == 0.2
    
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["day_name"] == "TOMORROW"
    assert result["forecast"][0]["temp_max"] == 23.0
    assert result["forecast"][0]["temp_min"] == 16.0
    assert result["forecast"][0]["icon"] == "sun"

def test_parse_weatherapi(base_config):
    base_config["provider"] = "weatherapi"
    module = WeatherModule(base_config)
    today = datetime.date(2026, 6, 3)
    
    result = module.parse_weatherapi(MOCK_WEATHERAPI_DATA, today)
    assert result["current"]["temp"] == 20.5
    assert result["current"]["icon"] == "cloud-sun"
    assert result["current"]["wind_speed"] == 4.5  # converted from 16.2 kph
    assert result["current"]["wind_dir"] == "E"
    assert result["current"]["precipitation"] == 0.2
    
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["day_name"] == "TOMORROW"
    assert result["forecast"][0]["temp_max"] == 23.0
    assert result["forecast"][0]["icon"] == "sun"

def test_parse_openweathermap(base_config):
    base_config["provider"] = "openweathermap"
    module = WeatherModule(base_config)
    
    # Mock timezone to UTC to match openweather timestamps in Europe/Stockholm (+2 in June)
    # 1780513200 is June 3, 2026 19:00:00 Stockholm local time
    # Let's set module timezone to stockholm and mock today as June 3, 2026
    today = datetime.date(2026, 6, 3)
    
    result = module.parse_openweathermap(MOCK_OPENWEATHER_DATA, today)
    assert result["current"]["temp"] == 20.5
    assert result["current"]["icon"] == "cloud-sun"
    assert result["current"]["wind_speed"] == 4.5
    assert result["current"]["wind_dir"] == "E"
    assert result["current"]["precipitation"] == 0.2
    
    assert len(result["forecast"]) == 1
    assert result["forecast"][0]["day_name"] == "TOMORROW"
    assert result["forecast"][0]["temp_max"] == 23.0
    assert result["forecast"][0]["icon"] == "sun"

def test_parse_hourly_and_combined(base_config):
    # SMHI Hourly Parsing
    module = WeatherModule(base_config)
    today = datetime.date(2026, 6, 3)
    result = module.parse_smhi(MOCK_SMHI_DATA, today)
    assert "hourly_forecast" in result
    assert len(result["hourly_forecast"]) > 0
    assert result["hourly_forecast"][0]["time_label"] == "21:00" or result["hourly_forecast"][0]["time_label"] == "09 PM"
    assert result["hourly_forecast"][0]["temp"] == 20.5

    # Open-Meteo Hourly Parsing
    base_config["provider"] = "open_meteo"
    om_module = WeatherModule(base_config)
    om_data = MOCK_OPEN_METEO_DATA.copy()
    om_data["hourly"] = {
        "time": ["2026-06-03T19:00", "2026-06-03T20:00"],
        "temperature_2m": [20.5, 19.5],
        "weather_code": [1, 1]
    }
    om_result = om_module.parse_open_meteo(om_data, today)
    assert len(om_result["hourly_forecast"]) == 2
    assert om_result["hourly_forecast"][0]["time_label"] == "19:00"
    assert om_result["hourly_forecast"][0]["temp"] == 20.5

    # WeatherAPI Hourly Parsing
    base_config["provider"] = "weatherapi"
    wa_module = WeatherModule(base_config)
    wa_data = MOCK_WEATHERAPI_DATA.copy()
    wa_data["forecast"] = {
        "forecastday": [
            {
                "date": "2026-06-03",
                "day": {"maxtemp_c": 21.0, "mintemp_c": 15.0, "condition": {"code": 1003}},
                "hour": [
                    {"time": "2026-06-03 19:00", "temp_c": 20.5, "condition": {"code": 1003}}
                ]
            }
        ]
    }
    wa_result = wa_module.parse_weatherapi(wa_data, today)
    assert len(wa_result["hourly_forecast"]) == 1
    assert wa_result["hourly_forecast"][0]["time_label"] == "19:00"
    assert wa_result["hourly_forecast"][0]["temp"] == 20.5

    # OpenWeatherMap Hourly Parsing
    base_config["provider"] = "openweathermap"
    owm_module = WeatherModule(base_config)
    owm_result = owm_module.parse_openweathermap(MOCK_OPENWEATHER_DATA, today)
    assert len(owm_result["hourly_forecast"]) > 0
    assert owm_result["hourly_forecast"][0]["temp"] == 20.5

