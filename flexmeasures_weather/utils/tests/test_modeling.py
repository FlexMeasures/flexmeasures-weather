from types import SimpleNamespace

import pytest

from flexmeasures import Asset
from flexmeasures.data.models.generic_assets import GenericAssetType

import flexmeasures_weather.utils.modeling as modeling
from flexmeasures_weather import DEFAULT_DATA_SOURCE_NAME, DEFAULT_WEATHER_STATION_NAME
from flexmeasures_weather.utils.modeling import (
    FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    SOURCE_TYPE,
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
    get_or_create_weather_account,
    get_or_create_weather_station,
    get_weather_station_by_asset_id,
)


def test_creating_two_weather_stations(fresh_db):
    first = get_or_create_weather_station(50, 40)
    second = get_or_create_weather_station(40, 50)
    fresh_db.session.flush()
    assert first.name == DEFAULT_WEATHER_STATION_NAME
    assert second.name == f"{DEFAULT_WEATHER_STATION_NAME}-1"
    assert get_or_create_weather_station(50, 40) == first


def test_station_name_skips_existing_public_assets(app, fresh_db, monkeypatch):
    monkeypatch.setitem(app.config, "WEATHER_STATION_NAME", "x" * 80)
    existing = Asset(
        name="x" * 78 + "-1", generic_asset_type=GenericAssetType(name="site")
    )
    fresh_db.session.add(existing)
    first = get_or_create_weather_station(50, 40)
    second = get_or_create_weather_station(40, 50)
    fresh_db.session.flush()
    assert first.name == "x" * 80
    assert second.name == "x" * 78 + "-2"


def test_get_asset_by_id_requires_existing_location(fresh_db):
    asset = Asset(
        name="Site without location", generic_asset_type=GenericAssetType(name="site")
    )
    fresh_db.session.add(asset)
    fresh_db.session.flush()
    with pytest.raises(Exception, match="missing location information"):
        get_weather_station_by_asset_id(asset.id)
    with pytest.raises(Exception, match="No asset found"):
        get_weather_station_by_asset_id(asset.id + 10000)


# The version-branch tests below still use monkeypatching to isolate source
# creation side effects without requiring multiple FlexMeasures installs.
@pytest.mark.skipif(
    not FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Weather source accounts are only supported on FlexMeasures >= 0.32.",
)
def test_get_or_create_weather_account(fresh_db):
    weather_account = get_or_create_weather_account()

    assert weather_account.name == DEFAULT_DATA_SOURCE_NAME
    assert (
        modeling.Account.query.filter(
            modeling.Account.name == weather_account.name
        ).count()
        == 1
    )


@pytest.mark.skipif(
    not FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Account-linked weather sources are only supported on FlexMeasures >= 0.32.",
)
def test_get_or_create_owm_data_source_registers_weather_source_on_weather_account(
    fresh_db,
):
    data_source = get_or_create_owm_data_source()

    assert data_source.type == SOURCE_TYPE
    assert data_source.account is not None
    assert data_source.account.name == data_source.name


@pytest.mark.skipif(
    not FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Account-linked weather sources are only supported on FlexMeasures >= 0.32.",
)
def test_get_or_create_owm_data_source_for_derived_data_uses_weather_account(fresh_db):
    derived_data_source = get_or_create_owm_data_source_for_derived_data()

    assert derived_data_source.type == SOURCE_TYPE
    assert derived_data_source.account is not None
    assert derived_data_source.account.name == DEFAULT_DATA_SOURCE_NAME


@pytest.mark.skipif(
    not FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Account-linked weather sources are only supported on FlexMeasures >= 0.32.",
)
def test_get_or_create_owm_data_source_passes_weather_account_when_supported(
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

    data_source = get_or_create_owm_data_source()

    assert data_source.type == SOURCE_TYPE
    assert captured_kwargs["account"].name == DEFAULT_DATA_SOURCE_NAME


@pytest.mark.skipif(
    not FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Account-linked weather sources are only supported on FlexMeasures >= 0.32.",
)
def test_get_or_create_owm_derived_data_source_passes_weather_account_when_supported(
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
    assert captured_kwargs["account"].name == DEFAULT_DATA_SOURCE_NAME


@pytest.mark.skipif(
    FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Legacy source creation without accounts is only used on FlexMeasures < 0.32.",
)
def test_get_or_create_owm_data_source_omits_account_when_not_supported(monkeypatch):
    captured_kwargs = {}

    def fake_get_or_create_source(source, source_type, flush):
        captured_kwargs.update(
            dict(
                source=source,
                source_type=source_type,
                flush=flush,
            )
        )
        return SimpleNamespace(type=source_type, name=source)

    monkeypatch.setattr(
        "flexmeasures_weather.utils.modeling.get_or_create_source",
        fake_get_or_create_source,
    )

    data_source = get_or_create_owm_data_source()

    assert data_source.type == SOURCE_TYPE
    assert captured_kwargs == {
        "source": DEFAULT_DATA_SOURCE_NAME,
        "source_type": SOURCE_TYPE,
        "flush": False,
    }


@pytest.mark.skipif(
    FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    reason="Legacy source creation without accounts is only used on FlexMeasures < 0.32.",
)
def test_get_or_create_owm_derived_data_source_omits_account_when_not_supported(
    monkeypatch,
):
    captured_kwargs = {}

    def fake_get_or_create_source(source, source_type, flush):
        captured_kwargs.update(
            dict(
                source=source,
                source_type=source_type,
                flush=flush,
            )
        )
        return SimpleNamespace(type=source_type, name=source)

    monkeypatch.setattr(
        "flexmeasures_weather.utils.modeling.get_or_create_source",
        fake_get_or_create_source,
    )

    data_source = get_or_create_owm_data_source_for_derived_data()

    assert data_source.type == SOURCE_TYPE
    assert captured_kwargs == {
        "source": f"FlexMeasures {DEFAULT_DATA_SOURCE_NAME}",
        "source_type": SOURCE_TYPE,
        "flush": False,
    }
