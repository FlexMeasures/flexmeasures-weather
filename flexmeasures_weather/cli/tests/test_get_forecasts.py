import logging
from datetime import timedelta

import pytest
from flexmeasures.data.models.time_series import Sensor, TimedBelief
from flexmeasures.data.models.generic_assets import GenericAsset, GenericAssetType

from ..commands import collect_weather_data
from ...utils import weather
from .utils import mock_api_response

"""
Useful resource: https://flask.palletsprojects.com/en/2.0.x/testing/#testing-cli-commands
"""


def test_get_weather_forecasts_to_db(
    app, fresh_db, monkeypatch, run_as_cli, add_weather_sensors_fresh_db
):
    """
    Test if we can process forecast and save them to the database.
    """
    wind_sensor = add_weather_sensors_fresh_db["wind"]
    fresh_db.session.flush()
    wind_sensor_id = wind_sensor.id
    weather_station = wind_sensor.generic_asset
    messages = [
        f"Using sensor {sensor.name} (ID {sensor.id}) on asset "
        f"{weather_station.name} (ID {weather_station.id}, "
        f"location {weather_station.latitude}, {weather_station.longitude})."
        for sensor in add_weather_sensors_fresh_db.values()
    ]

    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setattr(weather, "call_api", mock_api_response)

    runner = app.test_cli_runner()
    result = runner.invoke(
        collect_weather_data,
        ["--location", f"{weather_station.latitude},{weather_station.longitude}"],
    )
    print(result.output)
    assert "Reported task get-weather-forecasts status as True" in result.output
    for message in messages:
        assert result.output.count(message) == 1
        assert result.output.index(message) < result.output.index("Processing forecast")

    beliefs = (
        fresh_db.session.query(TimedBelief)
        .filter(TimedBelief.sensor_id == wind_sensor_id)
        .all()
    )
    assert len(beliefs) == 2
    for wind_speed in (100, 90):
        assert wind_speed in [belief.event_value for belief in beliefs]


def test_get_weather_forecasts_wapi_mapping(
    app, fresh_db, monkeypatch, run_as_cli, add_weather_sensors_fresh_db
):
    """
    Test that WeatherAPI provider-specific field names are mapped independently.
    """
    wind_sensor = add_weather_sensors_fresh_db["wind"]
    fresh_db.session.flush()
    wind_sensor_id = wind_sensor.id
    weather_station = wind_sensor.generic_asset

    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "WAPI")
    monkeypatch.setattr(weather, "call_api", mock_api_response)

    runner = app.test_cli_runner()
    result = runner.invoke(
        collect_weather_data,
        ["--location", f"{weather_station.latitude},{weather_station.longitude}"],
    )
    assert "Reported task get-weather-forecasts status as True" in result.output

    beliefs = (
        fresh_db.session.query(TimedBelief)
        .filter(TimedBelief.sensor_id == wind_sensor_id)
        .all()
    )
    assert len(beliefs) == 2
    expected_values = [pytest.approx(100 / 3.6), pytest.approx(90 / 3.6)]
    assert [belief.event_value for belief in beliefs] == expected_values


def test_ambiguous_temperature_does_not_block_wind_forecasts(
    app, fresh_db, monkeypatch, run_as_cli, add_weather_sensors_fresh_db, caplog
):
    wind = add_weather_sensors_fresh_db["wind"]
    station = wind.generic_asset
    site = GenericAsset(
        name="Site",
        generic_asset_type=GenericAssetType(name="site"),
        latitude=station.latitude,
        longitude=station.longitude,
    )
    other_temperature = Sensor(
        name="temperature",
        generic_asset=site,
        unit="°C",
        event_resolution=timedelta(hours=1),
    )
    fresh_db.session.add(other_temperature)
    fresh_db.session.flush()
    wind_id = wind.id
    temperature_id = add_weather_sensors_fresh_db["temperature"].id
    other_temperature_id = other_temperature.id
    location = f"{station.latitude},{station.longitude}"
    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setattr(weather, "call_api", mock_api_response)
    with caplog.at_level(logging.WARNING):
        result = app.test_cli_runner().invoke(
            collect_weather_data, ["--location", location]
        )
    assert "Reported task get-weather-forecasts status as True" in result.output
    assert "Multiple 'temperature' sensors" in caplog.text
    assert f"using sensor ID {temperature_id}" in caplog.text
    assert fresh_db.session.query(TimedBelief).filter_by(sensor_id=wind_id).count() == 2
    assert (
        fresh_db.session.query(TimedBelief).filter_by(sensor_id=temperature_id).count()
        == 2
    )
    assert (
        fresh_db.session.query(TimedBelief)
        .filter_by(sensor_id=other_temperature_id)
        .count()
        == 0
    )


def test_get_weather_forecasts_on_non_station_asset(
    app, fresh_db, monkeypatch, run_as_cli, add_weather_sensors_fresh_db
):
    sensor = add_weather_sensors_fresh_db["temperature"]
    asset = sensor.generic_asset
    fresh_db.session.flush()
    asset.generic_asset_type = GenericAssetType(name="site")
    fresh_db.session.flush()
    sensor_id = sensor.id
    asset_id = asset.id
    assert asset_id != asset.generic_asset_type_id
    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setattr(weather, "call_api", mock_api_response)

    result = app.test_cli_runner().invoke(
        collect_weather_data, ["--asset-id", str(asset_id)]
    )

    assert "Reported task get-weather-forecasts status as True" in result.output
    assert "on asset Test weather station" in result.output
    assert (
        fresh_db.session.query(TimedBelief).filter_by(sensor_id=sensor_id).count() == 2
    )


def test_get_weather_forecasts_no_close_sensors(
    app, db, monkeypatch, run_as_cli, add_weather_sensors_fresh_db, caplog
):
    """
    Looking for a location too far away from existing weather station.
    Check we get a warning.
    """
    weather_station = add_weather_sensors_fresh_db["wind"].generic_asset

    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setattr(weather, "call_api", mock_api_response)

    runner = app.test_cli_runner()
    with caplog.at_level(logging.WARNING):
        result = runner.invoke(
            collect_weather_data,
            [
                "--location",
                f"{weather_station.latitude - 5},{weather_station.longitude}",
            ],
        )
        print(result.output)
        assert "Reported task get-weather-forecasts status as True" in result.output
        assert "no sufficiently close weather sensor found" in caplog.text
