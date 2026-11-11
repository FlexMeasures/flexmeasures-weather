from datetime import timedelta

from flexmeasures.data.models.time_series import Sensor

from flexmeasures_weather import __automation_types__
from flexmeasures_weather.automation import WeatherForecastImporter
from flexmeasures_weather.utils import weather

from .utils import mock_api_response


def test_weather_automation_registration():
    handler = __automation_types__[0]

    assert handler.type_id == "weather-forecasts"
    assert handler.queue == "ingestion"
    assert handler.generator_class is WeatherForecastImporter


def test_weather_automation_collects_declared_station_sensors(
    app, fresh_db, monkeypatch, add_weather_sensors_fresh_db
):
    station = add_weather_sensors_fresh_db["wind"].generic_asset
    fresh_db.session.flush()
    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setattr(weather, "call_api", mock_api_response)

    importer = WeatherForecastImporter(config={"weather-station": station.id})
    results = importer.compute()

    assert {result["sensor"].name for result in results} == {
        "temperature",
        "wind speed",
    }
    assert all(len(result["data"]) == 2 for result in results)
    assert set(importer.output_sensors) == set(add_weather_sensors_fresh_db.values())


def test_weather_automation_falls_back_to_the_openweathermap_key(
    app, fresh_db, monkeypatch, add_weather_sensors_fresh_db
):
    """A config with only OPENWEATHERMAP_API_KEY, which the CLI accepts, also works for the automation."""
    station = add_weather_sensors_fresh_db["wind"].generic_asset
    fresh_db.session.flush()
    monkeypatch.delitem(app.config, "WEATHERAPI_KEY", raising=False)
    monkeypatch.setitem(app.config, "OPENWEATHERMAP_API_KEY", "owm-key")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    used_keys = []

    def recording_call_api(api_key, location):
        used_keys.append(api_key)
        return mock_api_response(api_key, location)

    monkeypatch.setattr(weather, "call_api", recording_call_api)

    results = WeatherForecastImporter(config={"weather-station": station.id}).compute()

    assert used_keys == ["owm-key"]
    assert all(len(result["data"]) == 2 for result in results)


def test_weather_automation_names_its_sources_like_the_cli(
    app, fresh_db, monkeypatch, add_weather_sensors_fresh_db
):
    """Moving a station from the CLI to the automation keeps the names of the sources its forecasts are recorded under."""
    station = add_weather_sensors_fresh_db["wind"].generic_asset
    irradiance = Sensor(
        name="irradiance",
        generic_asset=station,
        event_resolution=timedelta(minutes=60),
        unit="W/m²",
    )
    fresh_db.session.add(irradiance)
    fresh_db.session.flush()
    monkeypatch.setitem(app.config, "WEATHERAPI_KEY", "dummy")
    monkeypatch.setitem(app.config, "WEATHER_PROVIDER", "OWM")
    monkeypatch.setitem(app.config, "WEATHER_DATA_SOURCE_NAME", "OpenWeatherMap")

    def response_with_clouds(api_key, location):
        time_of_call, forecasts = mock_api_response(api_key, location)
        return time_of_call, [dict(forecast, clouds=50) for forecast in forecasts]

    monkeypatch.setattr(weather, "call_api", response_with_clouds)

    importer = WeatherForecastImporter(config={"weather-station": station.id})
    results = {result["sensor"].name: result["data"] for result in importer.compute()}

    source = importer.data_source
    assert (source.name, source.model) == (
        "OpenWeatherMap",
        "WeatherForecastImporter",
    )
    assert set(results["temperature"].sources) == {source}
    # FlexMeasures records all of a data generator's results under its source,
    # so irradiance, which the CLI records under "FlexMeasures OpenWeatherMap", is recorded under it too.
    assert set(results["irradiance"].sources) == {source}
