from __future__ import annotations

from typing import Tuple, List, Dict, Optional, Any
import os
from datetime import datetime, timedelta
import json

import click
from flask import current_app
import requests
from humanize import naturaldelta
from timely_beliefs import BeliefsDataFrame
from flexmeasures.utils.time_utils import as_server_time, get_timezone, server_now
from flexmeasures.data.models.time_series import Sensor, TimedBelief
from flexmeasures.data.utils import save_to_db

from flexmeasures_weather import DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE
from .locating import find_weather_sensor_by_location
from ..sensor_specs import mapping
from .modeling import (
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
)
from .radiating import compute_irradiance
from zoneinfo import ZoneInfo

API_VERSION = "3.0"


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


def process_weatherapi_data(
    data: List[Dict[str, Any]], hour_no: int
) -> List[Dict[str, Any]]:
    """
    Processes raw WeatherAPI forecast data into a format similar to OpenWeatherMap's format.

    Args:
        data (List[Dict[str, Any]]): A list of forecast day dictionaries from WeatherAPI,
            each containing an 'hour' key with 24 hourly entries.
        hour_no (int): The index of the current hour to start from.

    Returns:
        List[Dict[str, Any]]: A list of 48 hourly forecast entries, each mapped to the
        expected structure with fields like temperature, humidity, wind, and condition.
    """
    first_day = data[0]["hour"]
    second_day = data[1]["hour"]
    third_day = data[2]["hour"]
    combined = first_day + second_day + third_day

    relevant = combined[hour_no : hour_no + 48]
    # relevant = combined

    def map_weather_api_to_owm(weather_api_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts a single hour of WeatherAPI data to an OpenWeatherMap-style dictionary.

        Args:
            weather_api_data (Dict[str, Any]): A dictionary containing an hour's data from WeatherAPI.

        Returns:
            Dict[str, Any]: A dictionary with keys and structure similar to OpenWeatherMap's hourly forecast.
        """
        game = {
            "dt": weather_api_data["time_epoch"],
            "temp": weather_api_data["temp_c"],
            "feels_like": weather_api_data["feelslike_c"],
            "pressure": weather_api_data["pressure_mb"],
            "humidity": weather_api_data["humidity"],
            "dew_point": weather_api_data["dewpoint_c"],
            "uvi": weather_api_data["uv"],
            "clouds": weather_api_data["cloud"],
            "visibility": weather_api_data["vis_km"] * 1000,
            "wind_speed": weather_api_data["wind_kph"] / 3.6,
            "wind_deg": weather_api_data["wind_degree"],
            "wind_gust": weather_api_data["gust_kph"] / 3.6,
            "weather": [
                {
                    "id": weather_api_data["condition"]["code"],
                    "main": weather_api_data["condition"]["text"].split()[0],
                    "description": weather_api_data["condition"]["text"],
                    "icon": weather_api_data["condition"]["icon"],
                }
            ],
            "pop": weather_api_data["chance_of_rain"] / 100,
        }
        return game

    converted = [map_weather_api_to_owm(hour) for hour in relevant]
    return converted


def call_openweatherapi(
    api_key: str, location: Tuple[float, float]
) -> Tuple[datetime, List[Dict]]:
    """
    Make a single "one-call" to the Open Weather API and return the API timestamp as well as the 48 hourly forecasts.
    See https://openweathermap.org/api/one-call-3 for docs.
    Note that the first forecast is about the current hour.
    """
    check_openweathermap_version(API_VERSION)
    query_str = f"lat={location[0]}&lon={location[1]}&units=metric&exclude=minutely,daily,alerts&appid={api_key}"
    res = requests.get(
        f"http://api.openweathermap.org/data/{API_VERSION}/onecall?{query_str}"
    )
    assert (
        res.status_code == 200
    ), f"OpenWeatherMap returned status code {res.status_code}: {res.text}"
    data = res.json()
    time_of_api_call = as_server_time(
        datetime.fromtimestamp(data["current"]["dt"], tz=get_timezone())
    ).replace(second=0, microsecond=0)
    return time_of_api_call, data["hourly"]


def call_weatherapi(
    api_key: str, location: Tuple[float, float], days: int = 3
) -> Tuple[datetime, List[Dict]]:
    """
    Makes a request to the WeatherAPI to retrieve hourly weather forecast data.

    Args:
        api_key (str): API key for authenticating with the Weather API.
        location (Tuple[float, float]): A tuple containing the latitude and longitude.
        days (int, optional): Number of days to request the forecast for (default is 3, including current day).

    Returns:
        Tuple[datetime, List[Dict]]:
            - The timestamp of the API call.
            - A list of hourly forecast data as dictionaries. Note that the first forecast is about the current hour.

    Raises:
        AssertionError: If the response from the Weather API is not successful (HTTP status 200).
    """

    latitude, longitude = location[0], location[1]

    query_str = f"http://api.weatherapi.com/v1/forecast.json?key={api_key}&q={latitude},{longitude}&days={days}&aqi=yes&alerts=yes"
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
    time_of_api_call = time_of_api_call.replace(second=0, microsecond=0)

    print(f"Time of API call in WAPI is {time_of_api_call}")

    relevant = data["forecast"]["forecastday"]
    hour_no = local_time.hour

    hourly = process_weatherapi_data(relevant, hour_no)
    return time_of_api_call, hourly


def call_api(
    api_key: str, location: Tuple[float, float]
) -> Tuple[datetime, List[Dict]]:
    """
    Dispatches the weather API call based on the configured provider.

    Args:
        api_key (str): API key for the selected weather service provider.
        location (Tuple[float, float]): Latitude and longitude tuple.

    Returns:
        Tuple[datetime, List[Dict]]:
            - Timestamp of the API call.
            - List of hourly forecast data.

    Raises:
        Exception: If an invalid weather provider is configured.
    """

    provider = str(current_app.config.get("WEATHER_PROVIDER", ""))
    if provider not in ["OWM", "WAPI"]:
        raise Exception(
            "Invalid provider name. Please set WEATHER_PROVIDER setting in config file to either OWM or WAPI, the two permissible options."
        )

    if provider == "OWM":
        click.secho("Calling Open Weather Map")
        return call_openweatherapi(api_key, location)
    else:
        click.secho("Calling Weather API")
        return call_weatherapi(api_key, location)


def save_forecasts_in_db(
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
    for location in locations:
        click.echo("[FLEXMEASURES] %s, %s" % location)
        weather_sensors: Dict[str, Sensor] = (
            {}
        )  # keep track of the sensors to save lookups
        db_forecasts: Dict[Sensor, List[TimedBelief]] = {}  # collect beliefs per sensor

        now = server_now()
        time_of_api_call, forecasts = call_api(api_key, location)
        diff_fm_owm = now - time_of_api_call
        if abs(diff_fm_owm) > timedelta(minutes=10):
            click.echo(
                f"[FLEXMEASURES-WEATHER] Warning: difference between this server and Weather Provider is {naturaldelta(diff_fm_owm)}"
            )
        click.echo(
            f"[FLEXMEASURES-WEATHER] Called OpenWeatherMap API successfully at {now}."
        )

        # loop through forecasts, including the one of current hour (horizon 0)
        for fc in forecasts:
            fc_datetime = as_server_time(
                datetime.fromtimestamp(fc["dt"], get_timezone())
            )
            click.echo(
                f"[FLEXMEASURES-WEATHER] Processing forecast for {fc_datetime} ..."
            )
            data_source = get_or_create_owm_data_source()
            for sensor_specs in mapping:
                sensor_name = str(sensor_specs["fm_sensor_name"])
                owm_response_label = sensor_specs["weather_sensor_name"]
                if owm_response_label in fc:
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

                        fc_value = fc[owm_response_label]

                        # the irradiance is not available in Provider -> we compute it ourselves
                        if sensor_name == "irradiance":
                            fc_value = compute_irradiance(
                                location[0],
                                location[1],
                                fc_datetime,
                                # Provider sends cloud cover in percent, we need a ratio
                                fc_value / 100.0,
                            )
                            data_source = (
                                get_or_create_owm_data_source_for_derived_data()
                            )

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
                    msg = "No label '%s' in response data for time %s" % (
                        owm_response_label,
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
            json.dump(forecasts, outfile)


def check_openweathermap_version(api_version: str):
    supported_versions = ["2.5", "3.0"]
    if api_version not in supported_versions:
        current_app.logger.warning(
            f"This plugin may not be fully compatible with OpenWeatherMap API version {api_version}. We tested with versions {supported_versions}"
        )
