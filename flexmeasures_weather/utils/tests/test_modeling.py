from types import SimpleNamespace

from flexmeasures import Asset

import flexmeasures_weather.utils.modeling as modeling
from flexmeasures_weather import DEFAULT_DATA_SOURCE_NAME, DEFAULT_WEATHER_STATION_NAME
from flexmeasures_weather.utils.modeling import (
    SOURCE_TYPE,
    make_weather_station_name,
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
    get_or_create_weather_account,
    get_or_create_weather_station,
)


def test_creating_two_weather_stations(fresh_db):
    """Two stations at different locations coexist.

    FlexMeasures >= 1.0 requires unique root asset names, hence the location suffix.
    """
    first = get_or_create_weather_station(50, 40)
    second = get_or_create_weather_station(40, 50)

    assert first.name != second.name
    assert (
        Asset.query.filter(
            Asset.name.startswith(DEFAULT_WEATHER_STATION_NAME[:20])
        ).count()
        == 2
    )


def test_getting_existing_weather_station_is_by_location(fresh_db):
    created = get_or_create_weather_station(50, 40)
    fresh_db.session.flush()

    assert get_or_create_weather_station(50, 40).id == created.id
    assert Asset.query.filter(Asset.name == created.name).count() == 1


def test_weather_station_name_fits_the_name_column(fresh_db, monkeypatch):
    monkeypatch.setitem(modeling.current_app.config, "WEATHER_STATION_NAME", "w" * 100)

    name = make_weather_station_name(52.123456, -5.654321)

    assert len(name) <= 80
    assert name.endswith(" (52.1235, -5.6543)")


def test_get_or_create_weather_account(fresh_db):
    weather_account = get_or_create_weather_account()

    assert weather_account.name == DEFAULT_DATA_SOURCE_NAME
    assert (
        modeling.Account.query.filter(
            modeling.Account.name == weather_account.name
        ).count()
        == 1
    )


def test_get_or_create_weather_account_is_idempotent(fresh_db):
    first = get_or_create_weather_account()
    second = get_or_create_weather_account()

    assert first.id == second.id
    assert (
        modeling.Account.query.filter(
            modeling.Account.name == DEFAULT_DATA_SOURCE_NAME
        ).count()
        == 1
    )


def test_get_or_create_owm_data_source_registers_weather_source_on_weather_account(
    fresh_db,
):
    data_source = get_or_create_owm_data_source()

    assert data_source.type == SOURCE_TYPE
    assert data_source.account is not None
    assert data_source.account.name == data_source.name


def test_get_or_create_owm_data_source_is_idempotent(fresh_db):
    """The account-scoped lookup should find the source it created before."""
    first = get_or_create_owm_data_source()
    fresh_db.session.flush()
    second = get_or_create_owm_data_source()

    assert first.id == second.id


def test_get_or_create_owm_data_source_for_derived_data_uses_weather_account(fresh_db):
    derived_data_source = get_or_create_owm_data_source_for_derived_data()

    assert derived_data_source.type == SOURCE_TYPE
    assert derived_data_source.account is not None
    assert derived_data_source.account.name == DEFAULT_DATA_SOURCE_NAME


def test_get_or_create_owm_data_source_passes_weather_account(fresh_db, monkeypatch):
    captured_kwargs = {}

    def fake_get_or_create_source(source, source_type, account, flush):
        captured_kwargs.update(
            dict(
                source=source,
                source_type=source_type,
                account=account,
                flush=flush,
            )
        )
        return SimpleNamespace(type=source_type, account=account)

    monkeypatch.setattr(
        "flexmeasures_weather.utils.modeling.get_or_create_source",
        fake_get_or_create_source,
    )

    data_source = get_or_create_owm_data_source()

    assert data_source.type == SOURCE_TYPE
    assert captured_kwargs["source"] == DEFAULT_DATA_SOURCE_NAME
    assert captured_kwargs["account"].name == DEFAULT_DATA_SOURCE_NAME


def test_get_or_create_owm_derived_data_source_passes_weather_account(
    fresh_db, monkeypatch
):
    captured_kwargs = {}

    def fake_get_or_create_source(source, source_type, account, flush):
        captured_kwargs.update(
            dict(
                source=source,
                source_type=source_type,
                account=account,
                flush=flush,
            )
        )
        return SimpleNamespace(type=source_type, account=account)

    monkeypatch.setattr(
        "flexmeasures_weather.utils.modeling.get_or_create_source",
        fake_get_or_create_source,
    )

    data_source = get_or_create_owm_data_source_for_derived_data()

    assert data_source.type == SOURCE_TYPE
    assert captured_kwargs["source"] == f"FlexMeasures {DEFAULT_DATA_SOURCE_NAME}"
    assert captured_kwargs["account"].name == DEFAULT_DATA_SOURCE_NAME
