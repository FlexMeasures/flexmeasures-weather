# FLEXMEASURES-WEATHER - a plugin for FlexMeasures to integrate weather forecasts


This plugin currently supports two Weather API services: [OpenWeatherMap One Call API](https://openweathermap.org/api/one-call-3) and [Weather API](https://www.weatherapi.com/). The configuration is controlled via your FlexMeasures config file.


## Usage

To register a new weather sensor:

`flexmeasures weather register-weather-sensor --name "wind speed" --latitude 30 --longitude 40`

Currently supported: wind speed, temperature & irradiance.

To collect weather forecasts:

`flexmeasures weather get-weather-forecasts --location 30,40`

This saves forecasts for your registered sensors in the database.

Use the `--help`` option for more options, e.g. for specifying two locations and requesting that a number of weather stations cover the bounding box between them (where the locations represent top left and bottom right).

Notes about weather sensor setup: 

- Weather sensors are public assets in FlexMeasures. They are accessible by all accounts on a FlexMeasures server.
- The resolution is one hour. Weather also supports minutely data within the upcoming hour(s), but that is not supported here.

## Forecasting PV power output

Once you have an `irradiance` sensor with forecasts, this plugin can turn those into an
expected AC power forecast for a specific PV array, using pvlib. This needs no metered
history at all, which is what makes it useful for a newly commissioned array or one without historical data - and it makes
a good regressor for FlexMeasures' own trained forecaster once history does exist. See
[docs/pv-power-design.md](docs/pv-power-design.md).

First, register an irradiance sensor at the array's location, and collect forecasts for it:

`flexmeasures weather register-weather-sensor --name irradiance --latitude 52.09 --longitude 5.12`

`flexmeasures weather get-weather-forecasts --location 52.09,5.12`

Then register the array's specs on its asset. The asset needs a latitude and longitude of
its own (pvlib computes solar positions from those), and a power sensor to forecast onto:

`flexmeasures weather register-pv-array --asset-id 7 --tilt 30 --azimuth 180 --capacity-kw 5.4`

- `--tilt`: degrees from horizontal; 0 is flat, 90 is vertical.
- `--azimuth`: degrees clockwise from north; 180 is south.
- `--capacity-kw`: DC nameplate capacity in kW (kWp).
- `--losses` (optional): aggregate DC loss fraction, defaulting to the PVWatts 0.14. Raise
  it for a shaded or soiled array.
- `--mount` (optional): how the array is mounted, affecting how hot the cells run:
  `open_rack` (ground/pole-mounted or well-ventilated), `close_mount` (roof-racked with
  limited airflow behind the panels), or `insulated_back` (flush-mounted or
  building-integrated). Defaults to `close_mount`.

These are stored as the asset attributes `pv_tilt`, `pv_azimuth`, `pv_capacity_in_kw`,
`pv_losses` and `pv_mount`. Stock `flexmeasures edit attribute` can write them one at a time; this command
exists to validate a whole set at once.

Now forecast:

`flexmeasures add forecasts --sensor 42 --forecaster PVWattsForecaster`

where 42 is the array's power sensor. The forecaster picks the nearest registered
irradiance sensor. Everything else is the stock `flexmeasures add forecasts` interface, so
`--start`, `--end`, `--duration`, `--max-forecast-horizon` and `--forecast-frequency` all
work as usual, and `flexmeasures show forecasters` should list `PVWattsForecaster`. Note
that `--as-job` is not supported: pvlib computes in milliseconds, so there is nothing to
queue.

To point at a specific irradiance sensor, or to try out different array specs without
editing the asset, pass a config file:

```json
{
  "irradiance-sensor": 39,
  "tilt": 15
}
```

`flexmeasures add forecasts --sensor 42 --forecaster PVWattsForecaster --config whatif.json`

Because the array specs live on the asset rather than in the config, changing the tilt on
the asset does not create a new data source, whereas a config override does. Prefer the
config overrides for what-if runs.

### A more accurate alternative: `BlendedPVForecaster`

`PVWattsForecaster` above depends on the `irradiance` sensor, which this plugin derives
from WeatherAPI/OWM cloud cover. It might be more accurate to combine a global model with
a local model to average them out, rather than rely on a single derived source.
`BlendedPVForecaster` is a drop-in alternative that fetches two free, key-less providers
live and averages their independent PVWatts power estimates. It currently only combines
one fixed pair - Bright Sky's DWD forecast and Open-Meteo's `ecmwf_ifs025` model. The
Open-Meteo side works everywhere; Bright Sky only works where DWD MOSMIX has coverage, so
the blend as a whole is currently limited to those places - see
[docs/pv-power-design.md](docs/pv-power-design.md) for how to check a given site first.
PRs adding other providers/models for better coverage elsewhere are welcome. A different
blend can be injected via the `providers` constructor argument (not available from the
CLI, which always gets the default pair). It needs no irradiance
sensor and no `get-weather-forecasts` step, just the same registered array specs:

`flexmeasures add forecasts --sensor 42 --forecaster BlendedPVForecaster`

It exists alongside `PVWattsForecaster`, not instead of it - both remain selectable
`--forecaster` options, and existing pipelines built on the `irradiance` sensor keep
working unchanged.

An alternative usage is to save raw results in JSON files (for later processing), like this:

`flexmeasures weather get-weather-forecasts --location 30,40 --store-as-json-files --region somewhere`

This saves the complete response from the Weather Provider in a local folder (i.e. no sensor registration needed, this is a direct way to use Weather APIs, without FlexMeasures integration). `region` will become a subfolder.
 
Finally, note that these APIs allow free calls, but not without limits.
For instance, currently 1000 free calls per day can be made to the OpenWeatherMap API,
so you can make a call every 15 minutes for up to 10 locations or every hour for up to 40 locations (or get a paid account).


## Setup

### Installation

To add as plugin to an existing FlexMeasures system, add "/path/to/flexmeasures-weather-repo/flexmeasures_weather" to your FlexMeasures config file,
using the FLEXMEASURES_PLUGINS setting (a list).

Alternatively, if you installed this plugin as a package (e.g. via `python setup.py install`, `pip install -e` or `pip install flexmeasures_weather` after this project is on Pypi), then "flexmeasures_weather" suffices.

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

# File path to store weather data in JSON format
WEATHER_FILE_PATH_LOCATION = "/path/to/weather_output.json"
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

   This function should return data in the same structure as used by the original OpenWeatherMap integration, and **must have at least 48 hours of forecast data from the time of the call**.

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
   Modify the `call_api` function in the `weather.py` file to include a conditional branch for the new provider:

   ```python
   def call_api(...):
    if provider not in ['OWM', 'WAPI', ..., 'NEWAPI']:
        raise Exception
    if provider == 'NEWAPI':
        return call_NEWAPI_api(...)
   ```

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
