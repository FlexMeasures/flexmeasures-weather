"""Blended PV power forecaster (`--forecaster BlendedPVForecaster`): averages independent
PVWatts runs from two free, key-less weather providers (Bright Sky, Open-Meteo), fetched
live instead of reading a pre-collected irradiance sensor. See docs/pv-power-design.md.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import pandas as pd
from flask import current_app
from marshmallow import fields

from flexmeasures.data.config import db
from flexmeasures.data.models.forecasting import Forecaster
from flexmeasures.data.models.forecasting.utils import data_to_bdf
from flexmeasures.data.schemas.forecasting import ForecasterConfigSchema
from flexmeasures.data.schemas.forecasting.pipeline import ForecasterParametersSchema
from flexmeasures.data.utils import save_to_db
from flexmeasures.utils.unit_utils import convert_units

from flexmeasures_weather.weather_providers import (
    BrightSkyProvider,
    OpenMeteoProvider,
    WeatherProvider,
)
from flexmeasures_weather.weather_providers.open_meteo import MODEL as OPEN_METEO_MODEL

from .specs import read_pv_specs
from .utils.blend import blend_power
from .utils.forecast_shaping import read_asset_location, to_wide_horizon_frame
from .utils.solar_power import estimate_pv_power, estimate_pv_power_from_components


class BlendedPVConfigSchema(ForecasterConfigSchema):
    """Array config for a blended PV power forecast: tilt, azimuth, capacity, losses, mount.
    Same fields as `PVWattsForecaster`'s config, minus `irradiance-sensor` - both
    providers are fetched live here, not read from a pre-collected sensor.
    """

    tilt = fields.Float(
        required=False,
        metadata={"description": "Overrides the asset's 'pv_tilt' attribute."},
    )
    azimuth = fields.Float(
        required=False,
        metadata={"description": "Overrides the asset's 'pv_azimuth' attribute."},
    )
    capacity = fields.Float(
        required=False,
        metadata={
            "description": "Overrides the asset's 'pv_capacity_in_kw' attribute, in kW."
        },
    )
    losses = fields.Float(
        required=False,
        metadata={"description": "Overrides the asset's 'pv_losses' attribute."},
    )
    mount = fields.Str(
        required=False,
        metadata={"description": "Overrides the asset's 'pv_mount' attribute."},
    )


@dataclasses.dataclass(frozen=True)
class ProviderRun:
    """One weather provider to fetch and run through PVWatts before blending.
    `fetch_kwargs` are passed to `provider.fetch_hourly_forecast` alongside
    latitude/longitude/timezone, for per-call knobs such as `days`.
    """

    name: str
    provider: WeatherProvider
    fetch_kwargs: dict[str, Any] = dataclasses.field(default_factory=dict)


class BlendedPVForecaster(Forecaster):
    """Forecasts a PV array's AC power as the average of independent, free-provider
    PVWatts runs.

    Blends Bright Sky and Open-Meteo (`ecmwf_ifs025`) by default - the pair validated in
    docs/pv-power-design.md. A different blend can be injected via the `providers`
    constructor argument (`BlendedPVForecaster(providers=[OpenMeteoProvider(model=...)])`);
    the CLI has no way to pass that, so `--forecaster BlendedPVForecaster` always gets the
    default blend.
    """

    __version__ = "1"
    __author__ = "Elaad"

    _config_schema = BlendedPVConfigSchema()
    # Not inherited from Forecaster: see the identical note in PVWattsForecaster.
    _parameters_schema = ForecasterParametersSchema()

    def __init__(self, providers: list[WeatherProvider] | None = None, **kwargs):
        """:param providers: Weather providers to blend, already fully
        configured (e.g. `OpenMeteoProvider(model=...)`). Defaults to the
        validated Bright Sky + Open-Meteo pair (see the class docstring).
        """
        super().__init__(**kwargs)
        self._injected_providers = providers

    @property
    def input_sensors(self) -> list:
        """Empty: both providers are fetched live over HTTP, not read off a sensor."""
        return []

    def _compute_forecast(self, as_job: bool = False, **kwargs) -> list[dict[str, Any]]:
        if as_job:
            # Both provider fetches plus two pvlib PVWatts runs take well under a second;
            # queueing would buy nothing, and the CLI reads an as_job return differently.
            raise NotImplementedError(
                "BlendedPVForecaster computes in well under a second and does not support"
                " running as a job. Please drop the --as-job flag."
            )

        sensor = kwargs["sensor"]
        sensor_to_save = kwargs["sensor_to_save"]
        resolution = sensor.event_resolution
        config = self._config or {}

        asset = sensor.generic_asset
        specs = read_pv_specs(
            asset,
            overrides={
                key: config.get(key)
                for key in ("tilt", "azimuth", "capacity", "losses", "mount")
            },
        )
        latitude, longitude = read_asset_location(asset)

        viewpoint_starts = [
            kwargs["predict_start"] + i * kwargs["forecast_frequency"]
            for i in range(kwargs["m_viewpoints"])
        ]
        horizon_in_steps = kwargs["max_forecast_horizon"] // resolution
        forecast_days = _forecast_days_needed(
            viewpoint_starts, kwargs["max_forecast_horizon"], sensor.timezone
        )

        power = self._blended_power(
            self._providers(forecast_days),
            latitude,
            longitude,
            sensor.timezone,
            specs,
        )
        power = pd.Series(
            convert_units(power.to_numpy(), "W", sensor_to_save.unit),
            index=power.index,
        )

        data = to_wide_horizon_frame(
            power=power,
            viewpoint_starts=viewpoint_starts,
            belief_times=[
                kwargs["save_belief_time"] + i * kwargs["forecast_frequency"]
                for i in range(kwargs["m_viewpoints"])
            ],
            horizon_in_steps=horizon_in_steps,
            resolution=resolution,
        )
        bdf = data_to_bdf(
            data=data,
            horizon=horizon_in_steps,
            probabilistic=False,
            target_sensor=sensor,
            sensor_to_save=sensor_to_save,
            data_source=self.data_source,
        )
        # Honour --end, and drop events past whichever provider's horizon ran out first
        # (or both, for events beyond ~15 days out).
        event_starts = bdf.index.get_level_values("event_start")
        bdf = bdf[(event_starts < kwargs["end_date"]) & bdf["event_value"].notna()]

        # The forecaster is responsible for saving its own beliefs: neither
        # Forecaster._compute nor DataGenerator.compute writes anything to the database.
        save_to_db(bdf, save_changed_beliefs_only=False)
        db.session.commit()
        current_app.logger.info(
            "[FLEXMEASURES-WEATHER] Saved %d blended PV power beliefs to sensor %s"
            " (ID %s).",
            len(bdf),
            sensor_to_save.name,
            sensor_to_save.id,
        )

        return [{"sensor": sensor_to_save, "data": bdf}]

    def _providers(self, forecast_days: int) -> list[ProviderRun]:
        """Wrap the providers to fetch and blend with this run's `forecast_days`. Uses
        whatever was injected via the `providers` constructor argument; falls back to
        the default Bright Sky + Open-Meteo pair otherwise.
        """
        providers = self._injected_providers or [
            BrightSkyProvider(),
            OpenMeteoProvider(model=OPEN_METEO_MODEL),
        ]
        return [
            ProviderRun(
                name=type(provider).__name__,
                provider=provider,
                fetch_kwargs={"days": forecast_days},
            )
            for provider in providers
        ]

    @staticmethod
    def _blended_power(
        providers: list[ProviderRun],
        latitude: float,
        longitude: float,
        timezone: str,
        specs: dict,
    ) -> pd.Series:
        """Average one independent PVWatts run per provider. Each provider gets its own
        full PVWatts run (own DNI/DHI split, temperature, wind) rather than averaging raw
        GHI first - see docs/pv-power-design.md.
        """
        power_series = []
        for run in providers:
            records = run.provider.fetch_hourly_forecast(
                latitude, longitude, timezone=timezone, **run.fetch_kwargs
            )
            frame = _as_frame(records)
            power = _estimate_power(frame, latitude, longitude, specs)
            power_series.append(power.tz_convert(timezone))

        return blend_power(*power_series)


def _forecast_days_needed(
    viewpoint_starts: list[pd.Timestamp],
    max_forecast_horizon: pd.Timedelta,
    timezone: str,
) -> int:
    """How many days ahead to request from the providers, for this forecast run. Not a
    config value: covers the furthest event the run actually needs - the last viewpoint
    plus the max forecast horizon, `+ 1` for providers fetching whole calendar days.
    """
    furthest_event = max(viewpoint_starts) + max_forecast_horizon
    now = pd.Timestamp.now(tz=timezone)
    days = math.ceil((furthest_event - now) / pd.Timedelta(days=1))
    return max(1, days) + 1


def _estimate_power(
    frame: pd.DataFrame, latitude: float, longitude: float, specs: dict
) -> pd.Series:
    """Run PVWatts on one provider's forecast, using its DNI/DHI split if it has one
    (detected from the data, not hardcoded per provider) - see
    `estimate_pv_power_from_components` vs. `estimate_pv_power`.
    """
    if frame["dni_wm2"].notna().any() and frame["dhi_wm2"].notna().any():
        return estimate_pv_power_from_components(
            frame["ghi_wm2"],
            frame["dni_wm2"],
            frame["dhi_wm2"],
            latitude=latitude,
            longitude=longitude,
            temperature=frame["temperature_c"],
            wind_speed=frame["wind_speed_mps"],
            **specs,
        )
    return estimate_pv_power(
        frame["ghi_wm2"],
        latitude=latitude,
        longitude=longitude,
        temperature=frame["temperature_c"],
        wind_speed=frame["wind_speed_mps"],
        **specs,
    )


def _as_frame(records: list) -> pd.DataFrame:
    """Convert a provider's `list[HourlyForecast]` into a DataFrame indexed by
    `time`, matching the pd.Series inputs `estimate_pv_power` and
    `estimate_pv_power_from_components` need."""
    if not records:
        return pd.DataFrame(
            columns=[
                "temperature_c",
                "wind_speed_mps",
                "ghi_wm2",
                "dni_wm2",
                "dhi_wm2",
            ],
            index=pd.DatetimeIndex([], name="time"),
        )
    return pd.DataFrame([dataclasses.asdict(record) for record in records]).set_index(
        "time"
    )
