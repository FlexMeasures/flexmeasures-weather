"""Average independent PV power forecasts from multiple providers into one estimate. See
docs/pv-power-design.md for why we average full PVWatts output rather than raw GHI.
"""

from __future__ import annotations

import pandas as pd


def blend_power(*power_series: pd.Series) -> pd.Series:
    """Average AC power forecasts, tolerant of providers with different horizons.

    `mean(axis=1)` skips NaN, so once a shorter-horizon provider's series runs out, the
    blend degrades gracefully to whichever providers still have data.

    Args:
        power_series: One or more AC power series (matching units), each indexed by a
            timezone-aware DatetimeIndex. Aligned by `pd.concat` if indices differ.

    Returns:
        The averaged series, named 'pv_power'.
    """
    if not power_series:
        raise ValueError("blend_power needs at least one power series")

    frame = pd.DataFrame({i: series for i, series in enumerate(power_series)})
    blended = frame.mean(axis=1)
    blended.name = "pv_power"
    return blended
