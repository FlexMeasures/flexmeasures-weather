from types import SimpleNamespace

import pytest

from flexmeasures import Asset

import flexmeasures_weather.utils.modeling as modeling
from flexmeasures_weather import DEFAULT_DATA_SOURCE_NAME, DEFAULT_WEATHER_STATION_NAME
from flexmeasures_weather.utils.modeling import (
    FM_SUPPORTS_ACCOUNT_LINKED_SOURCES,
    SOURCE_TYPE,
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
    get_or_create_weather_account,
    get_or_create_weather_station,
)


def test_creating_two_weather_stations(fresh_db):
    get_or_create_weather_station(50, 40)
    get_or_create_weather_station(40, 50)
    assert Asset.query.filter(Asset.name == DEFAULT_WEATHER_STATION_NAME).count() == 2


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
