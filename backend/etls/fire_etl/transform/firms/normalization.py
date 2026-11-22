"""
Normalization of raw FIRMS CSV files (MODIS and VIIRS) into a common detection schema.

Converts types, dates, coordinates and confidence, and generates a deterministic
``detection_id``. It neither validates nor deduplicates: that is done by
:mod:`fire_etl.transform.firms.validation` and
:mod:`fire_etl.transform.firms.standardization`.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.extract.firms.reader import Source, detect_source
from fire_etl.transform.processing.confidence import (
    normalize_modis_confidence,
    normalize_viirs_confidence,
)

# Columns specific to each sensor -> columns of the common schema.
SENSOR_COLUMNS = {
    "MODIS": {"brightness": "brightness", "bright_t31": "brightness_2"},
    "VIIRS": {"bright_ti4": "brightness", "bright_ti5": "brightness_2"},
}

DETECTION_COLUMNS = (
    "detection_id",
    "source",
    "satellite",
    "acq_datetime",
    "acq_date",
    "lat",
    "lon",
    "area_width_km",
    "area_height_km",
    "frp",
    "biomass_kg_s",
    "frp_class",
    "frp_category",
    "is_valid",
    "skip_reason",
    "daynight",
    "confidence_raw",
    "confidence_norm",
    "brightness",
    "brightness_2",
    "version",
)

COORDINATE_DECIMALS = 5


def parse_acq_datetime(acq_date: pd.Series, acq_time: pd.Series) -> pd.Series:
    """
    Combines ``acq_date`` and ``acq_time`` into a tz-aware UTC datetime.

    Accepts ``acq_time`` as ``"HH:MM"`` (NRT files) or as an integer ``HHMM`` without leading
    zeros (FIRMS API, e.g. ``304`` = 03:04).

    Parameters
    ----------
    acq_date : pd.Series
        ``YYYY-MM-DD`` dates.
    acq_time : pd.Series
        UTC times.

    Returns
    -------
    pd.Series
        ``datetime64[ns, UTC]``; ``NaT`` if it cannot be interpreted.
    """
    raw_time = acq_time.astype("string").str.strip().str.replace(":", "", regex=False)
    is_hhmm = raw_time.str.fullmatch(r"\d{1,4}").fillna(False).astype(bool)
    hhmm = raw_time.where(is_hhmm, None).str.zfill(4)
    combined = acq_date.astype("string").str.strip() + " " + hhmm

    return pd.to_datetime(combined, format="%Y-%m-%d %H%M", errors="coerce", utc=True)


def build_detection_ids(detections: pd.DataFrame) -> pd.Series:
    """
    Generates a deterministic ``detection_id`` (SHA-1) per detection.

    The key is ``source|satellite|acq_datetime ISO|lat|lon`` with coordinates rounded to
    ``COORDINATE_DECIMALS`` decimals, so that re-ingesting the same file produces the same
    identifiers.

    Parameters
    ----------
    detections : pd.DataFrame
        Detections with ``source``, ``satellite``, ``acq_datetime``, ``lat`` and ``lon``.

    Returns
    -------
    pd.Series
        40-character hexadecimal identifiers.
    """
    keys = (
        detections["source"].astype(str)
        + "|"
        + detections["satellite"].astype(str)
        + "|"
        + detections["acq_datetime"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        + "|"
        + detections["lat"].map(lambda value: f"{value:.{COORDINATE_DECIMALS}f}")
        + "|"
        + detections["lon"].map(lambda value: f"{value:.{COORDINATE_DECIMALS}f}")
    )

    return keys.map(lambda key: hashlib.sha1(key.encode("utf-8")).hexdigest())


def normalize_firms(raw: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """
    Converts a raw FIRMS CSV to the common schema (without validating or deduplicating).

    Parameters
    ----------
    raw : pd.DataFrame
        Table as returned by :func:`fire_etl.extract.firms.reader.read_firms_csv`.
    config : PipelineConfig
        Configuration (confidence thresholds and biomass coefficient).

    Returns
    -------
    pd.DataFrame
        ``DETECTION_COLUMNS`` columns except ``detection_id``; values that cannot be interpreted
        are left as nulls so that validation discards them.
    """
    raw = raw.rename(columns=lambda column: column.strip().lower())
    source = detect_source(raw.columns)
    confidence_cfg = config.confidence

    normalized = pd.DataFrame(index=raw.index)
    normalized["source"] = source.value
    satellite = raw["satellite"].astype("string").str.strip()
    normalized["satellite"] = satellite.replace(config.validation.satellite_aliases)
    normalized["acq_datetime"] = parse_acq_datetime(raw["acq_date"], raw["acq_time"])
    normalized["acq_date"] = normalized["acq_datetime"].dt.date
    normalized["lat"] = pd.to_numeric(raw["latitude"], errors="coerce")
    normalized["lon"] = pd.to_numeric(raw["longitude"], errors="coerce")
    # The detection area is stored as generic width and height in km (not depending on the
    # sensor). Today they come from the nominal pixel size of each sensor
    # (``geometry.pixel_size_km``); FIRMS ``scan`` and ``track`` are not used.
    pixel_size_km = config.geometry.pixel_size_km[source.value]
    normalized["area_width_km"] = pixel_size_km
    normalized["area_height_km"] = pixel_size_km
    normalized["frp"] = pd.to_numeric(raw["frp"], errors="coerce")
    normalized["biomass_kg_s"] = normalized["frp"] * config.biomass.coefficient_kg_per_mj
    normalized["daynight"] = raw["daynight"].astype("string").str.strip().str.upper()
    normalized["confidence_raw"] = raw["confidence"].astype("string").str.strip()

    if source is Source.MODIS:
        normalized["confidence_norm"] = normalize_modis_confidence(
            raw["confidence"], confidence_cfg.modis_low_max, confidence_cfg.modis_nominal_max
        )
    else:
        normalized["confidence_norm"] = normalize_viirs_confidence(
            raw["confidence"], confidence_cfg.viirs_mapping
        )

    for raw_column, common_column in SENSOR_COLUMNS[source.value].items():
        values = raw[raw_column] if raw_column in raw else pd.Series(np.nan, index=raw.index)
        normalized[common_column] = pd.to_numeric(values, errors="coerce")

    version = raw["version"] if "version" in raw else pd.Series(pd.NA, index=raw.index)
    normalized["version"] = version.astype("string")
    # ``type`` only comes in the annual files (not in the NRT ones): it is used to flag, it is
    # not stored.
    firms_type = raw["type"] if "type" in raw else pd.Series(np.nan, index=raw.index)
    normalized["firms_type"] = pd.to_numeric(firms_type, errors="coerce")

    return normalized
