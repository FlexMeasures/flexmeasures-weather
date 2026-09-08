"""Shared contract for weather provider wrappers: all four converge on `HourlyForecast`,
with fields a given provider doesn't supply (e.g. OWM/WeatherAPI never report irradiance)
left `None`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pydantic
import pydantic.dataclasses


@pydantic.dataclasses.dataclass(frozen=True)
class HourlyForecast:
    """One hour of forecast data, normalized across providers.

    Fields a provider doesn't supply stay None - callers check what they need.
    `time` is validated tz-aware at construction time; a naive datetime raises.
    """

    time: pydantic.AwareDatetime  # interval start
    temperature_c: float | None = None  # deg C
    wind_speed_mps: float | None = None  # m/s
    cloud_cover_fraction: float | None = None  # 0-1
    ghi_wm2: float | None = None  # W/m^2
    dni_wm2: float | None = None  # W/m^2
    dhi_wm2: float | None = None  # W/m^2


class WeatherProvider(ABC):
    """Abstract base for a weather provider wrapper.

    Has no "as-of" timestamp of its own (Bright Sky, Open-Meteo). Providers that do
    report one subclass `TimestampedWeatherProvider` instead.
    """

    @abstractmethod
    def fetch_hourly_forecast(
        self, latitude: float, longitude: float, **kwargs: Any
    ) -> list[HourlyForecast]:
        """Fetch an hourly forecast for the given location.

        Subclasses may add provider-specific keyword params (e.g. Bright Sky's required
        `timezone`).
        """
        raise NotImplementedError

    def fetch_hourly_forecast_with_as_of(
        self, latitude: float, longitude: float, **kwargs: Any
    ) -> tuple[datetime | None, list[HourlyForecast]]:
        """Same as `fetch_hourly_forecast`, plus the provider's own "as-of" timestamp,
        when it has one - `None` otherwise. Callers that don't care which kind of
        provider they hold can always call this one.
        """
        return None, self.fetch_hourly_forecast(latitude, longitude, **kwargs)


class TimestampedWeatherProvider(WeatherProvider):
    """Abstract base for a weather provider wrapper that reports its own "as-of"
    timestamp (OWM, WeatherAPI).
    """

    @abstractmethod
    def fetch_hourly_forecast_with_as_of(
        self, latitude: float, longitude: float, **kwargs: Any
    ) -> tuple[datetime, list[HourlyForecast]]:
        """Same as `fetch_hourly_forecast`, but also returns the API's own "as-of"
        timestamp alongside the forecasts.
        """
        raise NotImplementedError

    def fetch_hourly_forecast(
        self, latitude: float, longitude: float, **kwargs: Any
    ) -> list[HourlyForecast]:
        _, forecasts = self.fetch_hourly_forecast_with_as_of(
            latitude, longitude, **kwargs
        )
        return forecasts
