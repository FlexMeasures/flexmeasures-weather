import pytest
from flexmeasures import Sensor
from flexmeasures.data.models.generic_assets import GenericAssetType

from ..commands import add_weather_sensor
from .utils import cli_params_from_dict


"""
Useful resource: https://flask.palletsprojects.com/en/2.0.x/testing/#testing-cli-commands
"""

sensor_params = {"name": "wind speed", "latitude": 30, "longitude": 40}


@pytest.mark.parametrize(
    "invalid_param, invalid_value, expected_msg",
    [
        ("name", "windd-speed", "not supported by flexmeasures-weather"),
        ("latitude", 93, "less than or equal to 90"),
        ("timezone", "Erope/Amsterdam", "is unknown"),
    ],
)
def test_register_weather_sensor_invalid_data(
    app, db, invalid_param, invalid_value, expected_msg
):
    test_sensor_params = sensor_params.copy()
    test_sensor_params[invalid_param] = invalid_value
    runner = app.test_cli_runner()
    result = runner.invoke(add_weather_sensor, cli_params_from_dict(test_sensor_params))
    assert "Aborted" in result.output
    assert expected_msg in result.output


def test_register_weather_sensor(app, fresh_db):
    runner = app.test_cli_runner()
    result = runner.invoke(add_weather_sensor, cli_params_from_dict(sensor_params))
    assert "Successfully created weather sensor with ID" in result.output
    assert "entity address" not in result.output
    sensor = Sensor.query.filter(Sensor.name == sensor_params["name"]).one_or_none()
    assert sensor is not None


def test_register_weather_sensor_twice(app, fresh_db):
    runner = app.test_cli_runner()
    result = runner.invoke(add_weather_sensor, cli_params_from_dict(sensor_params))
    assert "Successfully created weather sensor with ID" in result.output
    result = runner.invoke(add_weather_sensor, cli_params_from_dict(sensor_params))
    assert "already exists" in result.output


def test_register_weather_sensor_by_asset_id(
    app, fresh_db, add_weather_sensors_fresh_db
):
    asset = add_weather_sensors_fresh_db["wind"].generic_asset
    fresh_db.session.flush()
    asset.generic_asset_type = GenericAssetType(name="site")
    fresh_db.session.flush()
    asset_id = asset.id
    assert asset_id != asset.generic_asset_type_id
    result = app.test_cli_runner().invoke(
        add_weather_sensor, ["--name", "cloud cover", "--asset-id", str(asset_id)]
    )
    assert "Successfully created weather sensor" in result.output
    assert Sensor.query.filter_by(name="cloud cover").one().generic_asset_id == asset_id
