from datetime import timedelta

"""
This maps sensor specs which we can use in FlexMeasures to Weather labels.
Note: Sensor names we use in FM need to be unique per weather station.
At the moment, we only extract from Weather hourly data.
"""


weather_attributes = {
    "daily_seasonality": True,
    "weekly_seasonality": False,
    "yearly_seasonality": True,
}


mapping = [
    dict(
        fm_sensor_name="temperature",
        source_field="temperature_c",
        unit="°C",
        event_resolution=timedelta(minutes=60),
        attributes=weather_attributes,
    ),
    dict(
        fm_sensor_name="wind speed",
        source_field="wind_speed_mps",
        unit="m/s",
        event_resolution=timedelta(minutes=60),
        attributes=weather_attributes,
    ),
    dict(
        fm_sensor_name="cloud cover",
        source_field="cloud_cover_fraction",
        unit="%",
        event_resolution=timedelta(minutes=60),
        attributes=weather_attributes,
    ),
    dict(
        fm_sensor_name="irradiance",  # in save_forecasts_to_db, we catch this name and do the actual computation to get to the irradiance
        source_field="cloud_cover_fraction",
        unit="W/m²",
        event_resolution=timedelta(minutes=60),
        attributes=weather_attributes,
    ),
]
