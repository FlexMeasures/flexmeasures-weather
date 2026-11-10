from types import SimpleNamespace
from datetime import timedelta

import pytest
from werkzeug.exceptions import Forbidden
from flexmeasures.data.models.generic_assets import GenericAsset, GenericAssetType
from flexmeasures.data.models.time_series import Sensor

from flexmeasures_weather import DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE
from flexmeasures_weather.utils.locating import find_weather_sensor_by_location


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ((0.3, 0), True),
        ((-0.3, 0), True),
        ((0, 0.3), True),
        ((0, -0.3), True),
        ((0.31, 0), False),
        ((-0.31, 0), False),
        ((0, 0.31), False),
        ((0, -0.31), False),
    ],
)
def test_distance_limit_in_both_directions(
    fresh_db, monkeypatch, run_as_cli, location, expected
):
    sensor = SimpleNamespace(generic_asset=SimpleNamespace(location=(0, 0)))
    monkeypatch.setattr(
        "flexmeasures_weather.utils.locating.Sensor.find_closest",
        lambda **kwargs: sensor,
    )
    assert DEFAULT_MAXIMAL_DEGREE_LOCATION_DISTANCE == 0.3
    result = find_weather_sensor_by_location(location, 0.3, "temperature")
    assert (result is sensor) is expected


def test_exact_sensor_on_other_asset_takes_precedence(
    fresh_db, add_weather_sensors_fresh_db, run_as_cli
):
    nearby_sensor = add_weather_sensors_fresh_db["temperature"]
    station = nearby_sensor.generic_asset
    asset = GenericAsset(
        name="Site",
        generic_asset_type=GenericAssetType(name="site"),
        latitude=station.latitude + 0.1,
        longitude=station.longitude,
    )
    sensor = Sensor(
        name="temperature",
        generic_asset=asset,
        unit="°C",
        event_resolution=timedelta(hours=1),
    )
    fresh_db.session.add(sensor)
    fresh_db.session.flush()
    assert find_weather_sensor_by_location(asset.location, 0.3, "temperature") == sensor
    assert (
        find_weather_sensor_by_location(
            (station.latitude + 0.05, station.longitude), 0.3, "temperature"
        )
        == nearby_sensor
    )


def test_multiple_exact_sensors_are_ambiguous(
    fresh_db, add_weather_sensors_fresh_db, run_as_cli, caplog
):
    station = add_weather_sensors_fresh_db["temperature"].generic_asset
    asset = GenericAsset(
        name="Site",
        generic_asset_type=GenericAssetType(name="site"),
        latitude=station.latitude,
        longitude=station.longitude,
    )
    fresh_db.session.add(
        Sensor(
            name="temperature",
            generic_asset=asset,
            unit="°C",
            event_resolution=timedelta(hours=1),
        )
    )
    fresh_db.session.flush()
    expected = add_weather_sensors_fresh_db["temperature"]
    assert (
        find_weather_sensor_by_location(station.location, 0.3, "temperature")
        == expected
    )
    assert "Multiple 'temperature' sensors" in caplog.text
    assert f"using sensor ID {expected.id}" in caplog.text


def test_exact_sensor_lookup_requires_asset_access(
    fresh_db, add_weather_sensors_fresh_db
):
    station = add_weather_sensors_fresh_db["temperature"].generic_asset
    with pytest.raises(Forbidden):
        find_weather_sensor_by_location(station.location, 0.3, "temperature")
