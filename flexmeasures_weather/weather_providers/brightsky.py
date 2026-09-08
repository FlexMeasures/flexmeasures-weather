from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd
import requests
from flask import current_app
from pvlib.irradiance import campbell_norman
from pvlib.location import Location

from .base import HourlyForecast, WeatherProvider

BRIGHTSKY_URL = "https://api.brightsky.dev/weather"
# MOSMIX station forecast horizon - verified live against api.brightsky.dev/sources, at
# one site in the Netherlands.
FORECAST_DAYS = 10
REQUEST_TIMEOUT_IN_SECONDS = 10
# Below this fraction of native (non-fallback) GHI, fetch_hourly_forecast warns.
MINIMUM_NATIVE_GHI_FRACTION = 0.5


class BrightSkyProvider(WeatherProvider):
    """Wrapper around Bright Sky's (https://brightsky.dev) `/weather` endpoint, DWD's
    MOSMIX station forecast. Free, key-less.

    Its native hourly `solar` field is used as GHI; when null for a station, falls back
    to a `campbell_norman` cloud-cover estimate. See docs/pv-power-design.md.
    """

    def fetch_hourly_forecast(  # type: ignore[override]
        self,
        latitude: float,
        longitude: float,
        timezone: str,
        days: int = FORECAST_DAYS,
        now: pd.Timestamp | None = None,
        **kwargs: Any,
    ) -> list[HourlyForecast]:
        """Fetch an hourly Bright Sky forecast, with GHI resolved to a usable series.

        Args:
            latitude: Latitude of the site, in degrees.
            longitude: Longitude of the site, in degrees.
            timezone: IANA timezone name for the returned index (Bright Sky has no
                location-timezone lookup of its own, so the caller supplies one).
            days: How many days ahead to request.
            now: Reference "today", mainly for tests. Defaults to the current time in
                `timezone`.

        Returns:
            One `HourlyForecast` per hour, with `ghi_wm2`, `temperature_c` and
            `wind_speed_mps` populated (`cloud_cover_fraction`/`dni_wm2`/`dhi_wm2` stay
            None).

        Raises:
            requests.HTTPError: on a non-2xx response.
        """
        now = pd.Timestamp.now(tz=timezone) if now is None else now
        today = now.normalize()
        last_date = today + timedelta(days=days)

        params: dict[str, str | float] = {
            "lat": latitude,
            "lon": longitude,
            "date": today.strftime("%Y-%m-%d"),
            "last_date": last_date.strftime("%Y-%m-%d"),
            "tz": timezone,
        }
        response = requests.get(
            BRIGHTSKY_URL,
            params=params,
            timeout=REQUEST_TIMEOUT_IN_SECONDS,
        )
        response.raise_for_status()
        records = response.json()["weather"]
        if not records:
            return []
        return self._to_forecasts(records, latitude, longitude, timezone)

    @staticmethod
    def _to_forecasts(
        records: list[dict[str, Any]],
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> list[HourlyForecast]:

        raw = pd.DataFrame.from_records(records)
        raw.index = pd.to_datetime(raw["timestamp"])
        raw = raw.sort_index()
        # Bright Sky returns fixed UTC-offset timestamps (e.g. "+02:00"), not an IANA zone
        # name; pvlib/zoneinfo need the latter to know about DST transitions.
        raw.index = raw.index.tz_convert(timezone)

        solar_kwh_per_m2 = pd.to_numeric(raw["solar"], errors="coerce")
        # `solar` is an end-of-interval trailing-60-minute sum, not interval-start like this
        # plugin's other fields - relabel it onto the interval it describes.
        ghi_native = (solar_kwh_per_m2 * 1000.0).shift(-1)

        native_fraction = ghi_native.notna().mean() if len(ghi_native) else 1.0
        if native_fraction < MINIMUM_NATIVE_GHI_FRACTION:
            current_app.logger.warning(
                f"[FLEXMEASURES-WEATHER] Bright Sky's native 'solar' field was populated"
                f" for only {native_fraction * 100:.0f}% of the fetched hours near"
                f" ({latitude}, {longitude}); the rest fell back to a cloud-cover-derived"
                " clear-sky estimate (campbell_norman), which is considerably less"
                " accurate. This is a per-station DWD MOSMIX limitation - check"
                f" https://api.brightsky.dev/weather?lat={latitude}&lon={longitude}"
                " directly to confirm whether 'solar' is usable at this location."
            )

        cloud_fraction = (
            pd.to_numeric(raw["cloud_cover"], errors="coerce") / 100.0
        ).fillna(1.0)
        site = Location(latitude, longitude, tz=timezone)
        zenith = site.get_solarposition(raw.index)["zenith"]
        transmittance = (1 - cloud_fraction) * 0.75
        ghi_fallback = campbell_norman(zenith, transmittance)["ghi"]

        ghi = ghi_native.combine_first(ghi_fallback).clip(lower=0)

        weather = pd.DataFrame(
            {
                "ghi": ghi,
                "temperature": pd.to_numeric(raw["temperature"], errors="coerce"),
                "wind_speed": pd.to_numeric(raw["wind_speed"], errors="coerce") / 3.6,
            },
            index=raw.index,
        )
        return [
            HourlyForecast(
                time=time,
                temperature_c=_none_if_nan(row.temperature),
                wind_speed_mps=_none_if_nan(row.wind_speed),
                ghi_wm2=_none_if_nan(row.ghi),
            )
            for time, row in weather.iterrows()
        ]


def _none_if_nan(value: float) -> float | None:
    return None if pd.isna(value) else float(value)
