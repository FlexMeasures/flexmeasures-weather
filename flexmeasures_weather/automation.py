"""Automation support for recurring weather forecast imports."""

from flask import current_app
from marshmallow import Schema

from flexmeasures.data.models.data_sources import DataGenerator
from flexmeasures.data.schemas.generic_assets import GenericAssetIdField

from . import DEFAULT_DATA_SOURCE_NAME
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

    @classmethod
    def get_data_source_info(cls) -> dict:
        """Name the automation's source after the weather provider, as the CLI names its source.

        Without this, the source is named after FLEXMEASURES_DEFAULT_DATASOURCE ("FlexMeasures" by default),
        so moving a weather station from the CLI to the automation would change the name of the source its forecasts are recorded under.
        """
        source_info = super().get_data_source_info()
        source_info["source"] = current_app.config.get(
            "WEATHER_DATA_SOURCE_NAME", DEFAULT_DATA_SOURCE_NAME
        )
        return source_info

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
        # Like the CLI, fall back to the setting's older name, which existing OpenWeatherMap setups still use.
        api_key = str(
            current_app.config.get(
                "WEATHERAPI_KEY", current_app.config.get("OPENWEATHERMAP_API_KEY", "")
            )
        )
        if not api_key:
            raise ValueError(
                "Setting WEATHERAPI_KEY (or OPENWEATHERMAP_API_KEY) is not available."
            )
        sensors = {sensor.name: sensor for sensor in self.output_sensors}
        forecasts = collect_weather_forecasts(
            api_key,
            (station.latitude, station.longitude),
            weather_sensors=sensors,
            data_source=self.data_source,
            derived_data_source=self.data_source,
        )
        # FlexMeasures counts a run that returns nothing as succeeded, so fail it here rather than let the station's forecasts go stale unnoticed.
        missing = [
            sensor
            for sensor in sensors.values()
            if sensor not in forecasts or forecasts[sensor].empty
        ]
        if missing:
            raise ValueError(
                f"The weather provider returned no forecasts for {', '.join(f'{sensor.name} (sensor {sensor.id})' for sensor in missing)} on weather station {station.id}."
            )
        return [
            {"sensor": sensor, "data": beliefs} for sensor, beliefs in forecasts.items()
        ]
