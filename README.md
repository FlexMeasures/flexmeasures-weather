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

Alternatively, if you installed this plugin as a package (e.g. via `uv sync` in this repo, `uv pip install -e .` or `pip install flexmeasures_weather` after this project is on Pypi), then "flexmeasures_weather" suffices.

Note that `uv sync` already installs this project itself in editable mode, so a separate `uv pip install -e .` is only needed if you are installing into an environment you manage yourself.

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

We manage dependencies with [uv](https://docs.astral.sh/uv/). The `uv.lock` file is committed, so
everyone resolves to the same versions. This project pins the uv version it expects
(`0.12.7`, see `[tool.uv].required-version` in `pyproject.toml`) - other uv versions will refuse
to run rather than silently rewrite the lock file.

Set up a development environment and install the pre-commit hooks with:

    uv sync
    uv run pre-commit install

Try the hooks out:

    uv run pre-commit run --all-files --show-diff-on-failure

The hooks (flake8, black, mypy) all run through `uv run`, so their versions come from `uv.lock`
rather than from the pre-commit config.

Common tasks are defined as [poethepoet](https://poethepoet.natn.io/) tasks:

    uv run poe test         # run the test suite (needs a test database, see below)
    uv run poe type-check   # run mypy over flexmeasures_weather

### Running the tests

The test suite needs a PostgreSQL database with the `cube` and `earthdistance` extensions
loaded. Start a throwaway one in Docker (port 5544) and remove it again with:

    uv run poe test-db-start
    uv run poe test
    uv run poe test-db-stop

**Careful if you bring your own database.** FlexMeasures' `testing` config defaults to
port 5432 - often your *development* database. Our fixtures create/drop schema, which
would destroy that data. `poe test-db-start` avoids this by binding to port **5544**
instead, and `poe test` points `SQLALCHEMY_TEST_DATABASE_URI` there by default.

To use a different test database, set `SQLALCHEMY_TEST_DATABASE_URI` yourself (this is
the variable FlexMeasures reads in `testing`, *not* `SQLALCHEMY_DATABASE_URI`):

    SQLALCHEMY_TEST_DATABASE_URI=postgresql://user:pass@host:port/dbname uv run poe test

Running `pytest` directly skips that default - set the variable explicitly then too.
