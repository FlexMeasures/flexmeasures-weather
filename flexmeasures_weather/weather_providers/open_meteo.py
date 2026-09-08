from __future__ import annotations

from typing import Any

import pandas as pd
import requests
from pvlib.irradiance import complete_irradiance
from pvlib.location import Location

from .base import HourlyForecast, WeatherProvider

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs025"
# Open-Meteo pads with null past a model's real horizon regardless of what's asked for, so
# this asks for (close to) the maximum and relies on dropna() to find the real cutoff.
FORECAST_DAYS = 16
REQUEST_TIMEOUT_IN_SECONDS = 10


class OpenMeteoProvider(WeatherProvider):
    """Wrapper around Open-Meteo's (https://open-meteo.com) forecast API. Free, key-less.

    Returns native hourly GHI and DHI; DNI is backed out with
    `pvlib.irradiance.complete_irradiance`. `model` is required, not defaulted - which
    Open-Meteo model performs best depends on the deployment location, see
    docs/pv-power-design.md. `MODEL` (`ecmwf_ifs025`) is the value validated at one Dutch
    site.
    """

    def __init__(self, model: str):
        self.model = model

    def fetch_hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        days: int = FORECAST_DAYS,
        **kwargs: Any,
    ) -> list[HourlyForecast]:
        """Fetch an hourly Open-Meteo forecast, with DNI backed out from GHI and DHI.

        Args:
            latitude: Latitude of the site, in degrees.
            longitude: Longitude of the site, in degrees.
            days: How many days ahead to request from Open-Meteo.

        Returns:
            One `HourlyForecast` per hour, with `ghi_wm2`, `dni_wm2`, `dhi_wm2`,
            `temperature_c` and `wind_speed_mps` populated (`cloud_cover_fraction` stays
            None). Hours past the model's real horizon (all-null padding) are dropped.

        Raises:
            requests.HTTPError: on a non-2xx response.
        """
        params: dict[str, str | float] = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "shortwave_radiation,diffuse_radiation,temperature_2m,wind_speed_10m",
            "forecast_days": days,
            "models": self.model,
            "timezone": "auto",
        }
        response = requests.get(
            OPEN_METEO_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_IN_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        timezone = data["timezone"]
        hourly = data["hourly"]

        weather = pd.DataFrame(
            {
                "ghi": hourly["shortwave_radiation"],
                "dhi": hourly["diffuse_radiation"],
                "temperature": hourly["temperature_2m"],
                "wind_speed": hourly["wind_speed_10m"],
            },
            index=pd.to_datetime(hourly["time"]).tz_localize(timezone),
        )
        # Drop any trailing all-null padding past the model's real horizon.
        weather = weather.dropna(subset=["ghi", "dhi"])
        if weather.empty:
            return []

        site = Location(latitude, longitude, tz=weather.index.tz)
        zenith = site.get_solarposition(weather.index)["zenith"]
        weather["dni"] = complete_irradiance(
            zenith, ghi=weather["ghi"], dhi=weather["dhi"]
        )["dni"]
        return [
            HourlyForecast(
                time=time,
                temperature_c=_none_if_nan(row.temperature),
                wind_speed_mps=_none_if_nan(row.wind_speed),
                ghi_wm2=_none_if_nan(row.ghi),
                dni_wm2=_none_if_nan(row.dni),
                dhi_wm2=_none_if_nan(row.dhi),
            )
            for time, row in weather.iterrows()
        ]


def _none_if_nan(value: float) -> float | None:
    return None if pd.isna(value) else float(value)
