"""End-to-end test of the PV power forecaster, through the CLI.

This needs the test database, like the other CLI tests. The point of going through
`flexmeasures add forecasts` rather than calling the forecaster directly is that the
framework does *not* save beliefs for us: a forecaster that forgets to save its own data
still makes the CLI report "Successfully created N forecast beliefs". So the assertions
here are about rows in the database.
"""

from datetime import datetime, timedelta

import pandas as pd
import pytest
from flexmeasures.data.models.time_series import TimedBelief
from flexmeasures.data.services.data_sources import get_or_create_source
from pvlib.location import Location
from pytz import timezone

from ..commands import register_pv_array

TIMEZONE = timezone("Asia/Seoul")
# A clear midsummer day, so that we know what the output should roughly look like
FORECAST_START = TIMEZONE.localize(datetime(2024, 6, 21, 0))
FORECAST_END = FORECAST_START + timedelta(hours=24)
CAPACITY_IN_KW = 5.4
TILT = 30
AZIMUTH = 180


def seed_clear_sky_irradiance(db, irradiance_sensor) -> pd.Series:
    """Give the irradiance sensor a day of clear-sky GHI forecasts."""
    asset = irradiance_sensor.generic_asset
    index = pd.date_range(
        FORECAST_START, FORECAST_END, freq="h", tz=TIMEZONE, inclusive="left"
    )
    ghi = Location(asset.latitude, asset.longitude, tz=TIMEZONE).get_clearsky(index)[
        "ghi"
    ]

    source = get_or_create_source("Test weather", source_type="forecaster")
    db.session.add_all(
        [
            TimedBelief(
                event_start=event_start,
                belief_time=FORECAST_START - timedelta(hours=1),
                event_value=value,
                sensor=irradiance_sensor,
                source=source,
            )
            for event_start, value in ghi.items()
        ]
    )
    db.session.commit()
    return ghi


def register_specs(app, asset_id: int, **overrides):
    params = {
        "--asset-id": str(asset_id),
        "--tilt": str(TILT),
        "--azimuth": str(AZIMUTH),
        "--capacity-kw": str(CAPACITY_IN_KW),
    }
    params.update(overrides)
    return app.test_cli_runner().invoke(
        register_pv_array, [item for pair in params.items() for item in pair]
    )


def run_forecast(app, sensor_id: int, *extra_args):
    # Imported here, not at module level: flexmeasures.cli.data_add registers its commands
    # on `current_app` at import time, so it needs an application context.
    from flexmeasures.cli.data_add import add_forecast

    return app.test_cli_runner().invoke(
        add_forecast,
        [
            "--sensor",
            str(sensor_id),
            "--forecaster",
            "PVWattsForecaster",
            "--start",
            FORECAST_START.isoformat(),
            "--end",
            FORECAST_END.isoformat(),
            *extra_args,
        ],
    )


def beliefs_on(db, sensor_id: int) -> list:
    return (
        db.session.query(TimedBelief)
        .filter(TimedBelief.sensor_id == sensor_id)
        .order_by(TimedBelief.event_start)
        .all()
    )


def test_register_pv_array_writes_the_attributes(
    app, fresh_db, run_as_cli, add_pv_asset_fresh_db
):
    asset = add_pv_asset_fresh_db.generic_asset

    result = register_specs(app, asset.id)

    assert "Registered PV array specs" in result.output
    fresh_db.session.refresh(asset)
    assert asset.get_attribute("pv_tilt") == TILT
    assert asset.get_attribute("pv_azimuth") == AZIMUTH
    assert asset.get_attribute("pv_capacity_in_kw") == CAPACITY_IN_KW
    # not given, so the PVWatts default is written explicitly
    assert asset.get_attribute("pv_losses") == 0.14


def test_register_pv_array_rejects_an_impossible_tilt(
    app, fresh_db, run_as_cli, add_pv_asset_fresh_db
):
    result = register_specs(
        app, add_pv_asset_fresh_db.generic_asset.id, **{"--tilt": "120"}
    )

    assert "Aborted" in result.output
    assert "less than or equal to 90" in result.output


def test_pv_forecast_lands_in_the_database(
    app, fresh_db, run_as_cli, add_weather_sensors_fresh_db, add_pv_asset_fresh_db
):
    """The whole path: specs on the asset, GHI on a weather sensor, beliefs in the DB."""
    power_sensor = add_pv_asset_fresh_db
    ghi = seed_clear_sky_irradiance(
        fresh_db, add_weather_sensors_fresh_db["irradiance"]
    )
    register_specs(app, power_sensor.generic_asset.id)

    result = run_forecast(app, power_sensor.id)

    assert result.exit_code == 0, result.output
    assert "Successfully created 24 forecast beliefs" in result.output

    beliefs = beliefs_on(fresh_db, power_sensor.id)
    assert len(beliefs) == 24

    values = pd.Series(
        [belief.event_value for belief in beliefs],
        index=[belief.event_start.astimezone(TIMEZONE) for belief in beliefs],
    )
    # zero whenever the sun is down, positive whenever it is up
    assert (values[ghi.to_numpy() == 0] == 0).all()
    assert (values[ghi.to_numpy() > 0] > 0).all()
    # a plausible peak: below nameplate, and around the middle of the day
    assert 0 < values.max() < CAPACITY_IN_KW * 1000
    assert 9 <= values.idxmax().hour <= 15
    # the source records which forecaster produced this
    assert beliefs[0].source.model == "PVWattsForecaster"


def test_pv_forecast_without_registered_specs_says_what_to_do(
    app, fresh_db, run_as_cli, add_weather_sensors_fresh_db, add_pv_asset_fresh_db
):
    power_sensor = add_pv_asset_fresh_db
    seed_clear_sky_irradiance(fresh_db, add_weather_sensors_fresh_db["irradiance"])

    result = run_forecast(app, power_sensor.id)

    assert result.exit_code != 0
    assert "pv_tilt" in result.output
    assert "register-pv-array" in result.output


def test_pv_forecast_without_irradiance_data_says_what_to_do(
    app, fresh_db, run_as_cli, add_weather_sensors_fresh_db, add_pv_asset_fresh_db
):
    """The irradiance sensor exists, but nobody collected forecasts for it."""
    power_sensor = add_pv_asset_fresh_db
    register_specs(app, power_sensor.generic_asset.id)

    result = run_forecast(app, power_sensor.id)

    assert result.exit_code != 0
    assert "No irradiance data" in result.output
    assert "get-weather-forecasts" in result.output
    assert beliefs_on(fresh_db, power_sensor.id) == []


def test_pv_forecast_refuses_to_run_as_a_job(
    app, fresh_db, run_as_cli, add_weather_sensors_fresh_db, add_pv_asset_fresh_db
):
    """Queueing a millisecond computation buys nothing, so we say so rather than ignore it."""
    power_sensor = add_pv_asset_fresh_db
    seed_clear_sky_irradiance(fresh_db, add_weather_sensors_fresh_db["irradiance"])
    register_specs(app, power_sensor.generic_asset.id)

    result = run_forecast(app, power_sensor.id, "--as-job")

    assert result.exit_code != 0
    assert "does not support running as a job" in result.output
    assert beliefs_on(fresh_db, power_sensor.id) == []


@pytest.mark.parametrize("capacity_in_kw", [2.7, 10.8])
def test_pv_forecast_scales_with_the_registered_capacity(
    app,
    fresh_db,
    run_as_cli,
    add_weather_sensors_fresh_db,
    add_pv_asset_fresh_db,
    capacity_in_kw,
):
    power_sensor = add_pv_asset_fresh_db
    seed_clear_sky_irradiance(fresh_db, add_weather_sensors_fresh_db["irradiance"])
    register_specs(
        app,
        power_sensor.generic_asset.id,
        **{"--capacity-kw": str(capacity_in_kw)},
    )

    run_forecast(app, power_sensor.id)

    peak = max(belief.event_value for belief in beliefs_on(fresh_db, power_sensor.id))
    assert 0.6 * capacity_in_kw * 1000 < peak < 0.8 * capacity_in_kw * 1000
