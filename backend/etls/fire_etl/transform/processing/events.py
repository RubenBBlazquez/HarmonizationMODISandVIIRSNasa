"""
Linking of observations across consecutive windows to form fire events.

Two observations are linked if their window indices differ by between 1 and
``linking.max_window_gap`` and their envelopes are less than ``linking.max_distance_m`` apart
(``sjoin`` with ``dwithin``; distance 0 is equivalent to intersecting). This resolves the
artificial cut at window edges (e.g. 23:50 and 00:10 UTC).

Stable identifiers: if an event contains detections that already belonged to previous events,
it keeps the oldest previous ``event_id`` and records the merge of the others in
``event_merges``. New events receive a deterministic id derived from their oldest detection,
so reprocessing the same data produces the same ids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import geopandas as gpd
import numpy as np
import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.transform.processing.confidence import max_fused, max_level
from fire_etl.transform.processing.energy import event_energy
from fire_etl.transform.processing.fusion import ObservationResult, short_hash
from fire_etl.transform.processing.graph import component_labels, self_join_pairs

NOT_OBSERVED = "not_observed"  # sensor that did not see the event (instead of null)

EVENT_COLUMNS = (
    "event_id",
    "first_detection",
    "last_detection",
    "status",
    "n_observations",
    "n_detections",
    "n_modis",
    "n_viirs",
    "sensors",
    "frp_max",
    "frp_total",
    "biomass_total_kg_s",
    "biomass_max_kg_s",
    "n_passes",
    "max_pass_gap_hours",
    "fre_mj",
    "biomass_burned_kg",
    "confidence_modis",
    "confidence_viirs",
    "confidence_fused",
    "hull_area_m2",
    "frp_series",
    "merged_event_ids",
    "param_window_mode",
    "param_footprint_buffer_m",
    "param_link_max_distance_m",
    "param_link_max_window_gap",
    "params",
    "geom_centroid",
    "geom_hull",
)
EVENT_DETECTION_COLUMNS = ("event_id", "detection_id", "observation_id")
MERGE_COLUMNS = ("merged_event_id", "kept_event_id", "merged_at")


@dataclass(frozen=True)
class PreviousState:
    """
    State of a previous run.

    Attributes
    ----------
    detections : pd.DataFrame
        Already standardized detections (common schema).
    event_detections : pd.DataFrame
        ``event_id`` - ``detection_id`` relation.
    events : pd.DataFrame
        At least ``event_id`` and ``first_detection``.
    merges : pd.DataFrame
        Recorded merges (``MERGE_COLUMNS``).
    """

    detections: pd.DataFrame
    event_detections: pd.DataFrame
    events: pd.DataFrame
    merges: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=MERGE_COLUMNS))


@dataclass(frozen=True)
class EventResult:
    """
    Events and relations.

    Attributes
    ----------
    events : gpd.GeoDataFrame
        One row per event (``EVENT_COLUMNS``), active geometry ``geom_hull``.
    event_detections : pd.DataFrame
        Event - detection - observation relation.
    observations : gpd.GeoDataFrame
        Observations with their ``event_id`` (null if excluded as isolated low).
    merges : pd.DataFrame
        History of merges (previous + new).
    """

    events: gpd.GeoDataFrame
    event_detections: pd.DataFrame
    observations: gpd.GeoDataFrame
    merges: pd.DataFrame


def link_observations(
    observations: gpd.GeoDataFrame, max_distance_m: float, max_window_gap: int
) -> np.ndarray:
    """
    Groups close observations of consecutive windows into components (events).

    Parameters
    ----------
    observations : gpd.GeoDataFrame
        Observations with ``window_index`` and active geometry in metric CRS.
    max_distance_m : float
        Maximum linking distance in meters.
    max_window_gap : int
        Maximum window index difference.

    Returns
    -------
    np.ndarray
        Event label per observation (positional).
    """
    left, right = self_join_pairs(observations.geometry, "dwithin", distance=max_distance_m)
    window_index = observations["window_index"].to_numpy()
    gap = np.abs(window_index[left] - window_index[right])
    consecutive = (gap >= 1) & (gap <= max_window_gap)

    return component_labels(len(observations), left[consecutive], right[consecutive])


def _prior_event_ids(
    components: pd.DataFrame, previous: PreviousState | None
) -> dict[int, list[str]]:
    if previous is None or previous.event_detections.empty:
        return {}

    linked = components.merge(
        previous.event_detections[["detection_id", "event_id"]], on="detection_id"
    )
    prior_first = previous.events.set_index("event_id")["first_detection"]
    linked["_prior_first"] = linked["event_id"].map(prior_first)
    linked = linked.drop_duplicates(["_component", "event_id"]).sort_values(
        ["_component", "_prior_first", "event_id"]
    )

    return linked.groupby("_component")["event_id"].agg(list).to_dict()


def assign_event_ids(
    components: pd.DataFrame, previous: PreviousState | None, merged_at: datetime
) -> tuple[dict[int, str], pd.DataFrame]:
    """
    Assigns stable ``event_id`` values to the components and detects merges of previous events.

    Components are traversed from oldest to newest. Each one keeps the oldest previous event of its
    detections that no other component has claimed; the rest of the previous events it contains are
    recorded as merged into it. If it inherits none, it receives ``evt_<hash(oldest detection)>``.

    Parameters
    ----------
    components : pd.DataFrame
        ``_component``, ``detection_id`` and ``acq_datetime`` of each linked detection.
    previous : PreviousState | None
        Previous state.
    merged_at : datetime
        Timestamp recorded in the merges.

    Returns
    -------
    tuple[dict[int, str], pd.DataFrame]
        Component -> ``event_id`` mapping and new merges (``MERGE_COLUMNS``).
    """
    prior_ids = _prior_event_ids(components, previous)
    reserved = set() if previous is None else set(previous.events["event_id"])
    earliest = (
        components.sort_values(["acq_datetime", "detection_id"])
        .drop_duplicates("_component", keep="first")
        .set_index("_component")["detection_id"]
    )

    claimed: set[str] = set()
    assigned: dict[int, str] = {}
    merges: list[tuple[str, str, datetime]] = []
    for component, detection_id in earliest.items():
        candidates = [event for event in prior_ids.get(component, []) if event not in claimed]
        if candidates:
            event_id = candidates[0]
            merges.extend((other, event_id, merged_at) for other in candidates[1:])
            claimed.update(candidates)
        else:
            event_id = _new_event_id(detection_id, reserved | claimed)
            claimed.add(event_id)

        assigned[component] = event_id

    return assigned, pd.DataFrame(merges, columns=list(MERGE_COLUMNS))


def _new_event_id(detection_id: str, taken: set[str]) -> str:
    event_id = f"evt_{short_hash(detection_id)}"
    suffix = 0
    while event_id in taken:
        suffix += 1
        event_id = f"evt_{short_hash(detection_id, str(suffix))}"

    return event_id


def _frp_series(observations: pd.DataFrame) -> pd.Series:
    ordered = observations.sort_values(["event_id", "window_start", "observation_id"])
    records = ordered.assign(
        window_start_iso=ordered["window_start"].map(lambda value: value.isoformat()),
    )

    return records.groupby("event_id").apply(
        lambda group: json.dumps(
            [
                {
                    "observation_id": row.observation_id,
                    "window_start": row.window_start_iso,
                    "n_detections": int(row.n_detections),
                    "frp_total": float(row.frp_total),
                    "frp_max": float(row.frp_max),
                    "biomass_total_kg_s": float(row.biomass_total_kg_s),
                }
                for row in group.itertuples(index=False)
            ]
        ),
        include_groups=False,
    )


def _event_status(last_detection: pd.Series, reference: pd.Timestamp, hours: float) -> pd.Series:
    inactive = (reference - last_detection) > pd.Timedelta(hours=hours)

    return pd.Series(np.where(inactive, "closed", "active"), index=last_detection.index)


def empty_events(crs: object) -> gpd.GeoDataFrame:
    """Empty events table with the ``EVENT_COLUMNS`` schema."""
    columns = {column: pd.Series(dtype="object") for column in EVENT_COLUMNS}
    columns["geom_centroid"] = gpd.GeoSeries([], crs=crs)
    columns["geom_hull"] = gpd.GeoSeries([], crs=crs)

    return gpd.GeoDataFrame(columns, geometry="geom_hull", crs=crs)


def summarize_events(
    observations: gpd.GeoDataFrame,
    detections: gpd.GeoDataFrame,
    merges: pd.DataFrame,
    config: PipelineConfig,
    reference: datetime,
) -> gpd.GeoDataFrame:
    """
    Computes the attributes of each event.

    Parameters
    ----------
    observations : gpd.GeoDataFrame
        Linked observations (with non-null ``event_id``).
    detections : gpd.GeoDataFrame
        Linked detections (with ``event_id``) and metric ``geom_point``.
    merges : pd.DataFrame
        History of merges, to list the ids absorbed by each event.
    config : PipelineConfig
        Configuration (recorded status and parameters).
    reference : datetime
        Reference instant to decide ``active`` / ``closed``.

    Returns
    -------
    gpd.GeoDataFrame
        Events (``EVENT_COLUMNS``) in the metric CRS.
    """
    if detections.empty:
        return empty_events(detections.crs)

    by_detection = detections.assign(
        _x=detections["geom_point"].x, _y=detections["geom_point"].y
    ).groupby("event_id")
    events = by_detection.agg(
        first_detection=("acq_datetime", "min"),
        last_detection=("acq_datetime", "max"),
        n_detections=("detection_id", "size"),
        frp_max=("frp", "max"),
        frp_total=("frp", "sum"),
        biomass_total_kg_s=("biomass_kg_s", "sum"),
        biomass_max_kg_s=("biomass_kg_s", "max"),
        _x=("_x", "mean"),
        _y=("_y", "mean"),
    )

    by_observation = observations.groupby("event_id")
    events["n_observations"] = by_observation.size()
    events["n_modis"] = by_observation["n_modis"].sum()
    events["n_viirs"] = by_observation["n_viirs"].sum()
    events["sensors"] = by_observation["sensors"].agg(
        lambda values: ",".join(sorted({s for value in values for s in value.split(",")}))
    )
    for column in ("confidence_modis", "confidence_viirs"):
        events[column] = by_observation[column].agg(max_level).fillna(NOT_OBSERVED)
    events["confidence_fused"] = by_observation["confidence_fused"].agg(max_fused)
    events["frp_series"] = _frp_series(observations)
    events = events.join(event_energy(detections, config.biomass))

    hulls = observations[["event_id", "geom_hull"]].dissolve(by="event_id").convex_hull
    events["geom_hull"] = hulls
    events["hull_area_m2"] = hulls.area
    events["geom_centroid"] = gpd.GeoSeries(
        gpd.points_from_xy(events["_x"], events["_y"]), index=events.index, crs=detections.crs
    )

    events["status"] = _event_status(
        events["last_detection"], pd.Timestamp(reference), config.events.inactive_after_hours
    )

    absorbed = {
        kept: sorted(merged) for kept, merged in merges.groupby("kept_event_id")["merged_event_id"]
    }
    events["merged_event_ids"] = [
        json.dumps(absorbed.get(event_id, [])) for event_id in events.index
    ]

    parameters = config.algorithm_parameters()
    events["param_window_mode"] = parameters["window_mode"]
    events["param_footprint_buffer_m"] = parameters["footprint_buffer_m"]
    events["param_link_max_distance_m"] = parameters["link_max_distance_m"]
    events["param_link_max_window_gap"] = parameters["link_max_window_gap"]
    events["params"] = json.dumps(parameters, sort_keys=True)

    events = events.reset_index()

    return gpd.GeoDataFrame(
        events.loc[:, list(EVENT_COLUMNS)], geometry="geom_hull", crs=detections.crs
    ).sort_values(["first_detection", "event_id"], ignore_index=True)


def build_events(
    result: ObservationResult, config: PipelineConfig, previous: PreviousState | None = None
) -> EventResult:
    """
    Links observations into events with stable ``event_id`` and computes their attributes.

    Parameters
    ----------
    result : ObservationResult
        Detections and observations from
        :func:`fire_etl.transform.processing.fusion.build_observations`.
    config : PipelineConfig
        Pipeline configuration.
    previous : PreviousState | None
        State of the previous run (to keep ids and record merges).

    Returns
    -------
    EventResult
        Events, event-detection relation, observations with ``event_id`` and merges.
    """
    observations = result.observations.copy()
    eligible = np.ones(len(observations), dtype=bool)
    if config.confidence.discard_isolated_low:
        eligible = ~observations["is_isolated_low"].to_numpy(dtype=bool)

    linked = observations[eligible].reset_index(drop=True)
    linked["_component"] = link_observations(
        linked, config.linking.max_distance_m, config.linking.max_window_gap
    )

    detections = result.detections.merge(
        linked[["observation_id", "_component"]], on="observation_id", how="inner"
    )
    # Single reference (status and merge date): also includes the unlinked detections.
    reference = config.events.reference_time or result.detections["acq_datetime"].max()
    component_ids, new_merges = assign_event_ids(
        detections[["_component", "detection_id", "acq_datetime"]], previous, reference
    )

    linked["event_id"] = linked["_component"].map(component_ids)
    detections["event_id"] = detections["_component"].map(component_ids)
    detections = gpd.GeoDataFrame(detections, geometry="geom_footprint", crs=result.detections.crs)

    previous_merges = None if previous is None else previous.merges
    merges = _combine_merges(previous_merges, new_merges)
    events = summarize_events(linked, detections, merges, config, reference)

    event_by_observation = linked.set_index("observation_id")["event_id"]
    observations["event_id"] = observations["observation_id"].map(event_by_observation)
    event_detections = detections.loc[:, list(EVENT_DETECTION_COLUMNS)].sort_values(
        ["event_id", "detection_id"], ignore_index=True
    )

    return EventResult(
        events=events,
        event_detections=pd.DataFrame(event_detections),
        observations=observations,
        merges=merges,
    )


def _combine_merges(previous: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (previous, new) if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame(columns=list(MERGE_COLUMNS))

    combined = pd.concat(frames, ignore_index=True)

    return combined.drop_duplicates(["merged_event_id", "kept_event_id"], keep="first")
