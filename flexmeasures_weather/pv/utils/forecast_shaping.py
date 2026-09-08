"""Shape a physical power estimate into the wide-horizon frame `data_to_bdf` expects: one
row per viewpoint, one column per forecast step. Shared by `PVWattsForecaster` and
`BlendedPVForecaster` rather than duplicated.
"""

from __future__ import annotations

from datetime import timedelta
from typing import cast

import pandas as pd


def read_asset_location(asset) -> tuple[float, float]:
    """The (latitude, longitude) pvlib needs, read off a `GenericAsset`."""
    if asset.latitude is None or asset.longitude is None:
        raise ValueError(
            f"Asset '{asset.name}' (ID {asset.id}) has no latitude and/or longitude."
            " PV power forecasts need them, to compute solar positions."
        )
    return asset.latitude, asset.longitude


def to_wide_horizon_frame(
    power: pd.Series,
    viewpoint_starts: list,
    belief_times: list,
    horizon_in_steps: int,
    resolution: timedelta,
) -> pd.DataFrame:
    """Shape a power series the way `data_to_bdf` expects.

    One row per viewpoint, one column per forecast step ('1h'...'Nh', named after steps of
    sensor resolution despite the 'h'), indexed by the last known event start plus belief
    time - `data_to_bdf` shifts each column forward by its step count. Timestamps must be
    timezone-naive UTC, since `data_to_bdf` localizes them.
    """
    rows = []
    for viewpoint_start, belief_time in zip(viewpoint_starts, belief_times):
        event_starts = pd.date_range(
            start=pd.Timestamp(viewpoint_start).tz_convert(power.index.tz),
            periods=horizon_in_steps,
            freq=resolution,
        )
        values = power.reindex(event_starts).to_numpy()
        row = {f"{step + 1}h": value for step, value in enumerate(values)}
        row["event_start"] = naive_utc(viewpoint_start - resolution)
        row["belief_time"] = naive_utc(belief_time)
        rows.append(row)

    return pd.DataFrame(rows).set_index(["event_start", "belief_time"])


def naive_utc(moment: pd.Timestamp) -> pd.Timestamp:
    return cast(pd.Timestamp, pd.Timestamp(moment).tz_convert("UTC").tz_localize(None))
