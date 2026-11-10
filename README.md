# FLEXMEASURES-WEATHER - a plugin for FlexMeasures to integrate weather forecasts


This plugin currently supports two Weather API services: [OpenWeatherMap One Call API](https://openweathermap.org/api/one-call-3) and [Weather API](https://www.weatherapi.com/). The configuration is controlled via your FlexMeasures config file.


## Usage

To collect forecasts in the database, first register the weather sensors you need:

```sh
flexmeasures weather register-weather-sensor --name "wind speed" --latitude 30 --longitude 40
flexmeasures weather register-weather-sensor --name "cloud cover" --latitude 30 --longitude 40
```

Supported sensor names are `temperature` (°C), `wind speed` (m/s), `cloud cover` (%) and `irradiance` (W/m²). Irradiance is calculated from the location, time and forecast cloud cover. All four sensors have an hourly resolution. The optional `--timezone` for registration defaults to UTC.

**Sensor registration** by coordinates looks for an asset at exactly those coordinates. It reuses a single matching asset or, when several match, the weather station if there is exactly one. Otherwise it lists the matches and asks you to select one with `--asset-id`. If no asset is found, it creates a public weather station. It does not search nearby locations. `WEATHER_STATION_NAME` controls the name used for new stations.

There is no separate weather-plugin command to create a station, but FlexMeasures provides general asset commands. If the `weather station` asset type does not exist yet, create it with `flexmeasures add asset-type --name "weather station"`. Then use its ID to create a public asset with a unique name:

```sh
flexmeasures add asset --name "Antwerp weather station" --asset-type-id <type-id> --latitude 51.260197 --longitude 4.402771
```

Replace `<type-id>` with the ID of the `weather station` asset type, and register sensors using its coordinates or `--asset-id`. You can also register a sensor on another existing asset with coordinates by passing its ID.

To collect forecasts for one location:

```sh
flexmeasures weather get-weather-forecasts --location 30,40
```

**Forecast collection** fetches a forecast once; schedule it to run repeatedly if you need updates. For each weather quantity present in the provider response, it first looks for a matching sensor on any asset at the exact coordinates. If multiple sensors match, it warns and uses the one with the lowest sensor ID. If none matches exactly, it uses the nearest sensor on an asset of type `weather station`, within `WEATHER_MAXIMAL_DEGREE_LOCATION_DISTANCE` degrees in both latitude and longitude (default: 0.3). Different quantities may use different assets. If no suitable sensor exists, that quantity is skipped with a warning. The command does not create sensors or stations.

You can also pass `--asset-id` to use an asset's coordinates. The command still selects sensors by location, so the ID does not restrict the selected sensors to that asset.

The `--location` option can also describe a bounding box and generate a grid; see `flexmeasures weather get-weather-forecasts --help`. The current database-saving implementation saves only the final grid location, so use separate single-location calls when saving multiple locations to the database.

Alternatively, save the provider's hourly forecast entries as JSON files:

```sh
flexmeasures weather get-weather-forecasts --location 30,40 --store-as-json-files --region somewhere
```

This does not require registered sensors. The entries are taken from the provider's response; the file is not the complete API response. The optional region becomes a subfolder. Check your provider's current subscription and request limits when scheduling calls.


## Setup

### Installation

To add as plugin to an existing FlexMeasures system, add "/path/to/flexmeasures-weather-repo/flexmeasures_weather" to your FlexMeasures config file,
using the FLEXMEASURES_PLUGINS setting (a list).

Alternatively, if you installed this plugin as a package (e.g. via `pip install -e .` from the repository), then "flexmeasures_weather" suffices.

To enable weather forecast functionality, two PostgreSQL extensions must be installed. Run the following SQL commands in your database:

```
CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;
```

These extensions provide support for geographical calculations such as `ll_to_earth` and `earth_distance`, which we use to find the nearest weather station asset.


### Configuration

Add the following entries to your config:

```ini
# Select the weather provider to use: "OWM" (OpenWeatherMap) or "WAPI" (Weather API)
WEATHER_PROVIDER = "OWM"

# API key for the selected weather provider
WEATHERAPI_KEY = "your-api-key-here"

# Name to register the weather data source in FlexMeasures. The default is 'Weather'.
# Examples: "OpenWeatherMap" (for backwards compatibility with the OWM plugin).
WEATHER_DATA_SOURCE_NAME = "OpenWeatherMap"

# Directory in which to store weather forecasts as JSON files
WEATHER_FILE_PATH_LOCATION = "/path/to/weather_output"

# Maximum latitude and longitude difference from a sensor, in degrees (default: 0.3)
WEATHER_MAXIMAL_DEGREE_LOCATION_DISTANCE = 0.3
```

### Extending to Other Weather API Services

To expand the plugin's coverage to additional weather API services:

1. **Update the configuration**  
   Change the `WEATHER_PROVIDER` setting in your config to the identifier for the new API service (e.g., `NEWAPI`), and provide the necessary credentials in `WEATHERAPI_KEY`.

2. **Implement a new API function**  
   Create a function named in the format:

   ```python
   def call_NEWAPI_api(...):
       # Your logic to call the API and return data in the expected format
   ```

   This function should return the API call time and a list of hourly forecast entries. Its field names must match the provider-specific mapping, and the forecast processing code must recognize the provider’s timestamp and any units requiring conversion.

   You also need a provider-specific mapping entry in `flexmeasures_weather/sensor_specs.py`. Each supported sensor should include the new provider's response field name, for example:

   ```python
   dict(
       fm_sensor_name="temperature",
       OWM_sensor_name="temp",
       WAPI_sensor_name="temp_c",
       NEWAPI_sensor_name="temperatureC",
       unit="°C",
       event_resolution=timedelta(minutes=60),
       attributes=weather_attributes,
   )
   ```

3. **Integrate into the plugin**
   Update both `call_api` and `save_forecasts_in_db` in `weather.py` to accept the provider. Add a dispatch branch and handle its timestamp field and any unit conversions. Also update sensor registration to remove the new provider mapping field before constructing a sensor.

4. **Finalize and contribute**  
   Once you've implemented and tested the plugin with your chosen API service:
   - Update this README to reflect the new configuration and usage details.
   - Submit a pull request with your changes for review.

> This modular structure allows for seamless integration of additional services while maintaining consistency and clarity in data handling.


## Development

We use pre-commit to keep code quality up.

Install necessary tools with:

    pip install pre-commit
    pre-commit install

or:

    make install-for-dev

Try it:

    pre-commit run --all-files --show-diff-on-failure
