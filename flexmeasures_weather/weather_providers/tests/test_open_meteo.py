"""Tests for the Open-Meteo fetch.

No database and no Flask, so the module under test is loaded by path and these run with
``pytest --noconftest``. See the note at the top of ``test_solar_power.py``.

HTTP is mocked by monkeypatching ``requests.get`` with a fake response object, rather than
pulling in an HTTP-mocking library the rest of this plugin doesn't otherwise use.
"""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_PACKAGE_DIR = Path(__file__).parent.parent


def _load_weather_providers_module(name: str):
    """Load a ``weather_providers`` submodule by path, without importing
    ``flexmeasures_weather`` (which would pull in Flask/the CLI/DB - see the note at the
    top of ``test_solar_power.py``). ``weather_providers.open_meteo`` does ``from .base
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


open_meteo = _load_weather_providers_module("open_meteo")


def fetch_open_meteo_weather(*args, **kwargs):
    return open_meteo.OpenMeteoProvider(model=open_meteo.MODEL).fetch_hourly_forecast(
        *args, **kwargs
    )


def _times(records) -> list:
    return [r.time for r in records]


TIMEZONE = "Europe/Amsterdam"
LATITUDE = 52.09
LONGITUDE = 5.12


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise open_meteo.requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def _payload(times, ghi, dhi, temp=None, wind=None) -> dict:
    n = len(times)
    return {
        "timezone": TIMEZONE,
        "hourly": {
            "time": times,
            "shortwave_radiation": ghi,
            "diffuse_radiation": dhi,
            "temperature_2m": temp or [18.0] * n,
            "wind_speed_10m": wind or [10.0] * n,
        },
    }


def test_dni_is_backed_out_with_complete_irradiance(monkeypatch):
    """DNI must come from pvlib's complete_irradiance closure, not a manual
    (ghi - dhi) / cos(zenith) division (which spikes near sunrise/sunset)."""
    times = ["2024-06-21T10:00", "2024-06-21T11:00", "2024-06-21T12:00"]
    payload = _payload(times, ghi=[400.0, 600.0, 700.0], dhi=[100.0, 150.0, 120.0])
    monkeypatch.setattr(
        open_meteo.requests, "get", lambda *a, **k: FakeResponse(payload)
    )

    weather = fetch_open_meteo_weather(LATITUDE, LONGITUDE)

    from pvlib.irradiance import complete_irradiance
    from pvlib.location import Location

    site = Location(LATITUDE, LONGITUDE, tz=TIMEZONE)
    index = pd.DatetimeIndex(_times(weather))
    zenith = site.get_solarposition(index)["zenith"]
    ghi = pd.Series([r.ghi_wm2 for r in weather], index=index)
    dhi = pd.Series([r.dhi_wm2 for r in weather], index=index)
    expected_dni = complete_irradiance(zenith, ghi=ghi, dhi=dhi)["dni"]

    dni = [r.dni_wm2 for r in weather]
    assert dni == pytest.approx(expected_dni.to_numpy().tolist())
    # Sanity: for a positive GHI/DHI split at a reasonably high sun, DNI should be positive.
    assert all(value > 0 for value in dni)


def test_trailing_null_padding_past_the_real_horizon_is_dropped(monkeypatch):
    """Open-Meteo pads with null past a model's real horizon regardless of the requested
    forecast_days - the real horizon is whatever survives dropna()."""
    times = [
        "2024-06-21T10:00",
        "2024-06-21T11:00",
        "2024-06-21T12:00",
        "2024-06-21T13:00",
    ]
    payload = _payload(
        times, ghi=[400.0, 600.0, None, None], dhi=[100.0, 150.0, None, None]
    )
    monkeypatch.setattr(
        open_meteo.requests, "get", lambda *a, **k: FakeResponse(payload)
    )

    weather = fetch_open_meteo_weather(LATITUDE, LONGITUDE)

    assert len(weather) == 2
    assert max(_times(weather)) == pd.Timestamp("2024-06-21 11:00", tz=TIMEZONE)


def test_empty_response_gives_an_empty_list(monkeypatch):
    payload = _payload([], ghi=[], dhi=[])
    monkeypatch.setattr(
        open_meteo.requests, "get", lambda *a, **k: FakeResponse(payload)
    )

    weather = fetch_open_meteo_weather(LATITUDE, LONGITUDE)

    assert weather == []


def test_http_error_propagates(monkeypatch):
    monkeypatch.setattr(
        open_meteo.requests, "get", lambda *a, **k: FakeResponse({}, status_code=503)
    )

    with pytest.raises(open_meteo.requests.HTTPError):
        fetch_open_meteo_weather(LATITUDE, LONGITUDE)
