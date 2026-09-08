"""Turn an irradiance forecast into an expected PV power forecast. Pure pvlib, no
database and no Flask, so this module is unit-testable on its own.
"""

from __future__ import annotations

import pandas as pd
from pvlib.irradiance import erbs
from pvlib.location import Location
from pvlib.modelchain import ModelChain
from pvlib.pvsystem import PVSystem
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

# PVWatts default system losses (soiling, shading, snow, mismatch, wiring, connections,
# light-induced degradation, nameplate rating, availability). Inverter efficiency is *not*
# part of this - pvlib's PVWatts inverter model applies its own nominal efficiency.
DEFAULT_LOSSES = 0.14

# PVWatts' temperature coefficient of power for a generic crystalline silicon module,
# in fraction per degree Celsius.
GAMMA_PDC = -0.004

# Cell temperature models, keyed by how the array is mounted. Measured (5 kWp, south-facing,
# 30 degrees tilt, Utrecht, clear 21 June, 28°C air temp): open_rack (ground/pole-mounted,
# well-ventilated) runs coolest; close_mount (rack-mounted but tight to a roof, less airflow)
# runs about 5.8% lower daily energy; insulated_back (flush/BIPV, no airflow behind the
# module) runs about 8.5% lower. Those are worst-case, peak-production-hour figures for a hot
# clear day - the annual difference is smaller. pvlib itself has no default mount (PVSystem's
# `temperature_model_parameters` defaults to `None`; `open_rack_glass_glass` only appears as
# an example value in `ModelChain.with_pvwatts`'s docstring, not an enforced default). Default
# here is close_mount, since most arrays these specs describe are residential roof-mounted,
# not ground/pole-mounted; callers that know the mounting can pass `mount`.
MOUNT_TEMPERATURE_MODELS = {
    "open_rack": TEMPERATURE_MODEL_PARAMETERS["sapm"]["open_rack_glass_glass"],
    "close_mount": TEMPERATURE_MODEL_PARAMETERS["sapm"]["close_mount_glass_glass"],
    "insulated_back": TEMPERATURE_MODEL_PARAMETERS["sapm"][
        "insulated_back_glass_polymer"
    ],
}
DEFAULT_MOUNT = "close_mount"

# Fallbacks that pvlib's ModelChain also uses when weather data lacks these columns.
DEFAULT_TEMPERATURE_IN_C = 20.0
DEFAULT_WIND_SPEED_IN_MPS = 1.0


def decompose_ghi(ghi: pd.Series, latitude: float, longitude: float) -> pd.DataFrame:
    """Split global horizontal irradiance into its direct and diffuse components, using
    the Erbs model (estimates the split from GHI and solar zenith angle alone).

    Args:
        ghi: Global horizontal irradiance in W/m², indexed by a timezone-aware
            DatetimeIndex.
        latitude: Latitude of the array, in degrees.
        longitude: Longitude of the array, in degrees.

    Returns:
        DataFrame with columns 'ghi', 'dni' and 'dhi', on the input index.
    """
    index = _as_tz_aware_index(ghi)
    if len(index) == 0:
        return pd.DataFrame(columns=["ghi", "dni", "dhi"], index=index, dtype=float)

    solar_position = Location(latitude, longitude, tz=index.tz).get_solarposition(index)
    # Pass Series for both arguments, so that pvlib aligns them on the index rather than
    # on position.
    ghi_in_w_per_m2 = pd.Series(ghi.to_numpy(dtype=float), index=index)
    components = erbs(ghi_in_w_per_m2, solar_position["zenith"], index)

    return pd.DataFrame(
        {
            "ghi": ghi_in_w_per_m2,
            "dni": components["dni"],
            "dhi": components["dhi"],
        },
        index=index,
    )


def estimate_pv_power(
    ghi: pd.Series,
    latitude: float,
    longitude: float,
    tilt: float,
    azimuth: float,
    capacity: float,
    losses: float = DEFAULT_LOSSES,
    temperature: pd.Series | None = None,
    wind_speed: pd.Series | None = None,
    mount: str = DEFAULT_MOUNT,
) -> pd.Series:
    """Estimate the AC power an array produces, given an irradiance forecast.

    The pvlib PVWatts chain: decompose GHI, transpose onto the array plane, derate for
    cell temperature, run through a PVWatts inverter. PVWatts (not SAPM/CEC) because it
    needs only specs an installation owner actually knows. `losses` is folded into the DC
    nameplate rating and pvlib's own PVWatts loss stage is disabled
    (`losses_model="no_loss"`), so it isn't applied twice - see docs/pv-power-design.md.

    Args:
        ghi: Global horizontal irradiance in W/m², indexed by a timezone-aware
            DatetimeIndex.
        latitude: Latitude of the array, in degrees.
        longitude: Longitude of the array, in degrees.
        tilt: Tilt from horizontal, in degrees. 0 is flat, 90 is vertical.
        azimuth: Direction the array faces, in degrees clockwise from north. 180 is south.
        capacity: DC nameplate capacity of the array, in kW (kWp).
        losses: Fraction of DC output lost to soiling, shading, mismatch, wiring and the
            like, between 0 and 1. Defaults to the PVWatts default of 0.14.
        temperature: Optional ambient temperature in °C, on the same index as `ghi`.
            Improves the cell temperature estimate.
        wind_speed: Optional wind speed in m/s, on the same index as `ghi`. Also feeds
            the cell temperature estimate.
        mount: How the array is mounted, one of `MOUNT_TEMPERATURE_MODELS`
            ('open_rack', 'close_mount', 'insulated_back'). Affects how hot the cells run,
            and therefore output. Defaults to 'close_mount'.

    Returns:
        Expected AC power in W, on the input index. Zero at night.
    """
    weather = decompose_ghi(ghi, latitude, longitude)
    if len(weather) == 0:
        return pd.Series(dtype=float, index=weather.index, name="pv_power")

    return estimate_pv_power_from_components(
        weather["ghi"],
        weather["dni"],
        weather["dhi"],
        latitude=latitude,
        longitude=longitude,
        tilt=tilt,
        azimuth=azimuth,
        capacity=capacity,
        losses=losses,
        temperature=temperature,
        wind_speed=wind_speed,
        mount=mount,
    )


def estimate_pv_power_from_components(
    ghi: pd.Series,
    dni: pd.Series,
    dhi: pd.Series,
    latitude: float,
    longitude: float,
    tilt: float,
    azimuth: float,
    capacity: float,
    losses: float = DEFAULT_LOSSES,
    temperature: pd.Series | None = None,
    wind_speed: pd.Series | None = None,
    mount: str = DEFAULT_MOUNT,
) -> pd.Series:
    """Estimate AC power from an already-split ghi/dni/dhi forecast.

    The PVSystem/ModelChain half of `estimate_pv_power`, factored out for providers that
    already have their own direct/diffuse split (Open-Meteo) so they can skip the Erbs
    decomposition. See `estimate_pv_power` for the rest of the parameters and the
    loss-stage/temperature-model rationale.

    Args:
        ghi: Global horizontal irradiance in W/m², indexed by a timezone-aware
            DatetimeIndex.
        dni: Direct normal irradiance in W/m², on the same index.
        dhi: Diffuse horizontal irradiance in W/m², on the same index.

    Returns:
        Expected AC power in W, on the input index. Zero at night.
    """
    if not 0 <= losses < 1:
        raise ValueError("losses should lie in the interval [0, 1)")
    if capacity < 0:
        raise ValueError("capacity should not be negative")
    if mount not in MOUNT_TEMPERATURE_MODELS:
        raise ValueError(
            f"mount should be one of {sorted(MOUNT_TEMPERATURE_MODELS)}, got {mount!r}"
        )

    index = _as_tz_aware_index(ghi)
    if len(index) == 0:
        return pd.Series(dtype=float, index=index, name="pv_power")

    weather = pd.DataFrame(
        {
            "ghi": pd.Series(ghi.to_numpy(dtype=float), index=index),
            "dni": pd.Series(dni.to_numpy(dtype=float), index=index),
            "dhi": pd.Series(dhi.to_numpy(dtype=float), index=index),
        },
        index=index,
    )
    weather["temp_air"] = _aligned(temperature, index, DEFAULT_TEMPERATURE_IN_C)
    weather["wind_speed"] = _aligned(wind_speed, index, DEFAULT_WIND_SPEED_IN_MPS)

    # PVWatts expresses the array's rating in W of DC output at 1000 W/m² and 25 °C
    dc_capacity_in_w = capacity * 1000 * (1 - losses)
    system = PVSystem(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        module_parameters={"pdc0": dc_capacity_in_w, "gamma_pdc": GAMMA_PDC},
        inverter_parameters={"pdc0": dc_capacity_in_w},
        temperature_model_parameters=MOUNT_TEMPERATURE_MODELS[mount],
    )
    location = Location(latitude, longitude, tz=weather.index.tz)
    model_chain = ModelChain.with_pvwatts(system, location, losses_model="no_loss")
    model_chain.run_model(weather)

    power = model_chain.results.ac
    # The inverter model returns a small negative value for night-time standby losses,
    # which is not something we want to forecast onto a production sensor.
    power = power.clip(lower=0).fillna(0)
    power.name = "pv_power"
    return power


def _as_tz_aware_index(series: pd.Series) -> pd.DatetimeIndex:
    """Validate that the series is indexed by a timezone-aware DatetimeIndex."""
    index = series.index
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(
            "irradiance should be indexed by a DatetimeIndex, got"
            f" {type(index).__name__}"
        )
    if index.tz is None:
        raise ValueError(
            "irradiance should be indexed by a timezone-aware DatetimeIndex, so that"
            " solar positions can be computed"
        )
    return index


def _aligned(
    series: pd.Series | None, index: pd.DatetimeIndex, default: float
) -> pd.Series:
    """Reindex an optional weather series onto the irradiance index, filling gaps."""
    if series is None:
        return pd.Series(default, index=index)
    return (
        pd.Series(series.to_numpy(dtype=float), index=series.index)
        .reindex(index)
        .fillna(default)
    )
