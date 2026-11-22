"""
Transform: from the raw FIRMS tables to the final tables and the clean data.

Stages: standardization (normalization, validation and deduplication) -> geometries and zone
filter -> land cover (SIGPAC) -> observations -> events -> final data (``clean_data``).
It writes nothing: writing is the job of :mod:`fire_etl.load.main`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.stage_log import log_stage
from fire_etl.transform.clean_data import CleanData, build_clean_data
from fire_etl.transform.firms.standardization import StandardizedDetections, standardize_detections
from fire_etl.transform.firms.validation import keep_forest_detections
from fire_etl.transform.processing.events import PreviousState, build_events
from fire_etl.transform.processing.fusion import assign_time_windows, build_observations
from fire_etl.transform.processing.geometry import build_detection_geometries
from fire_etl.transform.sigpac_land_cover import enrich, log_summary
from fire_etl.transform.tables import PipelineTables

LOGGER = logging.getLogger(__name__)


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

    return combined.sort_values(["acq_datetime", "detection_id"], ignore_index=True)


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
    with log_stage(f"geometries and zone filter ({len(detections):,} detections)"):
        geometries = build_detection_geometries(
            detections, config.geometry, config.area_of_interest
        )

    if geometries.detections.empty:
        raise NoDetectionsError("There are no valid detections inside the area of interest")

    located = geometries.detections
    land_cover_cfg = config.land_cover
    database_url = config.database.url if land_cover_cfg.use_database else None
    if database_url or land_cover_cfg.sigpac_dir:
        located, land_cover_summary = enrich(
            located,
            Path(land_cover_cfg.sigpac_dir) if land_cover_cfg.sigpac_dir else None,
            database_url=database_url,
            sigpac_confidence=config.land_cover.sigpac_confidence,
            min_confidence=config.land_cover.min_confidence,
        )
        log_summary(land_cover_summary)
    else:
        LOGGER.warning(
            "Land cover disabled (land_cover.sigpac_dir not set): land_cover, "
            "land_cover_confidence, sigpac_use, is_agricultural and is_forest will be empty."
        )

    located, forest_kept = keep_forest_detections(located, config.validation)
    if forest_kept:
        LOGGER.info("Forest detections with low FRP kept as valid: %s", forest_kept)

    is_valid = located["is_valid"].astype(bool)
    if not is_valid.any():
        raise NoDetectionsError("No detection is valid (all flagged with skip_reason)")

    with log_stage(f"observations ({int(is_valid.sum()):,} valid detections)"):
        windowed = assign_time_windows(located[is_valid], config.windows.mode)
        observations = build_observations(windowed, config.confidence)

    with log_stage("events (linking between windows, energy and biomass)"):
        events = build_events(observations, config, previous)

    # The invalid ones are kept in the detections table, without observation or event.
    skipped = located[~is_valid].assign(observation_id=None, is_representative=False)
    all_detections = pd.concat([observations.detections, skipped], ignore_index=True)
    all_detections = all_detections.sort_values(
        ["acq_datetime", "detection_id"], ignore_index=True
    )

    discarded = standardized.discarded
    if geometries.outside_aoi:
        outside = pd.DataFrame(
            {"source": ["ALL"], "reason": ["outside_aoi"], "count": [geometries.outside_aoi]}
        )
        discarded = pd.concat([discarded, outside], ignore_index=True)

    run_parameters = {
        "parameters": config.algorithm_parameters(),
        "config": config.to_dict(),
        "metric_crs_resolved": geometries.metric_crs.to_string(),
        "counts": {
            "detections": len(all_detections),
            "skipped_detections": int((~is_valid).sum()),
            "observations": len(events.observations),
            "fire_events": len(events.events),
            "event_merges": len(events.merges),
            "discarded": int(discarded["count"].sum()) if not discarded.empty else 0,
        },
    }

    return PipelineTables(
        detections=all_detections,
        observations=events.observations,
        events=events.events,
        event_detections=events.event_detections,
        merges=events.merges,
        discarded=discarded,
        run_parameters=run_parameters,
    )


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
