"""Tests for the Bright Sky fetch.

No database and no Flask, so the module under test is loaded by path and these run with
``pytest --noconftest``. See the note at the top of ``test_solar_power.py``.

HTTP is mocked by monkeypatching ``requests.get`` with a fake response object, rather than
pulling in an HTTP-mocking library the rest of this plugin doesn't otherwise use.
"""

import importlib.util
import logging
import sys
from pathlib import Path

import pandas as pd
import pytest

_PACKAGE_DIR = Path(__file__).parent.parent


def _load_weather_providers_module(name: str):
    """Load a ``weather_providers`` submodule by path, without importing
    ``flexmeasures_weather`` (which would pull in Flask/the CLI/DB - see the note at the
    top of ``test_solar_power.py``). ``weather_providers.brightsky`` does ``from .base
    import WeatherProvider``, so a synthetic ``weather_providers`` package is registered in
    ``sys.modules`` first (its ``__init__`` is never executed) purely so that relative
    import can resolve ``base.py`` from the same directory.
    """
    package_name = "weather_providers"
    if package_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            package_name,
            _PACKAGE_DIR / "__init__.py",
            submodule_search_locations=[str(_PACKAGE_DIR)],
        )
        assert pkg_spec is not None
        sys.modules[package_name] = importlib.util.module_from_spec(pkg_spec)

    full_name = f"{package_name}.{name}"
    spec = importlib.util.spec_from_file_location(
        full_name, _PACKAGE_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


brightsky = _load_weather_providers_module("brightsky")


def fetch_brightsky_weather(*args, **kwargs):
    return brightsky.BrightSkyProvider().fetch_hourly_forecast(*args, **kwargs)


def _at(records, time: pd.Timestamp):
    """Find the record with the given interval-start time."""
    (match,) = [r for r in records if r.time == time]
    return match


TIMEZONE = "Europe/Amsterdam"
LATITUDE = 52.09
LONGITUDE = 5.12


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise brightsky.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def _record(timestamp: str, solar, cloud_cover=50, temperature=18.0, wind_speed=10.8):
    return {
        "timestamp": timestamp,
        "solar": solar,
        "cloud_cover": cloud_cover,
        "temperature": temperature,
        "wind_speed": wind_speed,
    }


def test_native_solar_is_shifted_back_one_hour(monkeypatch):
    """Regression guard: Bright Sky's `solar` is a trailing-60-minute sum reported at the
    interval's END. Without the shift(-1), this reads as a lag against every other
    provider's interval-start convention."""
    records = [
        _record("2024-06-21T10:00:00+02:00", solar=0.10),  # describes 09:00-10:00
        _record("2024-06-21T11:00:00+02:00", solar=0.50),  # describes 10:00-11:00
        _record("2024-06-21T12:00:00+02:00", solar=0.80),  # describes 11:00-12:00
    ]
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": records})
    )

    weather = fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    # The 09:00-10:00 bucket's GHI comes from the value reported at 10:00 (0.10 kWh/m^2 ->
    # 100 W/m^2), not from the value reported at 09:00 (which doesn't exist in this fixture,
    # so it must fall back).
    ten_am = pd.Timestamp("2024-06-21 10:00", tz=TIMEZONE)
    assert _at(weather, ten_am).ghi_wm2 == pytest.approx(500.0)  # 0.50 * 1000
    eleven_am = pd.Timestamp("2024-06-21 11:00", tz=TIMEZONE)
    assert _at(weather, eleven_am).ghi_wm2 == pytest.approx(800.0)  # 0.80 * 1000


def test_null_solar_falls_back_to_campbell_norman(monkeypatch):
    """The rare hours before the current MOSMIX run's first_record have `solar: null` -
    here, the last fetched record has nothing at T+1 to shift(-1) in from."""
    records = [
        _record("2024-06-21T10:00:00+02:00", solar=0.50, cloud_cover=0),
        _record("2024-06-21T11:00:00+02:00", solar=None, cloud_cover=0),
    ]
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": records})
    )

    weather = fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    eleven_am = pd.Timestamp("2024-06-21 11:00", tz=TIMEZONE)
    # shift(-1) leaves this bucket's native GHI null (no record at 12:00), so it must have
    # come from the campbell_norman fallback - a clear-sky-ish daytime value under a clear
    # sky (cloud_cover=0), not None and not zero.
    ghi = _at(weather, eleven_am).ghi_wm2
    assert ghi is not None
    assert ghi > 0


def test_all_null_solar_logs_a_warning(monkeypatch, caplog):
    """Regression guard for a real finding: some MOSMIX stations never report `solar` at
    all (not a shorter horizon, not a farther station - just null every hour), which
    would otherwise silently turn the rare-hour campbell_norman fallback into the entire
    forecast without anyone noticing."""
    # When this runs alongside the DB-backed test suite, the Flask app fixture's own
    # logging setup can disable loggers that existed before it ran (the default behaviour
    # of logging.config.dictConfig) - re-enable ours explicitly so caplog can see it.
    logging.getLogger(brightsky.__name__).disabled = False
    records = [
        _record("2024-06-21T10:00:00+02:00", solar=None, cloud_cover=20),
        _record("2024-06-21T11:00:00+02:00", solar=None, cloud_cover=20),
        _record("2024-06-21T12:00:00+02:00", solar=None, cloud_cover=20),
    ]
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": records})
    )

    with caplog.at_level("WARNING", logger=brightsky.__name__):
        fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    assert "populated for only 0%" in caplog.text


def test_mostly_populated_solar_does_not_warn(monkeypatch, caplog):
    logging.getLogger(brightsky.__name__).disabled = False
    records = [
        _record(f"2024-06-21T{hour:02d}:00:00+02:00", solar=0.5)
        for hour in range(6, 20)
    ]
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": records})
    )

    with caplog.at_level("WARNING", logger=brightsky.__name__):
        fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    assert "populated for only" not in caplog.text


def test_temperature_and_wind_speed_are_converted(monkeypatch):
    records = [
        _record(
            "2024-06-21T10:00:00+02:00", solar=0.5, temperature=21.3, wind_speed=18.0
        )
    ]
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": records})
    )

    weather = fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    ten_am = pd.Timestamp("2024-06-21 10:00", tz=TIMEZONE)
    record = _at(weather, ten_am)
    assert record.temperature_c == pytest.approx(21.3)
    assert record.wind_speed_mps == pytest.approx(5.0)  # 18.0 kph -> 5 m/s


def test_http_error_propagates(monkeypatch):
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({}, status_code=500)
    )

    with pytest.raises(brightsky.requests.HTTPError):
        fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)


def test_empty_forecast_gives_an_empty_list(monkeypatch):
    monkeypatch.setattr(
        brightsky.requests, "get", lambda *a, **k: FakeResponse({"weather": []})
    )

    weather = fetch_brightsky_weather(LATITUDE, LONGITUDE, TIMEZONE)

    assert weather == []
