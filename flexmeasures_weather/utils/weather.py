from __future__ import annotations

from typing import Tuple, List, Dict, Optional, Callable
import dataclasses
import os
from datetime import datetime, timedelta
import json

import click
from flask import current_app
from humanize import naturaldelta
from timely_beliefs import BeliefsDataFrame
from flexmeasures.utils.time_utils import as_server_time, server_now
from flexmeasures.data.models.time_series import Sensor, TimedBelief
from flexmeasures.data.utils import save_to_db

from flexmeasures_weather import DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE
from flexmeasures_weather.weather_providers import (
    HourlyForecast,
    OpenWeatherMapProvider,
    WeatherApiProvider,
    WeatherProvider,
)
from .locating import find_weather_sensor_by_location
from ..sensor_specs import mapping
from .modeling import (
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
)
from .radiating import compute_irradiance

# Registry of supported providers, keyed by WEATHER_PROVIDER config value. Each factory
# takes the api_key and returns a ready-to-use provider instance.
PROVIDERS: Dict[str, Tuple[str, Callable[[str], WeatherProvider]]] = {
    "OWM": ("Open Weather Map", OpenWeatherMapProvider),
    "WAPI": ("Weather API", WeatherApiProvider),
}


def get_supported_sensor_spec(name: str) -> Optional[dict]:
    """
    Find the specs from a sensor by name.
    """
    for supported_sensor_spec in mapping:
        if supported_sensor_spec["fm_sensor_name"] == name:
            return supported_sensor_spec.copy()
    return None


def get_supported_sensors_str() -> str:
    """A string - list of supported sensors, also revealing their unit"""
    return ", ".join(
        [
            f"{sensor_specs['fm_sensor_name']} ({sensor_specs['unit']})"
            for sensor_specs in mapping
        ]
    )


def call_api(
    api_key: str, location: Tuple[float, float]
) -> Tuple[Optional[datetime], List[HourlyForecast]]:
    """
    Dispatches the weather API call based on the configured provider.

    Args:
        api_key (str): API key for the selected weather service provider.
        location (Tuple[float, float]): Latitude and longitude tuple.

    Returns:
        Tuple[Optional[datetime], List[HourlyForecast]]:
            - Timestamp of the API call, if the provider reports one.
            - List of hourly forecast records.

    Raises:
        Exception: If an invalid weather provider is configured.
    """

    provider = str(current_app.config.get("WEATHER_PROVIDER", "OWM"))
    if provider not in PROVIDERS:
        raise Exception(
            f"Invalid provider name. Please set WEATHER_PROVIDER setting in config file to one of {list(PROVIDERS)}."
        )

    provider_label, provider_class = PROVIDERS[provider]
    click.secho(f"Calling {provider_label}")
    weather_provider = provider_class(api_key)

    return weather_provider.fetch_hourly_forecast_with_as_of(*location)


def save_forecasts_in_db(  # noqa: C901
    api_key: str,
    locations: List[Tuple[float, float]],
):
    """Process the response from Weather Provider API into timed beliefs.
    Collects all forecasts for all locations and all sensors at all locations, then bulk-saves them.
    """
    click.echo("[FLEXMEASURES-WEATHER] Getting weather forecasts:")
    click.echo("[FLEXMEASURES-WEATHER] Latitude, Longitude")
    click.echo("[FLEXMEASURES-WEATHER] -----------------------")
    max_degree_difference_for_nearest_weather_sensor = current_app.config.get(
        "WEATHER_MAXIMAL_DEGREE_LOCATION_DISTANCE",
        DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE,
    )
    provider = str(current_app.config.get("WEATHER_PROVIDER", ""))
    if provider not in PROVIDERS:
        raise Exception(
            f"Invalid provider name. Please set WEATHER_PROVIDER setting in config file to one of {list(PROVIDERS)}."
        )
    for location in locations:
        click.echo("[FLEXMEASURES] %s, %s" % location)
        weather_sensors: Dict[str, Sensor] = (
            {}
        )  # keep track of the sensors to save lookups
        db_forecasts: Dict[Sensor, List[TimedBelief]] = {}  # collect beliefs per sensor

        now = server_now()
        time_of_api_call, forecasts = call_api(api_key, location)
        if time_of_api_call is not None:
            diff_fm_owm = now - time_of_api_call
            if abs(diff_fm_owm) > timedelta(minutes=10):
                click.echo(
                    f"[FLEXMEASURES-WEATHER] Warning: difference between this server and Weather Provider is {naturaldelta(diff_fm_owm)}"
                )
        click.echo(
            f"[FLEXMEASURES-WEATHER] Called weather provider {provider} API successfully at {now}."
        )

        # loop through forecasts, including the one of current hour (horizon 0)
        for fc in forecasts:
            fc_datetime = as_server_time(fc.time)
            click.echo(
                f"[FLEXMEASURES-WEATHER] Processing forecast for {fc_datetime} ..."
            )
            data_source = get_or_create_owm_data_source()
            for sensor_specs in mapping:
                sensor_name = str(sensor_specs["fm_sensor_name"])
                source_field = str(sensor_specs["source_field"])
                if getattr(fc, source_field, None) is not None:
                    weather_sensor = get_weather_sensor(
                        sensor_specs,
                        location,
                        weather_sensors,
                        max_degree_difference_for_nearest_weather_sensor,
                    )
                    if weather_sensor is not None:
                        click.echo(
                            f"Found pre-configured weather sensor {weather_sensor.name} ..."
                        )
                        if weather_sensor not in db_forecasts.keys():
                            db_forecasts[weather_sensor] = []

                        fc_value = getattr(fc, source_field)

                        # the irradiance is not available in Provider -> we compute it ourselves
                        if sensor_name == "irradiance":
                            fc_value = compute_irradiance(
                                location[0],
                                location[1],
                                fc_datetime,
                                # HourlyForecast.cloud_cover_fraction is already a 0-1 ratio
                                fc_value,
                            )
                            data_source = (
                                get_or_create_owm_data_source_for_derived_data()
                            )
                        elif sensor_name == "cloud cover":
                            # The "cloud cover" sensor stores a percent, but
                            # HourlyForecast.cloud_cover_fraction is a 0-1 ratio.
                            fc_value = fc_value * 100.0

                        db_forecasts[weather_sensor].append(
                            TimedBelief(
                                event_start=fc_datetime,
                                belief_time=now,
                                event_value=fc_value,
                                sensor=weather_sensor,
                                source=data_source,
                            )
                        )
                else:
                    # we will not fail here, but issue a warning
                    msg = "No value for '%s' in response data for time %s" % (
                        source_field,
                        fc_datetime,
                    )
                    click.echo("[FLEXMEASURES-WEATHER] %s" % msg)
                    current_app.logger.warning(msg)
    for sensor in db_forecasts.keys():
        click.echo(f"[FLEXMEASURES-WEATHER] Saving {sensor.name} forecasts ...")
        if len(db_forecasts[sensor]) == 0:
            # This is probably a serious problem
            raise Exception(
                "Nothing to put in the database was produced. That does not seem right..."
            )
        status = save_to_db(BeliefsDataFrame(db_forecasts[sensor]))
        if status == "success_but_nothing_new":
            current_app.logger.info(
                "[FLEXMEASURES-WEATHER] Done. These beliefs had already been saved before."
            )
        elif status == "success_with_unchanged_beliefs_skipped":
            current_app.logger.info(
                "[FLEXMEASURES-WEATHER] Done. Some beliefs had already been saved before."
            )


def get_weather_sensor(
    sensor_specs: dict,
    location: Tuple[float, float],
    weather_sensors: Dict[str, Sensor],
    max_degree_difference_for_nearest_weather_sensor: int,
) -> Sensor | None:
    """Get the weather sensor for this own response label and location, if we haven't retrieved it already."""
    sensor_name = str(sensor_specs["fm_sensor_name"])
    if sensor_name in weather_sensors:
        weather_sensor = weather_sensors[sensor_name]
    else:
        weather_sensor = find_weather_sensor_by_location(
            location,
            max_degree_difference_for_nearest_weather_sensor,
            sensor_name=sensor_name,
        )
        weather_sensors[sensor_name] = weather_sensor
    if (
        weather_sensor is not None
        and weather_sensor.event_resolution != sensor_specs["event_resolution"]
    ):
        raise Exception(
            f"[FLEXMEASURES-WEATHER] The weather sensor found for {sensor_name} has an unfitting event resolution (should be {sensor_specs['event_resolution']}, but is {weather_sensor.event_resolution}."
        )
    return weather_sensor


def save_forecasts_as_json(
    api_key: str, locations: List[Tuple[float, float]], data_path: str
):
    """Get forecasts, then store each as a raw JSON file, for later processing."""
    click.echo("[FLEXMEASURES-WEATHER] Getting weather forecasts:")
    click.echo("[FLEXMEASURES-WEATHER] Latitude, Longitude")
    click.echo("[FLEXMEASURES-WEATHER] ----------------------")
    for location in locations:
        click.echo("[FLEXMEASURES-WEATHER] %s, %s" % location)
        now = server_now()
        time_of_api_call, forecasts = call_api(api_key, location)
        if time_of_api_call is not None:
            diff_fm_owm = now - time_of_api_call
            if abs(diff_fm_owm) > timedelta(minutes=10):
                click.echo(
                    f"[FLEXMEASURES-WEATHER] Warning: difference between this server and Weather Provider is {naturaldelta(diff_fm_owm)}"
                )
        now_str = now.strftime("%Y-%m-%dT%H-%M-%S")
        path_to_files = os.path.join(data_path, now_str)
        if not os.path.exists(path_to_files):
            click.echo(f"[FLEXMEASURES-WEATHER] Making directory: {path_to_files} ...")
            os.mkdir(path_to_files)
        forecasts_file = "%s/forecast_lat_%s_lng_%s.json" % (
            path_to_files,
            str(location[0]),
            str(location[1]),
        )
        with open(forecasts_file, "w") as outfile:
            # `default=str` covers the "time" field's datetime, not JSON-serializable
            # out of the box.
            # HourlyForecast is a pydantic dataclass; mypy's dataclasses.asdict overloads
            # don't recognize it as a DataclassInstance even though it works at runtime.
            json.dump(
                [dataclasses.asdict(fc) for fc in forecasts],  # type: ignore[call-overload]
                outfile,
                default=str,
            )
