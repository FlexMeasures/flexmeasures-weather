from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from flask import current_app
from flexmeasures.utils.time_utils import as_server_time, get_timezone

from .base import HourlyForecast, TimestampedWeatherProvider


def check_openweathermap_version(api_version: str):
    supported_versions = ["2.5", "3.0"]
    if api_version not in supported_versions:
        current_app.logger.warning(
            f"This plugin may not be fully compatible with OpenWeatherMap API version {api_version}. We tested with versions {supported_versions}"
        )


class OpenWeatherMapProvider(TimestampedWeatherProvider):
    """Wrapper around the OpenWeatherMap "one-call" API."""

    API_VERSION = "3.0"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_hourly_forecast_with_as_of(
        self, latitude: float, longitude: float, **kwargs: Any
    ) -> tuple[datetime, list[HourlyForecast]]:
        """
        Make a single "one-call" to the Open Weather API and return the API's own
        "as-of" timestamp alongside the 48 hourly forecasts.
        See https://openweathermap.org/api/one-call-3 for docs.
        Note that the first forecast is about the current hour.
        """
        check_openweathermap_version(self.API_VERSION)
        query_str = f"lat={latitude}&lon={longitude}&units=metric&exclude=minutely,daily,alerts&appid={self.api_key}"
        res = requests.get(
            f"http://api.openweathermap.org/data/{self.API_VERSION}/onecall?{query_str}"
        )
        assert (
            res.status_code == 200
        ), f"OpenWeatherMap returned status code {res.status_code}: {res.text}"
        data = res.json()
        last_fetched_at = as_server_time(
            datetime.fromtimestamp(data["current"]["dt"], tz=get_timezone())
        ).replace(second=0, microsecond=0)

        forecasts = [
            HourlyForecast(
                time=as_server_time(datetime.fromtimestamp(fc["dt"], get_timezone())),
                temperature_c=fc.get("temp"),
                wind_speed_mps=fc.get("wind_speed"),
                cloud_cover_fraction=_percent_to_fraction(fc.get("clouds")),
            )
            for fc in data["hourly"]
        ]
        return last_fetched_at, forecasts


def _percent_to_fraction(value: float | None) -> float | None:
    return value / 100.0 if value is not None else None
