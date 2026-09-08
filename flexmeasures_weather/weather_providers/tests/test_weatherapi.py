"""Tests for the WeatherAPI fetch.

Unlike Bright Sky/Open-Meteo, this provider calls `as_server_time`/`get_timezone`, which
need a Flask app context - so these run with the plugin's normal conftest (the `app`
fixture), not `--noconftest`.
"""

import pytest

from flexmeasures_weather.weather_providers.weatherapi import WeatherApiProvider

LATITUDE = 52.09
LONGITUDE = 5.12


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _payload(hour_temp_wind_cloud):
    """Build a minimal WeatherAPI forecast response with one forecastday, `len(...)` hours
    starting at local hour 0, so `hour_no` (derived from `localtime_epoch`) is 0 and every
    entry ends up in the returned 48-hour window."""
    hours = [
        {
            "time_epoch": index * 3600,
            "temp_c": temp,
            "wind_kph": wind,
            "cloud": cloud,
        }
        for index, (temp, wind, cloud) in enumerate(hour_temp_wind_cloud)
    ]
    day = {"hour": hours}
    return {
        "location": {"localtime_epoch": 0, "tz_id": "UTC"},
        "forecast": {"forecastday": [day, day, day]},
    }


def test_wind_speed_is_converted_from_kph_to_mps(app, monkeypatch):
    """Regression guard: `HourlyForecast.wind_speed_mps` is documented as m/s regardless of
    provider - WeatherAPI's native unit is kph, so the /3.6 conversion has to happen inside
    this provider (`save_forecasts_in_db` no longer does any provider-specific conversion).
    """
    import requests

    payload = _payload([(21.0, 100.0, 40)])
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))

    forecasts = WeatherApiProvider(api_key="dummy").fetch_hourly_forecast(
        LATITUDE, LONGITUDE
    )

    assert forecasts[0].wind_speed_mps == pytest.approx(100.0 / 3.6)


def test_temperature_and_cloud_cover_pass_through(app, monkeypatch):
    """WeatherAPI's native `cloud` is a percent (0-100); `cloud_cover_fraction` stores a
    0-1 ratio, so the provider divides by 100."""
    import requests

    payload = _payload([(21.0, 100.0, 40)])
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(payload))

    forecasts = WeatherApiProvider(api_key="dummy").fetch_hourly_forecast(
        LATITUDE, LONGITUDE
    )

    assert forecasts[0].temperature_c == pytest.approx(21.0)
    assert forecasts[0].cloud_cover_fraction == pytest.approx(0.4)
