"""Categorization of fire radiative power (FRP, in MW) into low/medium/high classes."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from fire_etl.config import _default_frp_thresholds_mw

CATEGORY_LOW = "low"
CATEGORY_MEDIUM = "medium"
CATEGORY_HIGH = "high"
CATEGORY_DISCARDED = "discarded"
CATEGORY_UNKNOWN = "unknown"


def _normalize_instrument(
    series: pd.Series, thresholds: Mapping[str, Mapping[str, float]]
) -> pd.Series:
    """Normalizes variants ("viirs", "VIIRS_SNPP", "MODIS_NRT"...) to VIIRS/MODIS or NaN."""
    text = series.astype("string").str.strip().str.upper()
    result = pd.Series(np.nan, index=series.index, dtype="object")
    for name in thresholds:
        result[text.str.contains(name, regex=False).fillna(False).to_numpy(bool)] = name
    return result


def classify_frp(
    data: pd.DataFrame | Sequence[Mapping[str, Any]],
    instrument_col: str = "instrument",
    col_frp: str = "frp",
    col_daynight: str = "daynight",
    thresholds: Mapping[str, Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """
    Adds ``frp_class`` and ``frp_category`` to each detection according to its FRP.

    Rules (per instrument; the minimum depends on ``daynight``: "D" day, "N" night):

    - ``frp < minimum``: discarded (class 0).
    - ``minimum <= frp <= limit_low``: class 1, "low".
    - ``limit_low < frp <= limit_medium``: class 2, "medium".
    - ``frp > limit_medium``: class 3, "high".

    Rows with an unrecognized instrument/daynight, or null or negative FRP, receive the
    "unknown" category (null class) and a ``UserWarning`` is issued.

    Parameters
    ----------
    data : pandas.DataFrame or sequence of mapping
        Detections. The remaining columns are kept unchanged.
    instrument_col, col_frp, col_daynight : str
        Names of the input columns.
    thresholds : mapping, optional
        Thresholds per instrument; defaults to those of :class:`ValidationConfig`.

    Returns
    -------
    pandas.DataFrame
        Copy of the input with ``frp_class`` (``Int64``; 0 = discarded, null if unknown) and
        ``frp_category``.
    """
    thresholds = thresholds or _default_frp_thresholds_mw()
    df = pd.DataFrame(data).copy()

    instrument = _normalize_instrument(df[instrument_col], thresholds)
    daynight = df[col_daynight].astype("string").str.strip().str.upper()
    frp = pd.to_numeric(df[col_frp], errors="coerce")

    is_day = (daynight == "D").to_numpy(bool)
    is_night = (daynight == "N").to_numpy(bool)
    valid = instrument.notna().to_numpy(bool) & (is_day | is_night)
    valid &= (frp >= 0).to_numpy(bool)  # NaN and negatives are left out

    # Per-row thresholds (vectorized): NaN where the instrument is not recognized.
    def _threshold(key: str) -> np.ndarray:
        by_instrument = {name: limits[key] for name, limits in thresholds.items()}
        return instrument.map(by_instrument).astype(float).to_numpy()

    minimum = np.where(is_day, _threshold("min_day"), _threshold("min_night"))
    limit_low = _threshold("limit_low")
    limit_medium = _threshold("limit_medium")
    value = frp.to_numpy(float)

    # Comparisons against NaN give False; `valid` decides the final case.
    classes = np.select(
        [value < minimum, value <= limit_low, value <= limit_medium],
        [0, 1, 2],
        default=3,
    )
    classes = np.where(valid, classes, -1)
    categories = np.array(
        [CATEGORY_DISCARDED, CATEGORY_LOW, CATEGORY_MEDIUM, CATEGORY_HIGH], dtype=object
    )
    df["frp_category"] = np.where(valid, categories[np.clip(classes, 0, 3)], CATEGORY_UNKNOWN)
    df["frp_class"] = pd.array(np.where(classes >= 0, classes, 0), dtype="Int64")
    df.loc[classes < 0, "frp_class"] = pd.NA  # only unknown ones; discarded = 0

    n_invalid = int((~valid).sum())
    if n_invalid:
        warnings.warn(
            f"{n_invalid} row(s) with invalid instrument, daynight or FRP "
            f"-> category '{CATEGORY_UNKNOWN}'.",
            UserWarning,
            stacklevel=2,
        )
    return df
