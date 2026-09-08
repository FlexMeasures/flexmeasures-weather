"""Tests for the power-level PV forecast blend.

No database and no Flask, so the module under test is loaded by path and these run with
``pytest --noconftest``. See the note at the top of ``test_solar_power.py``.
"""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


blend = _load_module_by_path(
    "blend", Path(__file__).parent.parent / "utils" / "blend.py"
)
blend_power = blend.blend_power

TIMEZONE = "Europe/Amsterdam"


def _series(values: list[float], periods: int, tz: str = TIMEZONE) -> pd.Series:
    index = pd.date_range("2024-06-21 08:00", periods=periods, freq="h", tz=tz)
    return pd.Series(values[:periods], index=index)


def test_blend_averages_two_equal_length_series():
    a = _series([100.0, 200.0, 300.0], periods=3)
    b = _series([300.0, 200.0, 100.0], periods=3)

    blended = blend_power(a, b)

    assert list(blended.to_numpy()) == pytest.approx([200.0, 200.0, 200.0])
    assert blended.name == "pv_power"


def test_blend_degrades_to_the_remaining_provider_past_a_shorter_horizon():
    """One provider (e.g. Bright Sky, ~10 days) runs out before the other (Open-Meteo,
    ~15 days). NaN-tolerant averaging should silently fall back to whoever is left,
    rather than producing NaN for the whole tail."""
    short = _series([100.0, 200.0], periods=2)
    long = _series([300.0, 400.0, 500.0, 600.0], periods=4)

    blended = blend_power(short, long)

    assert blended.to_numpy().tolist() == pytest.approx([200.0, 300.0, 500.0, 600.0])


def test_blend_of_a_single_series_is_that_series():
    a = _series([123.0, 456.0], periods=2)

    blended = blend_power(a)

    assert blended.to_numpy().tolist() == pytest.approx([123.0, 456.0])


def test_blend_needs_at_least_one_series():
    with pytest.raises(ValueError, match="at least one"):
        blend_power()


def test_blend_of_three_series_averages_all_of_them():
    a = _series([0.0, 300.0], periods=2)
    b = _series([300.0, 300.0], periods=2)
    c = _series([300.0, 300.0], periods=2)

    blended = blend_power(a, b, c)

    assert blended.to_numpy().tolist() == pytest.approx([200.0, 300.0])
