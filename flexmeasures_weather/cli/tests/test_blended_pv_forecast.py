"""End-to-end test of BlendedPVForecaster, through the CLI.

Same rationale as test_pv_forecast.py: the framework does not save beliefs for a
forecaster, so the assertions are about rows in the database, not the CLI's own report.

Unlike PVWattsForecaster, this forecaster fetches both providers live over HTTP rather
than reading a pre-collected irradiance sensor, so the two provider fetch functions are
monkeypatched instead of seeding a sensor.
"""

from datetime import datetime, timedelta

import pandas as pd
from pvlib.irradiance import complete_irradiance
from pvlib.location import Location
from pytz import timezone

from .test_pv_forecast import beliefs_on, register_specs
from ...weather_providers import BrightSkyProvider, HourlyForecast, OpenMeteoProvider

TIMEZONE = timezone("Asia/Seoul")
FORECAST_START = TIMEZONE.localize(datetime(2024, 6, 21, 0))
FORECAST_END = FORECAST_START + timedelta(hours=24)
CAPACITY_IN_KW = 5.4


def _clear_sky_index():
    return pd.date_range(
        FORECAST_START, FORECAST_END, freq="h", tz=TIMEZONE, inclusive="left"
    )


def fake_brightsky_weather(
    self, latitude, longitude, timezone, *args, **kwargs
) -> list[HourlyForecast]:
    index = _clear_sky_index()
    ghi = Location(latitude, longitude, tz=TIMEZONE).get_clearsky(index)["ghi"]
    return [
        HourlyForecast(
            time=time, temperature_c=20.0, wind_speed_mps=3.0, ghi_wm2=ghi.loc[time]
        )
        for time in index
    ]


def fake_open_meteo_weather(
    self, latitude, longitude, *args, **kwargs
) -> list[HourlyForecast]:
    index = _clear_sky_index()
    site = Location(latitude, longitude, tz=TIMEZONE)
    clearsky = site.get_clearsky(index)
    zenith = site.get_solarposition(index)["zenith"]
    dni = complete_irradiance(zenith, ghi=clearsky["ghi"], dhi=clearsky["dhi"])["dni"]
    return [
        HourlyForecast(
            time=time,
            temperature_c=20.0,
            wind_speed_mps=3.0,
            ghi_wm2=clearsky["ghi"].loc[time],
            dhi_wm2=clearsky["dhi"].loc[time],
            dni_wm2=dni.loc[time],
        )
        for time in index
    ]


def run_blended_forecast(app, sensor_id: int, *extra_args):
    from flexmeasures.cli.data_add import add_forecast

    return app.test_cli_runner().invoke(
        add_forecast,
        [
            "--sensor",
            str(sensor_id),
            "--forecaster",
            "BlendedPVForecaster",
            "--start",
            FORECAST_START.isoformat(),
            "--end",
            FORECAST_END.isoformat(),
            *extra_args,
        ],
    )


def test_blended_pv_forecast_lands_in_the_database(
    app, fresh_db, run_as_cli, monkeypatch, add_pv_asset_fresh_db
):
    power_sensor = add_pv_asset_fresh_db
    monkeypatch.setattr(
        BrightSkyProvider, "fetch_hourly_forecast", fake_brightsky_weather
    )
    monkeypatch.setattr(
        OpenMeteoProvider, "fetch_hourly_forecast", fake_open_meteo_weather
    )
    register_specs(app, power_sensor.generic_asset.id)

    result = run_blended_forecast(app, power_sensor.id)

    assert result.exit_code == 0, result.output
    assert "Successfully created 24 forecast beliefs" in result.output

    beliefs = beliefs_on(fresh_db, power_sensor.id)
    assert len(beliefs) == 24
    values = pd.Series(
        [belief.event_value for belief in beliefs],
        index=[belief.event_start.astimezone(TIMEZONE) for belief in beliefs],
    )
    assert (values >= 0).all()
    assert values.max() > 0
    assert 0 < values.max() < CAPACITY_IN_KW * 1000
    assert beliefs[0].source.model == "BlendedPVForecaster"


def test_blended_pv_forecast_refuses_to_run_as_a_job(
    app, fresh_db, run_as_cli, monkeypatch, add_pv_asset_fresh_db
):
    power_sensor = add_pv_asset_fresh_db
    monkeypatch.setattr(
        BrightSkyProvider, "fetch_hourly_forecast", fake_brightsky_weather
    )
    monkeypatch.setattr(
        OpenMeteoProvider, "fetch_hourly_forecast", fake_open_meteo_weather
    )
    register_specs(app, power_sensor.generic_asset.id)

    result = run_blended_forecast(app, power_sensor.id, "--as-job")

    assert result.exit_code != 0
    assert "does not support running as a job" in result.output
    assert beliefs_on(fresh_db, power_sensor.id) == []


def test_blended_pv_forecast_without_registered_specs_says_what_to_do(
    app, fresh_db, run_as_cli, monkeypatch, add_pv_asset_fresh_db
):
    power_sensor = add_pv_asset_fresh_db
    monkeypatch.setattr(
        BrightSkyProvider, "fetch_hourly_forecast", fake_brightsky_weather
    )
    monkeypatch.setattr(
        OpenMeteoProvider, "fetch_hourly_forecast", fake_open_meteo_weather
    )

    result = run_blended_forecast(app, power_sensor.id)

    assert result.exit_code != 0
    assert "pv_tilt" in result.output
    assert "register-pv-array" in result.output
