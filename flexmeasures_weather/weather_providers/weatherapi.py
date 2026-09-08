from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from flexmeasures.utils.time_utils import as_server_time, get_timezone

from .base import HourlyForecast, TimestampedWeatherProvider


def process_weatherapi_data(
    data: list[dict[str, Any]], hour_no: int
) -> list[dict[str, Any]]:
    """
    Processes raw WeatherAPI forecast data into a flat list of hourly entries.

    Args:
        data: A list of forecast day dictionaries from WeatherAPI, each containing an
            'hour' key with 24 hourly entries.
        hour_no: The index of the current hour to start from.

    Returns:
        A list of 48 hourly forecast entries, raw WeatherAPI field names untouched.
    """
    first_day = data[0]["hour"]
    second_day = data[1]["hour"]
    third_day = data[2]["hour"]
    combined = first_day + second_day + third_day

    relevant = combined[hour_no : hour_no + 48]
    return relevant


class WeatherApiProvider(TimestampedWeatherProvider):
    """Wrapper around WeatherAPI's forecast API."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_hourly_forecast_with_as_of(
        self, latitude: float, longitude: float, days: int = 3, **kwargs: Any
    ) -> tuple[datetime, list[HourlyForecast]]:
        """
        Makes a request to the WeatherAPI to retrieve hourly weather forecast data.

        Args:
            latitude: Latitude of the location.
            longitude: Longitude of the location.
            days: Number of days to request the forecast for (default is 3, including
                current day).

        Returns:
            The API's own "as-of" timestamp, and a list of 48 hourly forecast records.
            Note that the first forecast is about the current hour. `wind_speed_mps` is
            converted from WeatherAPI's native kph to m/s here, so it's directly
            comparable to the other providers' records.

        Raises:
            AssertionError: If the response from the Weather API is not successful (HTTP status 200).
        """

        query_str = f"http://api.weatherapi.com/v1/forecast.json?key={self.api_key}&q={latitude},{longitude}&days={days}&aqi=yes&alerts=yes"
        res = requests.get(query_str)

        assert (
            res.status_code == 200
        ), f"Weather API returned status code {res.status_code}: {res.text}"

        data = res.json()

        # get the time of the api call
        time_of_call = int(data["location"]["localtime_epoch"])
        local_timezone = ZoneInfo(data["location"]["tz_id"])
        local_time = datetime.fromtimestamp(time_of_call, local_timezone)
        time_of_api_call = as_server_time(local_time)
        last_fetched_at = time_of_api_call.replace(second=0, microsecond=0)

        relevant = data["forecast"]["forecastday"]
        hour_no = local_time.hour

        hourly = process_weatherapi_data(relevant, hour_no)
        forecasts = [
            HourlyForecast(
                time=as_server_time(
                    datetime.fromtimestamp(fc["time_epoch"], get_timezone())
                ),
                temperature_c=fc.get("temp_c"),
                wind_speed_mps=_kph_to_mps(fc.get("wind_kph")),
                cloud_cover_fraction=_percent_to_fraction(fc.get("cloud")),
            )
            for fc in hourly
        ]
        return last_fetched_at, forecasts


def _kph_to_mps(value: float | None) -> float | None:
    return value / 3.6 if value is not None else None


def _percent_to_fraction(value: float | None) -> float | None:
    return value / 100.0 if value is not None else None
