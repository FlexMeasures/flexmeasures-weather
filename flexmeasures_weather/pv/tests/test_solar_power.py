"""Tests for the pvlib PV power estimate.

These need neither a database nor Flask, so the module under test is loaded directly by
path: importing ``flexmeasures_weather`` would pull in the whole plugin (its ``__init__``
imports the CLI, which imports FlexMeasures).

Because of that, run them with ``--noconftest``, so that the plugin's DB-bound
``conftest.py`` is not imported either::

    pytest --noconftest flexmeasures_weather/pv/tests/test_solar_power.py

Clear-sky irradiance from pvlib itself drives the tests, so there is no fixture data.
Tolerances are a few percent, not exact: pvlib's clear-sky and transposition models can
shift between patch releases.
"""

import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from pvlib.location import Location


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PV_UTILS = Path(__file__).parent.parent / "utils"
_UTILS = Path(__file__).parent.parent.parent / "utils"
solar_power = _load_module_by_path("solar_power", _PV_UTILS / "solar_power.py")
radiating = _load_module_by_path("radiating", _UTILS / "radiating.py")

estimate_pv_power = solar_power.estimate_pv_power
estimate_pv_power_from_components = solar_power.estimate_pv_power_from_components
decompose_ghi = solar_power.decompose_ghi
ghi_clear_to_ghi = radiating.ghi_clear_to_ghi


# A rooftop array in Utrecht, the Netherlands
LATITUDE = 52.09
LONGITUDE = 5.12
TIMEZONE = "Europe/Amsterdam"
TILT = 30.0
AZIMUTH = 180.0  # south
CAPACITY_IN_KW = 5.0
LOSSES = 0.14


def clear_sky_ghi(day: str, freq: str = "h") -> pd.Series:
    """Clear-sky GHI for a whole day at the test location."""
    index = pd.date_range(
        f"{day} 00:00", f"{day} 23:00", freq=freq, tz=TIMEZONE, inclusive="both"
    )
    return Location(LATITUDE, LONGITUDE, tz=TIMEZONE).get_clearsky(index)["ghi"]


def power(ghi: pd.Series, **overrides) -> pd.Series:
    kwargs = dict(
        latitude=LATITUDE,
        longitude=LONGITUDE,
        tilt=TILT,
        azimuth=AZIMUTH,
        capacity=CAPACITY_IN_KW,
        losses=LOSSES,
    )
    kwargs.update(overrides)
    return estimate_pv_power(ghi, **kwargs)


def test_clear_midsummer_peak_is_a_realistic_fraction_of_nameplate():
    """A well-oriented array peaks at 70-75% of nameplate on a clear midsummer day.

    Pinned to `mount="open_rack"`: that's the configuration the 3601 W figure in
    docs/pv-power-design.md ("The PVWatts loss stage is deliberately disabled" table)
    was measured against, independent of which mount is the module's own default.
    """
    peak_in_w = power(clear_sky_ghi("2024-06-21"), mount="open_rack").max()

    assert peak_in_w == pytest.approx(3601, rel=0.05)
    fraction_of_nameplate = peak_in_w / (CAPACITY_IN_KW * 1000)
    assert 0.70 < fraction_of_nameplate < 0.75


def test_clear_midsummer_yield_is_a_realistic_number_of_kwh_per_kwp():
    """A clear Dutch midsummer day yields 5.5-6.5 kWh per kWp.

    Pinned to `mount="open_rack"`, see test_clear_midsummer_peak_is_a_realistic_fraction_of_nameplate.
    """
    # hourly samples of instantaneous power, so summing gives Wh directly
    daily_in_kwh = power(clear_sky_ghi("2024-06-21"), mount="open_rack").sum() / 1000

    assert daily_in_kwh == pytest.approx(30.6, rel=0.05)
    assert 5.5 < daily_in_kwh / CAPACITY_IN_KW < 6.5


def test_clear_midwinter_peak_is_well_below_the_midsummer_peak():
    december_peak = power(clear_sky_ghi("2024-12-21")).max()
    june_peak = power(clear_sky_ghi("2024-06-21")).max()

    assert december_peak == pytest.approx(1743, rel=0.05)
    assert december_peak < 0.6 * june_peak


def test_night_hours_are_exactly_zero():
    output = power(clear_sky_ghi("2024-06-21"))
    night = output[(output.index.hour < 4) | (output.index.hour > 23)]

    assert len(night) > 0
    assert (night == 0).all()


def test_overcast_sky_yields_a_small_fraction_of_the_clear_sky_peak():
    """Full cloud cover leaves about a tenth of the clear-sky peak."""
    ghi_clear = clear_sky_ghi("2024-06-21")
    ghi_overcast = ghi_clear.map(lambda ghi: ghi_clear_to_ghi(ghi, 1.0))

    ratio = power(ghi_overcast).max() / power(ghi_clear).max()

    assert ratio == pytest.approx(0.11, abs=0.03)


def test_losses_are_applied_exactly_once():
    """Regression test: pvlib's own PVWatts loss stage must stay disabled.

    ``ModelChain.with_pvwatts`` defaults to ``losses_model="pvwatts"``, which applies its
    own ~14% system derate. Combined with folding the caller's `losses` into the nameplate
    rating, that derated twice (~0.86 * 0.86) and made the `losses` argument mean something
    other than what it says.
    """
    ghi = clear_sky_ghi("2024-06-21")

    lossless_peak = power(ghi, losses=0).max()
    derated_peak = power(ghi, losses=0.14).max()

    assert derated_peak / lossless_peak == pytest.approx(0.86, rel=0.01)


def test_output_scales_linearly_with_capacity():
    ghi = clear_sky_ghi("2024-06-21")

    small = power(ghi, capacity=2.0)
    large = power(ghi, capacity=8.0)

    assert large.max() / small.max() == pytest.approx(4.0, rel=0.02)
    assert large.sum() / small.sum() == pytest.approx(4.0, rel=0.02)


def test_peak_moves_west_as_the_array_turns_west():
    """East-facing arrays peak in the morning, west-facing ones in the afternoon."""
    ghi = clear_sky_ghi("2024-06-21")

    peak_hours = [
        power(ghi, azimuth=azimuth).idxmax().hour
        for azimuth in (90, 135, 180, 225, 270)
    ]

    assert peak_hours[0] == 11
    assert peak_hours[-1] == 16
    assert peak_hours == sorted(peak_hours)


def test_east_and_west_are_mirror_images():
    """Equal deviations from south yield the same amount of energy."""
    ghi = clear_sky_ghi("2024-06-21")

    south_east = power(ghi, azimuth=135).sum()
    south_west = power(ghi, azimuth=225).sum()

    assert south_east == pytest.approx(south_west, rel=0.01)


def test_empty_input_gives_an_empty_float_series():
    empty = pd.Series(
        dtype=float, index=pd.DatetimeIndex([], tz=TIMEZONE, name="event_start")
    )

    output = power(empty)

    assert len(output) == 0
    assert output.dtype == float
    assert decompose_ghi(empty, LATITUDE, LONGITUDE).empty


def test_timezone_naive_index_is_rejected():
    ghi = clear_sky_ghi("2024-06-21")
    ghi.index = ghi.index.tz_localize(None)

    with pytest.raises(ValueError, match="timezone-aware"):
        power(ghi)


def test_non_datetime_index_is_rejected():
    ghi = pd.Series([100.0, 200.0], index=[0, 1])

    with pytest.raises(ValueError, match="DatetimeIndex"):
        power(ghi)


@pytest.mark.parametrize("losses", [1.0, 1.5, -0.1])
def test_out_of_range_losses_are_rejected(losses):
    with pytest.raises(ValueError, match="losses"):
        power(clear_sky_ghi("2024-06-21"), losses=losses)


def test_negative_capacity_is_rejected():
    with pytest.raises(ValueError, match="capacity"):
        power(clear_sky_ghi("2024-06-21"), capacity=-1)


def test_from_components_matches_the_ghi_only_path_on_a_clear_day():
    """Erbs-decomposed components fed straight into `estimate_pv_power_from_components`
    should reproduce `estimate_pv_power`'s own (internal) Erbs decomposition exactly."""
    ghi = clear_sky_ghi("2024-06-21")
    components = decompose_ghi(ghi, LATITUDE, LONGITUDE)

    via_components = estimate_pv_power_from_components(
        components["ghi"],
        components["dni"],
        components["dhi"],
        latitude=LATITUDE,
        longitude=LONGITUDE,
        tilt=TILT,
        azimuth=AZIMUTH,
        capacity=CAPACITY_IN_KW,
        losses=LOSSES,
    )
    via_ghi = power(ghi)

    pd.testing.assert_series_equal(via_components, via_ghi, check_names=False)


def test_from_components_uses_the_given_split_rather_than_re_deriving_it():
    """A provider that already gives its own dni/dhi (e.g. Open-Meteo, via
    complete_irradiance) should see that split reflected in the output, not Erbs's."""
    ghi = clear_sky_ghi("2024-06-21")
    erbs_components = decompose_ghi(ghi, LATITUDE, LONGITUDE)
    # A deliberately different (nonsensical) split: all diffuse, no direct light.
    all_diffuse = estimate_pv_power_from_components(
        erbs_components["ghi"],
        dni=pd.Series(0.0, index=erbs_components.index),
        dhi=erbs_components["ghi"],
        latitude=LATITUDE,
        longitude=LONGITUDE,
        tilt=TILT,
        azimuth=AZIMUTH,
        capacity=CAPACITY_IN_KW,
        losses=LOSSES,
    )

    assert all_diffuse.max() != pytest.approx(power(ghi).max())


def test_from_components_empty_input_gives_an_empty_float_series():
    empty = pd.Series(
        dtype=float, index=pd.DatetimeIndex([], tz=TIMEZONE, name="event_start")
    )

    output = estimate_pv_power_from_components(
        empty,
        empty,
        empty,
        latitude=LATITUDE,
        longitude=LONGITUDE,
        tilt=TILT,
        azimuth=AZIMUTH,
        capacity=CAPACITY_IN_KW,
    )

    assert len(output) == 0
    assert output.dtype == float


def test_ambient_temperature_lowers_output_when_it_is_hot():
    """Cell temperature derating: a hot day produces less than a cool one."""
    ghi = clear_sky_ghi("2024-06-21")
    cool = pd.Series(5.0, index=ghi.index)
    hot = pd.Series(35.0, index=ghi.index)

    assert power(ghi, temperature=hot).max() < power(ghi, temperature=cool).max()


def test_mount_defaults_to_close_mount():
    """An unspecified `mount` should reproduce an explicit 'close_mount' exactly."""
    ghi = clear_sky_ghi("2024-06-21")
    hot = pd.Series(28.0, index=ghi.index)

    pd.testing.assert_series_equal(
        power(ghi, temperature=hot),
        power(ghi, temperature=hot, mount="close_mount"),
    )


def test_a_roof_mount_runs_hotter_and_yields_less():
    """A close/insulated roof mount runs hotter than open rack, so yields less - a few
    percent for close_mount, up to ~8-9% for insulated_back on a hot clear day (see
    docs/pv-power-design.md)."""
    ghi = clear_sky_ghi("2024-06-21")
    hot = pd.Series(28.0, index=ghi.index)

    open_rack = power(ghi, temperature=hot, mount="open_rack").sum()
    close_mount = power(ghi, temperature=hot, mount="close_mount").sum()
    insulated_back = power(ghi, temperature=hot, mount="insulated_back").sum()

    assert 0 < close_mount < open_rack
    assert 0 < insulated_back < close_mount
    assert insulated_back / open_rack == pytest.approx(0.915, abs=0.02)


def test_an_unknown_mount_is_rejected():
    with pytest.raises(ValueError, match="mount"):
        power(clear_sky_ghi("2024-06-21"), mount="on_a_pole_in_a_lake")
