import inspect
from types import SimpleNamespace

from flexmeasures import Asset
from flexmeasures import Account

from flexmeasures_weather import DEFAULT_WEATHER_STATION_NAME
from flexmeasures_weather.utils.modeling import (
    get_or_create_owm_data_source,
    get_or_create_owm_data_source_for_derived_data,
    get_or_create_weather_account,
    get_or_create_weather_station,
)


def test_creating_two_weather_stations(fresh_db):
    get_or_create_weather_station(50, 40)
    get_or_create_weather_station(40, 50)
    assert Asset.query.filter(Asset.name == DEFAULT_WEATHER_STATION_NAME).count() == 2


def test_get_or_create_weather_account(fresh_db):
    weather_account = get_or_create_weather_account()

    assert weather_account.name == "Weather"
    assert Account.query.filter(Account.name == weather_account.name).count() == 1


def test_get_or_create_owm_data_source_registers_market_source_on_weather_account(
    fresh_db,
):
    data_source = get_or_create_owm_data_source()

    assert data_source.type == "market"
    if (
        "account"
        in inspect.signature(
            get_or_create_owm_data_source.__globals__["get_or_create_source"]
        ).parameters
    ):
        assert data_source.account is not None
        assert data_source.account.name == data_source.name
    else:
        assert Account.query.filter(Account.name == data_source.name).count() == 0


def test_get_or_create_owm_data_source_for_derived_data_uses_weather_account(fresh_db):
    derived_data_source = get_or_create_owm_data_source_for_derived_data()

    assert derived_data_source.type == "forecaster"
    if (
        "account"
        in inspect.signature(
            get_or_create_owm_data_source.__globals__["get_or_create_source"]
        ).parameters
    ):
        assert derived_data_source.account is not None
        assert derived_data_source.account.name == "Weather"
    else:
        assert Account.query.filter(Account.name == "Weather").count() == 0


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

    assert data_source.type == "market"
    assert captured_kwargs["account"].name == "Weather"


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

    assert data_source.type == "forecaster"
    assert captured_kwargs["account"].name == "Weather"
