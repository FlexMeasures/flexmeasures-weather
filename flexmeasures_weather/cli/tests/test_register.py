import pytest
from flexmeasures import Sensor
from flexmeasures.data.models.generic_assets import GenericAsset, GenericAssetType

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
    asset_name = asset.name
    assert asset_id != asset.generic_asset_type_id
    result = app.test_cli_runner().invoke(
        add_weather_sensor, ["--name", "cloud cover", "--asset-id", str(asset_id)]
    )
    assert "Successfully created weather sensor" in result.output
    assert f"on asset {asset_name} (ID {asset_id})" in result.output
    assert Sensor.query.filter_by(name="cloud cover").one().generic_asset_id == asset_id


@pytest.mark.parametrize("station_count", [0, 1, 2])
def test_register_colocated_assets(app, fresh_db, station_count):
    """Prefer one weather station among colocated assets; otherwise require an asset ID."""
    site_type = GenericAssetType(name="site")
    station_type = GenericAssetType(name="weather station")
    assets = [
        GenericAsset(
            name="Site", generic_asset_type=site_type, latitude=30, longitude=40
        ),
        GenericAsset(
            name="Battery", generic_asset_type=site_type, latitude=30, longitude=40
        ),
    ]
    for index in range(station_count):
        assets.append(
            GenericAsset(
                name=f"Station {index}",
                generic_asset_type=station_type,
                latitude=30,
                longitude=40,
            )
        )
    fresh_db.session.add_all(assets)
    fresh_db.session.flush()
    expected_ids = [(asset.name, asset.id) for asset in assets]
    result = app.test_cli_runner().invoke(
        add_weather_sensor,
        ["--name", "wind speed", "--latitude", "30", "--longitude", "40"],
    )
    if station_count == 1:
        assert result.exit_code == 0
        assert (
            Sensor.query.filter_by(name="wind speed").one().generic_asset_id
            == expected_ids[-1][1]
        )
    else:
        assert result.exit_code != 0
        assert "--asset-id" in result.output
        for name, asset_id in expected_ids:
            assert f"{name} (ID {asset_id})" in result.output
        result = app.test_cli_runner().invoke(
            add_weather_sensor,
            ["--name", "wind speed", "--asset-id", str(expected_ids[0][1])],
        )
        assert result.exit_code == 0
        assert (
            Sensor.query.filter_by(name="wind speed").one().generic_asset_id
            == expected_ids[0][1]
        )
