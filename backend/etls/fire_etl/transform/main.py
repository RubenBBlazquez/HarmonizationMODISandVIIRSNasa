"""
Transform: from the raw FIRMS tables to the final tables and the clean data.

Stages, one function each (see :func:`process_detections`):

1. standardization (normalization, validation and deduplication);
2. geometries and zone filter;
3. land cover (SIGPAC);
4. static thermal sources (industry);
5. forest detections with low FRP kept as valid;
6. observations and events;
7. final data (``clean_data``).

It writes nothing: writing is the job of :mod:`fire_etl.load.main`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.stage_log import log_stage
from fire_etl.transform.clean_data import CleanData, build_clean_data
from fire_etl.transform.firms.standardization import StandardizedDetections, standardize_detections
from fire_etl.transform.firms.validation import keep_forest_detections
from fire_etl.transform.processing.events import EventResult, PreviousState, build_events
from fire_etl.transform.processing.fusion import (
    ObservationResult,
    assign_time_windows,
    build_observations,
)
from fire_etl.transform.processing.geometry import DetectionGeometries, build_detection_geometries
from fire_etl.transform.sigpac_land_cover import enrich, log_summary
from fire_etl.transform.static_sources import (
    fetch_static_cells,
    flag_static_sources,
    log_flag_summary,
)
from fire_etl.transform.tables import PipelineTables

LOGGER = logging.getLogger(__name__)

SORT_COLUMNS = ["acq_datetime", "detection_id"]


class NoDetectionsError(ValueError):
    """No valid detections are left to process."""


@dataclass(frozen=True)
class TransformResult:
    """
    Result of the transformation.

    Attributes
    ----------
    tables : PipelineTables
        Complete tables in the metric CRS (pipeline state).
    clean : CleanData
        Final data for the map and the chatbot.
    """

    tables: PipelineTables
    clean: CleanData


# --- Public API -----------------------------------------------------------------------------


def transform(
    frames: Iterable[pd.DataFrame],
    config: PipelineConfig,
    previous: PreviousState | None = None,
) -> TransformResult:
    """
    Transforms raw FIRMS tables into the final tables and the clean data.

    Parameters
    ----------
    frames : Iterable[pd.DataFrame]
        Raw FIRMS tables (MODIS and/or VIIRS, in any order).
    config : PipelineConfig
        Pipeline configuration.
    previous : PreviousState | None
        Previous state to keep ``event_id`` values and deduplicate between runs.

    Returns
    -------
    TransformResult
        Complete tables and final data.

    Raises
    ------
    NoDetectionsError
        If no valid detections are left to process.
    """
    with log_stage("standardization and validation of the FIRMS tables"):
        standardized = standardize_detections(frames, config)

    tables = process_detections(standardized, config, previous)
    with log_stage("final data (clean_data)"):
        clean = build_clean_data(tables, config)

    return TransformResult(tables=tables, clean=clean)


def process_detections(
    standardized: StandardizedDetections,
    config: PipelineConfig,
    previous: PreviousState | None = None,
) -> PipelineTables:
    """
    Runs the complete processing on already standardized detections (no I/O).

    Parameters
    ----------
    standardized : StandardizedDetections
        Result of :func:`fire_etl.transform.firms.standardization.standardize_detections`.
    config : PipelineConfig
        Pipeline configuration.
    previous : PreviousState | None
        Previous state to keep ``event_id`` values and deduplicate between runs.

    Returns
    -------
    PipelineTables
        Final tables in the metric CRS.

    Raises
    ------
    NoDetectionsError
        If there are no valid detections inside the area of interest.
    """
    detections = merge_with_previous(standardized.detections, previous)
    geometries = _locate(detections, config)

    located = _add_land_cover(geometries.detections, config)
    located = _flag_static_sources(located, config)
    # After the static sources: a detection in an industry is not rescued even if it is forest.
    located = _keep_forest(located, config)

    is_valid = _valid_mask(located)
    observations, events = _build_events(located[is_valid], config, previous)

    all_detections = _all_detections(observations, located[~is_valid])
    discarded = _discarded(standardized, geometries)

    return PipelineTables(
        detections=all_detections,
        observations=events.observations,
        events=events.events,
        event_detections=events.event_detections,
        merges=events.merges,
        discarded=discarded,
        run_parameters=_run_parameters(config, geometries, all_detections, events, discarded),
    )


def merge_with_previous(new: pd.DataFrame, previous: PreviousState | None) -> pd.DataFrame:
    """
    Merges the new detections with those of previous runs, without duplicating ``detection_id``.

    Parameters
    ----------
    new : pd.DataFrame
        Detections of this run.
    previous : PreviousState | None
        Previous state.

    Returns
    -------
    pd.DataFrame
        Unique detections sorted by ``acq_datetime`` and ``detection_id``.
    """
    if previous is None or previous.detections.empty:
        return new

    combined = pd.concat([previous.detections, new], ignore_index=True)
    combined = combined.drop_duplicates("detection_id", keep="first")

    return combined.sort_values(SORT_COLUMNS, ignore_index=True)


# --- Stages ---------------------------------------------------------------------------------


def _locate(detections: pd.DataFrame, config: PipelineConfig) -> DetectionGeometries:
    """Builds the geometries and keeps the detections inside the area of interest."""
    with log_stage(f"geometries and zone filter ({len(detections):,} detections)"):
        geometries = build_detection_geometries(
            detections, config.geometry, config.area_of_interest
        )

    if geometries.detections.empty:
        raise NoDetectionsError("There are no valid detections inside the area of interest")

    return geometries


def _add_land_cover(located: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """Adds the SIGPAC land cover (database or local folder), if configured."""
    settings = config.land_cover
    database_url = config.database.url if settings.use_database else None
    if not database_url and not settings.sigpac_dir:
        LOGGER.warning(
            "Land cover disabled (land_cover.sigpac_dir not set): land_cover, "
            "land_cover_confidence, sigpac_use, is_agricultural and is_forest will be empty."
        )
        return located

    enriched, summary = enrich(
        located,
        Path(settings.sigpac_dir) if settings.sigpac_dir else None,
        database_url=database_url,
        sigpac_confidence=settings.sigpac_confidence,
        min_confidence=settings.min_confidence,
    )
    log_summary(summary)

    return enriched


def _flag_static_sources(located: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """Flags the detections in the static thermal sources of ``fire.static_sources``."""
    settings = config.static_sources
    if not settings.enabled or not config.database.url:
        return located

    cells = fetch_static_cells(config.database.url, settings)
    if not cells:
        return located

    flagged, summary = flag_static_sources(located, cells, settings)
    log_flag_summary(summary, settings)

    return flagged


def _keep_forest(located: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    """Keeps as valid the forest detections flagged only for low FRP."""
    kept, count = keep_forest_detections(located, config.validation)
    if count:
        LOGGER.info("Forest detections with low FRP kept as valid: %s", count)

    return kept


def _valid_mask(located: pd.DataFrame) -> pd.Series:
    """Mask of the valid detections; fails if there is none."""
    is_valid = located["is_valid"].astype(bool)
    if not is_valid.any():
        raise NoDetectionsError("No detection is valid (all flagged with skip_reason)")

    return is_valid


def _build_events(
    valid: pd.DataFrame, config: PipelineConfig, previous: PreviousState | None
) -> tuple[ObservationResult, EventResult]:
    """Groups the valid detections into observations and links them into events."""
    with log_stage(f"observations ({len(valid):,} valid detections)"):
        windowed = assign_time_windows(valid, config.windows.mode)
        observations = build_observations(windowed, config.confidence)

    with log_stage("events (linking between windows, energy and biomass)"):
        events = build_events(observations, config, previous)

    return observations, events


# --- Output tables --------------------------------------------------------------------------


def _all_detections(observations: ObservationResult, skipped: pd.DataFrame) -> pd.DataFrame:
    """Valid detections (with observation) plus the flagged ones, without observation or event."""
    skipped = skipped.assign(observation_id=None, is_representative=False)
    combined = pd.concat([observations.detections, skipped], ignore_index=True)

    return combined.sort_values(SORT_COLUMNS, ignore_index=True)


def _discarded(
    standardized: StandardizedDetections, geometries: DetectionGeometries
) -> pd.DataFrame:
    """Discard summary of the standardization plus the detections outside the area."""
    if not geometries.outside_aoi:
        return standardized.discarded

    outside = pd.DataFrame(
        {"source": ["ALL"], "reason": ["outside_aoi"], "count": [geometries.outside_aoi]}
    )

    return pd.concat([standardized.discarded, outside], ignore_index=True)


def _run_parameters(
    config: PipelineConfig,
    geometries: DetectionGeometries,
    all_detections: pd.DataFrame,
    events: EventResult,
    discarded: pd.DataFrame,
) -> dict[str, Any]:
    """Parameters and counts of the run, stored next to the tables."""
    skipped = int((~all_detections["is_valid"].astype(bool)).sum())

    return {
        "parameters": config.algorithm_parameters(),
        "config": config.to_dict(),
        "metric_crs_resolved": geometries.metric_crs.to_string(),
        "counts": {
            "detections": len(all_detections),
            "skipped_detections": skipped,
            "observations": len(events.observations),
            "fire_events": len(events.events),
            "event_merges": len(events.merges),
            "discarded": int(discarded["count"].sum()) if not discarded.empty else 0,
        },
    }
