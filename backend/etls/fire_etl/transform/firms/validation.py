"""
Validation of normalized detections: invalidity reasons, source filters and discards.

Invalid rows (date, coordinates, FRP...) are discarded; those that are not a vegetation fire
(volcanoes, static sources, exclusion zones) are only flagged with ``skip_reason``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fire_etl.config import ExclusionZone, ValidationConfig
from fire_etl.transform.processing.frp_classification import CATEGORY_LOW

BELOW_FRP_REASON = "below_frp_threshold"


def _in_zone(detections: pd.DataFrame, zone: ExclusionZone) -> pd.Series:
    min_lon, min_lat, max_lon, max_lat = zone.bbox
    inside = detections["lon"].between(min_lon, max_lon) & detections["lat"].between(
        min_lat, max_lat
    )
    if zone.start is not None:
        inside &= detections["acq_date"] >= zone.start

    if zone.end is not None:
        inside &= detections["acq_date"] <= zone.end

    return inside


def source_skip_reasons(detections: pd.DataFrame, rules: ValidationConfig) -> pd.Series:
    """
    Reason why a detection is not a vegetation fire (``None`` if it is).

    The FIRMS ``type`` takes precedence (volcano, static source, sea), followed by the configured
    exclusion zones.

    Parameters
    ----------
    detections : pd.DataFrame
        Normalized detections, with ``firms_type``, ``lat``, ``lon`` and ``acq_date``.
    rules : ValidationConfig
        Types and exclusion zones.

    Returns
    -------
    pd.Series
        Reason per row (object, ``None`` if not flagged).
    """
    by_type = detections["firms_type"].map(rules.non_wildfire_types).astype("object")
    reasons = by_type.where(by_type.notna(), None)
    for zone in rules.exclusion_zones:
        free = reasons.isna() & _in_zone(detections, zone)
        reasons = reasons.mask(free, zone.reason)

    return reasons


def invalid_reasons(detections: pd.DataFrame, rules: ValidationConfig) -> pd.Series:
    """
    Returns the first invalidity reason of each row (``None`` if it is valid).

    Parameters
    ----------
    detections : pd.DataFrame
        Normalized detections.
    rules : ValidationConfig
        Valid ranges.

    Returns
    -------
    pd.Series
        Reason per row.
    """
    checks = {
        "missing_satellite": detections["satellite"].isna() | (detections["satellite"] == ""),
        "invalid_datetime": detections["acq_datetime"].isna(),
        "invalid_lat": ~detections["lat"].between(*rules.lat_range),
        "invalid_lon": ~detections["lon"].between(*rules.lon_range),
        "invalid_frp": ~(detections["frp"] >= rules.frp_min),
        "invalid_daynight": ~detections["daynight"].isin(rules.allowed_daynight).fillna(False),
        "invalid_confidence": detections["confidence_norm"].isna(),
    }
    conditions = [mask.fillna(True).to_numpy(dtype=bool) for mask in checks.values()]
    reasons = np.select(conditions, list(checks), default="")

    reasons = pd.Series(reasons, index=detections.index, dtype="object")

    return reasons.where(reasons != "", None)


def keep_forest_detections(
    detections: pd.DataFrame, rules: ValidationConfig
) -> tuple[pd.DataFrame, int]:
    """
    Rescues the forest detections that were flagged for FRP below the minimum.

    In forest the risk is high even if the hotspot is small: they become valid with category
    ``low``. Those in agriculture or other uses stay flagged. Needs ``is_forest`` (SIGPAC); without
    that column it does nothing.

    Parameters
    ----------
    detections : pd.DataFrame
        Detections with ``is_valid``, ``skip_reason`` and, if land cover is available,
        ``is_forest``.
    rules : ValidationConfig
        Validation rules (``keep_forest_below_frp``).

    Returns
    -------
    tuple[pd.DataFrame, int]
        Updated detections (copy) and how many were rescued.
    """
    if not rules.keep_forest_below_frp or "is_forest" not in detections:
        return detections, 0

    forest = detections["is_forest"].fillna(False).astype(bool)
    rescued = (detections["skip_reason"] == BELOW_FRP_REASON) & forest
    count = int(rescued.sum())
    if count == 0:
        return detections, 0

    updated = detections.copy()
    updated.loc[rescued, "is_valid"] = True
    updated.loc[rescued, "skip_reason"] = None
    updated.loc[rescued, "frp_category"] = CATEGORY_LOW
    updated.loc[rescued, "frp_class"] = 1

    return updated, count


def summarize_discards(sources: pd.Series, reasons: pd.Series) -> pd.DataFrame:
    """Groups the discards by sensor and reason."""
    discarded = pd.DataFrame({"source": sources, "reason": reasons}).dropna(subset=["reason"])
    if discarded.empty:
        return pd.DataFrame({"source": [], "reason": [], "count": []})

    return discarded.value_counts(["source", "reason"]).rename("count").reset_index()
