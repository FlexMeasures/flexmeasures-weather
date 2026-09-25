"""Automation support for recurring weather forecast imports."""

from flask import current_app
from marshmallow import Schema

from flexmeasures.data.models.data_sources import DataGenerator
from flexmeasures.data.schemas.generic_assets import GenericAssetIdField

from .sensor_specs import mapping
from .utils.weather import collect_weather_forecasts


class WeatherForecastConfigSchema(Schema):
    weather_station = GenericAssetIdField(data_key="weather-station", required=True)


class WeatherForecastParametersSchema(Schema):
    pass


class WeatherForecastImporter(DataGenerator):
    """Fetch weather forecasts for the sensors of one weather station."""

    __data_generator_base__ = "forecaster"
    __version__ = "0.1"
    _config_schema = WeatherForecastConfigSchema()
    _parameters_schema = WeatherForecastParametersSchema()

    @property
    def output_sensors(self) -> list:
        station = self._config["weather_station"]
        supported_names = {spec["fm_sensor_name"] for spec in mapping}
        sensors = [
            sensor for sensor in station.sensors if sensor.name in supported_names
        ]
        if not sensors:
            raise ValueError(
                f"Weather station {station.id} has no supported weather sensors."
            )
        return sensors

    def _compute(self) -> list[dict]:
        station = self._config["weather_station"]
        if station.latitude is None or station.longitude is None:
            raise ValueError(
                f"Weather station {station.id} needs latitude and longitude."
            )
        api_key = str(current_app.config.get("WEATHERAPI_KEY", ""))
        if not api_key:
            raise ValueError("Setting WEATHERAPI_KEY is not available.")
        sensors = {sensor.name: sensor for sensor in self.output_sensors}
        forecasts = collect_weather_forecasts(
            api_key,
            (station.latitude, station.longitude),
            weather_sensors=sensors,
            data_source=self.data_source,
            derived_data_source=self.data_source,
        )
        return [
            {"sensor": sensor, "data": beliefs} for sensor, beliefs in forecasts.items()
        ]
