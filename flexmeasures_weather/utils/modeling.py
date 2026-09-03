from flask import current_app
from flexmeasures.data.models.generic_assets import GenericAsset, GenericAssetType
from flexmeasures import Account, Source
from flexmeasures.data import db
from flexmeasures.data.services.data_sources import get_or_create_source

from flexmeasures_weather import DEFAULT_DATA_SOURCE_NAME
from flexmeasures_weather import WEATHER_STATION_TYPE_NAME
from flexmeasures_weather import DEFAULT_WEATHER_STATION_NAME

SOURCE_TYPE = "forecaster"


def get_or_create_weather_account():
    """Make sure we have an account for the weather provider service."""
    account_name = current_app.config.get(
        "WEATHER_DATA_SOURCE_NAME", DEFAULT_DATA_SOURCE_NAME
    )
    weather_account = Account.query.filter(
        Account.name == account_name,
    ).one_or_none()
    if weather_account is None:
        weather_account = Account(name=account_name)
        db.session.add(weather_account)
        db.session.flush()
    return weather_account


def get_or_create_owm_data_source() -> Source:
    """Make sure we have a weather provider data source of the configured type.

    TODO: account scoping is newly active as of the FlexMeasures 1.0 upgrade, which
          splits pre-upgrade weather sources from new ones. Maintainer call on whether
          existing deployments need a migration - see docs/fm-1.0-upgrade-notes.md.
    """
    return get_or_create_source(
        source=current_app.config.get(
            "WEATHER_DATA_SOURCE_NAME", DEFAULT_DATA_SOURCE_NAME
        ),
        source_type=SOURCE_TYPE,
        account=get_or_create_weather_account(),
        flush=False,
    )


def get_or_create_owm_data_source_for_derived_data() -> Source:
    owm_source_name = current_app.config.get(
        "WEATHER_DATA_SOURCE_NAME", DEFAULT_DATA_SOURCE_NAME
    )
    return get_or_create_source(
        source=f"FlexMeasures {owm_source_name}",
        source_type=SOURCE_TYPE,
        account=get_or_create_weather_account(),
        flush=False,
    )


def get_or_create_weather_station_type() -> GenericAssetType:
    """Make sure a weather station type exists"""
    weather_station_type = GenericAssetType.query.filter(
        GenericAssetType.name == WEATHER_STATION_TYPE_NAME,
    ).one_or_none()
    if weather_station_type is None:
        weather_station_type = GenericAssetType(
            name=WEATHER_STATION_TYPE_NAME,
            description="A weather station with various sensors.",
        )
        db.session.add(weather_station_type)
    return weather_station_type


def make_weather_station_name(latitude: float, longitude: float) -> str:
    """Name for the weather station at this location.

    FlexMeasures >= 1.0 requires root asset names to be unique, and we create one station per location - see docs/fm-1.0-upgrade-notes.md.
    """
    station_name = current_app.config.get(
        "WEATHER_STATION_NAME", DEFAULT_WEATHER_STATION_NAME
    )
    suffix = f" ({round(latitude, 4)}, {round(longitude, 4)})"
    # Truncate the configurable part, not the coordinates, so the name stays unique
    return station_name[: 80 - len(suffix)] + suffix


def get_or_create_weather_station(latitude: float, longitude: float) -> GenericAsset:
    """Make sure a weather station exists at this location."""
    weather_station = GenericAsset.query.filter(
        GenericAsset.latitude == latitude, GenericAsset.longitude == longitude
    ).one_or_none()
    if weather_station is None:
        weather_station_type = get_or_create_weather_station_type()
        weather_station = GenericAsset(
            name=make_weather_station_name(latitude, longitude),
            generic_asset_type=weather_station_type,
            latitude=latitude,
            longitude=longitude,
        )
        db.session.add(weather_station)
    return weather_station


def get_weather_station_by_asset_id(asset_id: int) -> GenericAsset:
    weather_station = GenericAsset.query.filter(
        GenericAsset.generic_asset_type_id == asset_id
    ).one_or_none()
    if weather_station is None:
        raise Exception(
            f"[FLEXMEASURES-WEATHER] Weather station is not present for the given asset id '{asset_id}'."
        )

    if weather_station.latitude is None or weather_station.longitude is None:
        raise Exception(
            f"[FLEXMEASURES-WEATHER] Weather station {weather_station} is missing location information [Latitude, Longitude]."
        )

    return weather_station
