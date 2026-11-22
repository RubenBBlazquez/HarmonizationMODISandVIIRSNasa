"""
Grouping of detections into observations within each time window.

Two detections of the same window are connected if their footprints intersect; each connected
component of the resulting graph is an **observation**. The representative detection is chosen
after grouping, prioritizing VIIRS (375 m) over MODIS (1 km), without discarding the MODIS
detections of the group.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

from fire_etl.config import ConfidenceConfig, WindowMode
from fire_etl.extract.firms.reader import Source
from fire_etl.transform.processing.confidence import LEVEL_RANK, ConfidenceLevel, fuse_confidence
from fire_etl.transform.processing.graph import component_labels, self_join_pairs

EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
FIXED_WINDOW_LENGTH = {
    WindowMode.DAY_UTC: pd.Timedelta(days=1),
    WindowMode.HOURS_12: pd.Timedelta(hours=12),
}
# Lower value = higher priority as representative detection.
SOURCE_PRIORITY = {Source.VIIRS.value: 0, Source.MODIS.value: 1}
RANK_TO_LEVEL = {rank: level.value for level, rank in LEVEL_RANK.items()}
ID_LENGTH = 16
DAY_FLAG = "D"

OBSERVATION_COLUMNS = (
    "observation_id",
    "window_key",
    "window_index",
    "window_start",
    "window_end",
    "first_detection",
    "last_detection",
    "sensors",
    "n_detections",
    "n_modis",
    "n_viirs",
    "frp_total",
    "frp_max",
    "biomass_total_kg_s",
    "biomass_max_kg_s",
    "confidence_modis",
    "confidence_viirs",
    "confidence_fused",
    "is_isolated_low",
    "representative_detection_id",
    "representative_source",
    "footprint_area_m2",
    "hull_area_m2",
    "geom_centroid",
    "geom_hull",
)


@dataclass(frozen=True)
class ObservationResult:
    """
    Result of the grouping.

    Attributes
    ----------
    detections : gpd.GeoDataFrame
        Detections with window columns, ``observation_id`` and ``is_representative``.
    observations : gpd.GeoDataFrame
        One row per observation (``OBSERVATION_COLUMNS``), active geometry ``geom_hull``.
    """

    detections: gpd.GeoDataFrame
    observations: gpd.GeoDataFrame


def short_hash(*parts: str) -> str:
    """Deterministic truncated SHA-1 hash of the given parts."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    return digest[:ID_LENGTH]


def assign_time_windows(detections: pd.DataFrame, mode: WindowMode) -> pd.DataFrame:
    """
    Assigns each detection its time window.

    - ``day_utc`` / ``hours_12``: fixed windows aligned to the Unix epoch in UTC. The window
      index is absolute, so two windows are consecutive if their indices differ by 1.
    - ``date_daynight``: window = UTC ``acq_date`` + ``daynight``. Start and end are the first and
      last observed acquisitions. The index is also absolute: ``2 * day`` for ``N`` and
      ``2 * day + 1`` for ``D``, so that N(d), D(d) and N(d+1) are consecutive and gaps
      without data do not bring distant windows closer. Limitation: in Spain an ``N`` date groups
      early-morning passes (~01-03 UTC) and night passes (~19-22 UTC) of the same UTC day, so
      those of the night of ``d`` only link with N(d+1) through D(d).

    Parameters
    ----------
    detections : pd.DataFrame
        Detections with ``acq_datetime``, ``acq_date`` and ``daynight``.
    mode : WindowMode
        Partitioning mode.

    Returns
    -------
    pd.DataFrame
        Copy with ``window_key``, ``window_index``, ``window_start`` and ``window_end``.
    """
    windowed = detections.copy()
    times = windowed["acq_datetime"]

    if mode in FIXED_WINDOW_LENGTH:
        length = FIXED_WINDOW_LENGTH[mode]
        start = times.dt.floor(length)
        windowed["window_start"] = start
        windowed["window_end"] = start + length
        windowed["window_index"] = ((start - EPOCH) // length).astype(np.int64)
        windowed["window_key"] = f"{mode.value}:" + start.dt.strftime("%Y-%m-%dT%H:%MZ")

        return windowed

    key = windowed["acq_date"].astype(str) + ":" + windowed["daynight"].astype(str)
    grouped_times = times.groupby(key)
    windowed["window_start"] = grouped_times.transform("min")
    windowed["window_end"] = grouped_times.transform("max")
    day_number = (times.dt.floor("D") - EPOCH) // pd.Timedelta(days=1)
    is_day = (windowed["daynight"].astype(str) == DAY_FLAG).astype(np.int64)
    windowed["window_index"] = (day_number * 2 + is_day).astype(np.int64)
    windowed["window_key"] = f"{mode.value}:" + key

    return windowed


def label_observations(windowed: gpd.GeoDataFrame) -> np.ndarray:
    """
    Labels the observations: connected components of footprints that intersect in the same
    window. One ``sjoin`` is done per window (not a global one) so as not to generate pairs between
    detections of different windows, which would grow quadratically in long fires.

    Parameters
    ----------
    windowed : gpd.GeoDataFrame
        Detections with ``window_key`` and active geometry ``geom_footprint`` (metric CRS).

    Returns
    -------
    np.ndarray
        Observation label per row (positional).
    """
    footprints = windowed.geometry
    positions = pd.Series(np.arange(len(windowed)))
    lefts: list[np.ndarray] = []
    rights: list[np.ndarray] = []
    for _, window_positions in positions.groupby(windowed["window_key"].to_numpy()):
        selected = window_positions.to_numpy()
        left, right = self_join_pairs(footprints.iloc[selected], predicate="intersects")
        lefts.append(selected[left])
        rights.append(selected[right])

    if not lefts:
        return component_labels(0, np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64))

    return component_labels(len(windowed), np.concatenate(lefts), np.concatenate(rights))


def _sensor_levels(detections: pd.DataFrame) -> pd.DataFrame:
    ranks = detections["confidence_norm"].map(lambda level: LEVEL_RANK[ConfidenceLevel(level)])
    by_sensor = ranks.groupby([detections["_label"], detections["source"]]).max().unstack()
    levels = pd.DataFrame(index=by_sensor.index)
    sensor_columns = (
        (Source.MODIS, "confidence_modis"),
        (Source.VIIRS, "confidence_viirs"),
    )
    for source, column in sensor_columns:
        values = by_sensor[source.value] if source.value in by_sensor else pd.Series(dtype=float)
        levels[column] = values.reindex(by_sensor.index).map(RANK_TO_LEVEL)

    return levels.astype("object").where(levels.notna(), None)


def _representatives(detections: pd.DataFrame) -> pd.DataFrame:
    ordered = detections.assign(
        _priority=detections["source"].map(SOURCE_PRIORITY),
        _conf_rank=detections["confidence_norm"].map(
            lambda level: LEVEL_RANK[ConfidenceLevel(level)]
        ),
    ).sort_values(
        ["_label", "_priority", "_conf_rank", "frp", "detection_id"],
        ascending=[True, True, False, False, True],
    )
    first = ordered.drop_duplicates("_label", keep="first").set_index("_label")

    return pd.DataFrame(
        {
            "representative_detection_id": first["detection_id"],
            "representative_source": first["source"],
        }
    )


def _fused_columns(levels: pd.DataFrame, confidence: ConfidenceConfig) -> pd.DataFrame:
    outcomes = [
        fuse_confidence(modis, viirs, confidence.single_sensor_high_level)
        for modis, viirs in zip(levels["confidence_modis"], levels["confidence_viirs"])
    ]

    return pd.DataFrame(
        {
            "confidence_fused": [outcome.level.value for outcome in outcomes],
            "is_isolated_low": [outcome.is_isolated_low for outcome in outcomes],
        },
        index=levels.index,
    )


def _sensors_label(n_modis: pd.Series, n_viirs: pd.Series) -> pd.Series:
    modis = np.where(n_modis > 0, Source.MODIS.value, "")
    viirs = np.where(n_viirs > 0, Source.VIIRS.value, "")
    joined = pd.Series(modis, index=n_modis.index) + "," + pd.Series(viirs, index=n_viirs.index)

    return joined.str.strip(",")


def build_observations(
    windowed: gpd.GeoDataFrame, confidence: ConfidenceConfig
) -> ObservationResult:
    """
    Groups the detections into observations and computes their attributes.

    Parameters
    ----------
    windowed : gpd.GeoDataFrame
        Output of :func:`assign_time_windows` on the geolocated detections.
    confidence : ConfidenceConfig
        Confidence fusion rules.

    Returns
    -------
    ObservationResult
        Labeled detections and observations table.
    """
    detections = windowed.reset_index(drop=True).copy()
    detections["_label"] = label_observations(detections)
    detections["_is_modis"] = detections["source"] == Source.MODIS.value
    detections["_x"] = detections["geom_point"].x
    detections["_y"] = detections["geom_point"].y

    grouped = detections.groupby("_label", sort=True)
    observations = grouped.agg(
        window_key=("window_key", "first"),
        window_index=("window_index", "first"),
        window_start=("window_start", "first"),
        window_end=("window_end", "first"),
        first_detection=("acq_datetime", "min"),
        last_detection=("acq_datetime", "max"),
        n_detections=("detection_id", "size"),
        n_modis=("_is_modis", "sum"),
        frp_total=("frp", "sum"),
        frp_max=("frp", "max"),
        biomass_total_kg_s=("biomass_kg_s", "sum"),
        biomass_max_kg_s=("biomass_kg_s", "max"),
        _x=("_x", "mean"),
        _y=("_y", "mean"),
        _min_detection_id=("detection_id", "min"),
    )
    observations["n_viirs"] = observations["n_detections"] - observations["n_modis"]
    observations["sensors"] = _sensors_label(observations["n_modis"], observations["n_viirs"])
    observations["observation_id"] = [
        f"obs_{short_hash(key, detection_id)}"
        for key, detection_id in zip(observations["window_key"], observations["_min_detection_id"])
    ]

    levels = _sensor_levels(detections)
    observations = observations.join(levels).join(_fused_columns(levels, confidence))
    observations = observations.join(_representatives(detections))

    # Geometric operations are done on GeoSeries: a column extracted from a plain DataFrame
    # is a Series without ``.area``.
    union = detections[["_label", "geom_footprint"]].dissolve(by="_label").geometry
    hulls = union.convex_hull
    observations["footprint_area_m2"] = union.area
    observations["geom_hull"] = hulls
    observations["hull_area_m2"] = hulls.area
    observations["geom_centroid"] = gpd.GeoSeries(
        gpd.points_from_xy(observations["_x"], observations["_y"]),
        index=observations.index,
        crs=detections.crs,
    )

    observation_ids = observations["observation_id"]
    detections["observation_id"] = detections["_label"].map(observation_ids)
    detections["is_representative"] = detections["detection_id"].isin(
        observations["representative_detection_id"]
    )

    observations = gpd.GeoDataFrame(
        observations.loc[:, list(OBSERVATION_COLUMNS)].reset_index(drop=True),
        geometry="geom_hull",
        crs=detections.crs,
    )
    helper_columns = ["_label", "_is_modis", "_x", "_y"]

    return ObservationResult(
        detections=detections.drop(columns=helper_columns), observations=observations
    )
