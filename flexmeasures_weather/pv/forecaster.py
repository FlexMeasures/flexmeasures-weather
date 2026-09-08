"""A physical PV power forecaster (`--forecaster PVWattsForecaster`), driven by pvlib.
Turns an irradiance forecast into expected AC watts using the array's geometry - needs no
metered history, unlike the built-in LightGBM forecaster. See docs/pv-power-design.md.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd
from flask import current_app
from marshmallow import fields

from flexmeasures.data.config import db
from flexmeasures.data.models.forecasting import Forecaster
from flexmeasures.data.models.forecasting.utils import data_to_bdf
from flexmeasures.data.schemas.forecasting import ForecasterConfigSchema
from flexmeasures.data.schemas.forecasting.pipeline import ForecasterParametersSchema
from flexmeasures.data.schemas.sensors import SensorIdField
from flexmeasures.data.utils import save_to_db
from flexmeasures.utils.unit_utils import convert_units

from .. import DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE
from ..utils.locating import find_weather_sensor_by_location
from .specs import read_pv_specs
from .utils.forecast_shaping import (
    read_asset_location,
    to_wide_horizon_frame,
)
from .utils.solar_power import estimate_pv_power

IRRADIANCE_SENSOR_NAME = "irradiance"
IRRADIANCE_UNIT = "W/m²"


class PVWattsConfigSchema(ForecasterConfigSchema):
    """Static configuration of a PV power forecast. Everything optional: defaults are the
    asset's own specs (`flexmeasures_weather.pv.specs`) and the nearest registered
    irradiance sensor.
    """

    irradiance_sensor = SensorIdField(
        data_key="irradiance-sensor",
        required=False,
        metadata={
            "description": "ID of the sensor holding the irradiance (GHI) forecast in"
            f" {IRRADIANCE_UNIT}. Defaults to the nearest registered"
            f" '{IRRADIANCE_SENSOR_NAME}' weather sensor.",
            "example": 2092,
        },
    )
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


class PVWattsForecaster(Forecaster):
    """Forecast a PV array's AC power output from an irradiance forecast."""

    __version__ = "1"
    __author__ = "Elaad"

    _config_schema = PVWattsConfigSchema()
    # Not inherited from Forecaster: DataGenerator declares _parameters_schema as None,
    # and DataGenerator.compute would die on None.dump. Core wires this in for its own
    # pipeline only.
    _parameters_schema = ForecasterParametersSchema()

    @property
    def input_sensors(self) -> list:
        """The irradiance sensor we read, if it was configured.

        The base implementation reads the past/future/regressor config keys of the
        built-in pipeline, which this forecaster does not have. Nor does it read the
        forecast sensor's own history.
        """
        config = self._config or {}
        return self._resolve_sensors(config.get("irradiance_sensor"))

    def _compute_forecast(self, as_job: bool = False, **kwargs) -> list[dict[str, Any]]:
        if as_job:
            # The CLI reads an as_job return as pipeline_returns["n_jobs"], a differently
            # shaped result. Queueing would buy nothing anyway: pvlib runs in milliseconds.
            raise NotImplementedError(
                "PVWattsForecaster computes in milliseconds and does not support running"
                " as a job. Please drop the --as-job flag."
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
        irradiance_sensor = self._get_irradiance_sensor(latitude, longitude)

        # One viewpoint per forecast_frequency step, each looking max_forecast_horizon ahead
        viewpoint_starts = [
            kwargs["predict_start"] + i * kwargs["forecast_frequency"]
            for i in range(kwargs["m_viewpoints"])
        ]
        horizon_in_steps = kwargs["max_forecast_horizon"] // resolution
        ghi = self._load_ghi(
            irradiance_sensor,
            event_starts_after=viewpoint_starts[0],
            event_ends_before=viewpoint_starts[-1] + kwargs["max_forecast_horizon"],
            beliefs_before=kwargs["beliefs_before"],
            resolution=resolution,
            timezone=sensor.timezone,
        )
        power = estimate_pv_power(ghi, latitude=latitude, longitude=longitude, **specs)
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
        # Honour --end, and drop events we had no irradiance for
        event_starts = bdf.index.get_level_values("event_start")
        bdf = bdf[(event_starts < kwargs["end_date"]) & bdf["event_value"].notna()]

        # The forecaster is responsible for saving its own beliefs: neither
        # Forecaster._compute nor DataGenerator.compute writes anything to the database.
        save_to_db(bdf, save_changed_beliefs_only=False)
        db.session.commit()
        current_app.logger.info(
            "[FLEXMEASURES-WEATHER] Saved %d PV power beliefs to sensor %s (ID %s).",
            len(bdf),
            sensor_to_save.name,
            sensor_to_save.id,
        )

        return [{"sensor": sensor_to_save, "data": bdf}]

    def _get_irradiance_sensor(self, latitude: float, longitude: float):
        """The configured irradiance sensor, or else the nearest registered one."""
        configured = (self._config or {}).get("irradiance_sensor")
        if configured is not None:
            return configured

        max_degree_difference = current_app.config.get(
            "WEATHER_MAXIMAL_DEGREE_LOCATION_DISTANCE",
            DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE,
        )
        sensor = find_weather_sensor_by_location(
            (latitude, longitude),
            max_degree_difference_for_nearest_weather_sensor=max_degree_difference,
            sensor_name=IRRADIANCE_SENSOR_NAME,
        )
        if sensor is None:
            raise ValueError(
                "No irradiance sensor found within"
                f" {max_degree_difference} degrees of ({latitude}, {longitude})."
                " Register one with: flexmeasures weather register-weather-sensor"
                f" --name {IRRADIANCE_SENSOR_NAME} --latitude {latitude}"
                f" --longitude {longitude}"
                " (and then collect forecasts for it), or point at an existing sensor"
                " with the 'irradiance-sensor' config field."
            )
        return sensor

    @staticmethod
    def _load_ghi(
        irradiance_sensor,
        event_starts_after,
        event_ends_before,
        beliefs_before,
        resolution: timedelta,
        timezone: str,
    ) -> pd.Series:
        """Read global horizontal irradiance, resampled to the output resolution.

        `resolution` lets timely_beliefs resample, so the irradiance sensor need not
        share the output sensor's resolution.
        """
        beliefs = irradiance_sensor.search_beliefs(
            event_starts_after=event_starts_after,
            event_ends_before=event_ends_before,
            beliefs_before=beliefs_before,
            most_recent_beliefs_only=True,
            one_deterministic_belief_per_event=True,
            resolution=resolution,
        )
        if beliefs.empty:
            raise ValueError(
                f"No irradiance data on sensor {irradiance_sensor.name}"
                f" (ID {irradiance_sensor.id}) for events between"
                f" {event_starts_after} and {event_ends_before}."
                " Collect forecasts first, with:"
                " flexmeasures weather get-weather-forecasts"
            )
        ghi = convert_units(
            beliefs["event_value"].to_numpy(), irradiance_sensor.unit, IRRADIANCE_UNIT
        )
        index = beliefs.index.get_level_values("event_start").tz_convert(timezone)
        return pd.Series(ghi, index=index, name="ghi").sort_index()
