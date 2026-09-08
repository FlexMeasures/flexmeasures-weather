from flask import current_app

from flask.cli import with_appcontext
import click
from flexmeasures.data.models.time_series import Sensor

from flexmeasures.data.transactional import task_with_status_report
from flexmeasures.data.config import db

from .. import flexmeasures_weather_bp
from .schemas.weather_sensor import WeatherSensorSchema
from ..pv.specs import PV_ATTRIBUTES, PVSpecsSchema
from ..pv.utils.solar_power import DEFAULT_MOUNT, MOUNT_TEMPERATURE_MODELS
from ..utils.modeling import (
    get_or_create_weather_station,
    get_weather_station_by_asset_id,
)
from ..utils.locating import get_locations, get_location_by_asset_id
from ..utils.filing import make_file_path
from ..utils.weather import (
    save_forecasts_in_db,
    save_forecasts_as_json,
    get_supported_sensor_spec,
)
from ..sensor_specs import mapping

from flexmeasures.data.models.generic_assets import GenericAsset
from marshmallow import ValidationError

"""
TODO: allow to also pass an asset ID or name for the weather station (instead of location) to both commands?
      See https://github.com/FlexMeasures/flexmeasures-weather
"""

supported_sensors_list = ", ".join(
    [str(sensor_specs["fm_sensor_name"]) for sensor_specs in mapping]
)


@flexmeasures_weather_bp.cli.command("register-weather-sensor")
@with_appcontext
@click.option(
    "--name",
    required=True,
    help=f"Name of the sensor. Has to be from the supported list ({supported_sensors_list})",
)
@click.option(
    "--asset-id",
    required=False,
    type=int,
    help="The asset id of the weather station (you can also give its location).",
)
@click.option(
    "--latitude",
    required=False,
    type=float,
    help="Latitude of where you want to measure.",
)
@click.option(
    "--longitude",
    required=False,
    type=float,
    help="Longitude of where you want to measure.",
)
@click.option(
    "--timezone",
    default="UTC",
    help="The timezone of the sensor data as string, e.g. 'UTC' (default) or 'Europe/Amsterdam'",
)
def add_weather_sensor(**args):
    """
    Add a weather sensor.
    This will first create a weather station asset if none exists at the location yet.

    """
    errors = WeatherSensorSchema().validate(args)
    if errors:
        click.echo(
            f"[FLEXMEASURES-WEATHER] Please correct the following errors:\n{errors}.\n Use the --help flag to learn more."
        )
        raise click.Abort
    if args["asset_id"] is not None:
        weather_station = get_weather_station_by_asset_id(args["asset_id"])
    elif args["latitude"] is not None and args["longitude"] is not None:
        weather_station = get_or_create_weather_station(
            args["latitude"], args["longitude"]
        )
    else:
        raise Exception(
            "Arguments are missing to register a weather sensor. Provide either '--asset-id' or ('--latitude' and '--longitude')."
        )

    sensor = Sensor.query.filter(
        Sensor.name == args["name"].lower(),
        Sensor.generic_asset == weather_station,
    ).one_or_none()
    if sensor:
        click.echo(
            f"[FLEXMEASURES-WEATHER] A '{args['name']}' weather sensor already exists at this weather station (the station's ID is {weather_station.id})."
        )
        return
    fm_sensor_specs = get_supported_sensor_spec(args["name"])
    fm_sensor_specs["generic_asset"] = weather_station
    fm_sensor_specs["timezone"] = args["timezone"]
    fm_sensor_specs["name"] = fm_sensor_specs.pop("fm_sensor_name")
    fm_sensor_specs.pop("source_field")
    sensor = Sensor(**fm_sensor_specs)
    sensor.attributes = fm_sensor_specs["attributes"]

    db.session.add(sensor)
    db.session.commit()
    click.echo(
        f"[FLEXMEASURES-WEATHER] Successfully created weather sensor with ID {sensor.id}, at weather station with ID {weather_station.id}"
    )
    click.echo(
        f"[FLEXMEASURES-WEATHER] You can access this sensor at its entity address {sensor.entity_address}"
    )


@flexmeasures_weather_bp.cli.command("get-weather-forecasts")
@with_appcontext
@click.option(
    "--location",
    type=str,
    required=False,
    help='Measurement location(s). "latitude,longitude" or "top-left-latitude,top-left-longitude:'
    'bottom-right-latitude,bottom-right-longitude." The first format defines one location to measure.'
    " The second format defines a region of interest with several (>=4) locations"
    ' (see also the "method" and "num_cells" parameters for details on how to use this feature).',
)
@click.option(
    "--asset-id",
    type=int,
    required=False,
    help="ID of a weather station asset - forecasts will be gotten for its location. If present, --location will be ignored.",
)
@click.option(
    "--store-in-db/--store-as-json-files",
    default=True,
    help="Store forecasts in the database, or simply save as json files (defaults to database).",
)
@click.option(
    "--num_cells",
    type=int,
    default=1,
    help="Number of cells on the grid. Only used if a region of interest has been mapped in the location parameter. Defaults to 1.",
)
@click.option(
    "--method",
    default="hex",
    type=click.Choice(["hex", "square"]),
    help="Grid creation method. Only used if a region of interest has been mapped in the location parameter.",
)
@click.option(
    "--region",
    type=str,
    default="",
    help="Name of the region (will create sub-folder if you store json files).",
)
@task_with_status_report("get-weather-forecasts")
def collect_weather_data(location, asset_id, store_in_db, num_cells, method, region):
    """
    Collect weather forecasts from the Weather Provider API.
    This will be done for one or more locations, for which we first identify relevant weather stations.

    This function can get weather data for one location or for several locations within
    a geometrical grid (See the --location parameter).
    """

    api_key = str(
        current_app.config.get(
            "WEATHERAPI_KEY", current_app.config.get("OPENWEATHERMAP_API_KEY", "")
        )
    )
    if api_key == "":
        raise Exception("[FLEXMEASURES-WEATHER] Setting WEATHERAPI_KEY not available.")
    if asset_id is not None:
        locations = [get_location_by_asset_id(asset_id)]
    elif location is not None:
        locations = get_locations(location, num_cells, method)
    else:
        raise Warning(
            "[FLEXMEASURES-WEATHER] Pass either location or asset-id to get weather forecasts."
        )

    # Save the results
    if store_in_db:
        save_forecasts_in_db(api_key, locations)
    else:
        save_forecasts_as_json(
            api_key, locations, data_path=make_file_path(current_app, region)
        )


@flexmeasures_weather_bp.cli.command("register-pv-array")
@with_appcontext
@click.option(
    "--asset-id",
    required=True,
    type=int,
    help="The asset id of the PV installation whose specs you are registering.",
)
@click.option(
    "--tilt",
    required=True,
    type=float,
    help="Tilt of the array from horizontal, in degrees. 0 is flat, 90 is vertical.",
)
@click.option(
    "--azimuth",
    required=True,
    type=float,
    help="Direction the array faces, in degrees clockwise from north. 180 is south.",
)
@click.option(
    "--capacity-kw",
    required=True,
    type=float,
    help="DC nameplate capacity of the array, in kW (kWp).",
)
@click.option(
    "--losses",
    required=False,
    type=float,
    default=None,
    help="Fraction of DC output lost to soiling, shading, mismatch, wiring and the like,"
    " between 0 and 1. Defaults to the PVWatts default of 0.14.",
)
@click.option(
    "--mount",
    required=False,
    type=click.Choice(sorted(MOUNT_TEMPERATURE_MODELS)),
    default=None,
    help="How the array is mounted, affecting how hot the cells run: 'open_rack'"
    " (ground/pole-mounted or well-ventilated), 'close_mount' (roof-racked with limited"
    " airflow behind the panels), or 'insulated_back' (flush-mounted or"
    f" building-integrated). Defaults to '{DEFAULT_MOUNT}'.",
)
def register_pv_array(asset_id, tilt, azimuth, capacity_kw, losses, mount):
    """
    Register the specs of a PV array on its asset, for PV power forecasting.

    These are stored as asset attributes, because they are physical properties of the
    installation rather than of any one forecasting run. The stock
    'flexmeasures edit attribute' command can write them one key at a time; this command
    exists to validate a whole set of specs at once.

    The array's location is taken from the asset's own latitude and longitude, so make
    sure those are set too.
    """
    asset = GenericAsset.query.filter(GenericAsset.id == asset_id).one_or_none()
    if asset is None:
        click.echo(f"[FLEXMEASURES-WEATHER] No asset found with ID {asset_id}.")
        raise click.Abort

    specs = {
        "pv_tilt": tilt,
        "pv_azimuth": azimuth,
        "pv_capacity_in_kw": capacity_kw,
    }
    if losses is not None:
        specs["pv_losses"] = losses
    if mount is not None:
        specs["pv_mount"] = mount
    try:
        specs = PVSpecsSchema().load(specs)
    except ValidationError as error:
        click.echo(
            "[FLEXMEASURES-WEATHER] Please correct the following errors:\n"
            f"{error.messages}.\n Use the --help flag to learn more."
        )
        raise click.Abort

    if asset.latitude is None or asset.longitude is None:
        click.echo(
            f"[FLEXMEASURES-WEATHER] Warning: asset {asset_id} has no latitude and/or"
            " longitude yet. PV power forecasts need them, to compute solar positions."
        )

    # Assign a new dict rather than mutating in place: SQLAlchemy does not track
    # mutations of a JSONB column, and GenericAsset.set_attribute only overwrites
    # attributes that already exist.
    asset.attributes = {**(asset.attributes or {}), **specs}
    db.session.add(asset)
    db.session.commit()

    click.echo(
        f"[FLEXMEASURES-WEATHER] Registered PV array specs on asset {asset.name}"
        f" (ID {asset.id}): "
        + ", ".join(f"{attribute}={specs[attribute]}" for attribute in PV_ATTRIBUTES)
    )
    click.echo(
        "[FLEXMEASURES-WEATHER] You can now forecast its power output with:"
        " flexmeasures add forecasts --sensor <power sensor ID>"
        " --forecaster PVWattsForecaster"
    )
