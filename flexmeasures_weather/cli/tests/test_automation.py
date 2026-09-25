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
