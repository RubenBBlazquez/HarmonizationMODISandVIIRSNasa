"""
Standardization of the raw FIRMS tables: from the CSV files read to common-schema detections.

Steps: normalization (:mod:`fire_etl.transform.firms.normalization`), discarding of invalid and
duplicated rows, FRP classification and flagging of what is not a vegetation fire
(:mod:`fire_etl.transform.firms.validation`). Reading the CSV files is the job of
:mod:`fire_etl.extract.firms.reader`.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.transform.firms.normalization import (
    DETECTION_COLUMNS,
    build_detection_ids,
    normalize_firms,
)
from fire_etl.transform.firms.validation import (
    BELOW_FRP_REASON,
    invalid_reasons,
    source_skip_reasons,
    summarize_discards,
)
from fire_etl.transform.processing.frp_classification import CATEGORY_DISCARDED, classify_frp

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class StandardizedDetections:
    """
    Standardized detections and summary of what was discarded.

    Attributes
    ----------
    detections : pd.DataFrame
        Valid, deduplicated detections in the common schema (``DETECTION_COLUMNS``).
    discarded : pd.DataFrame
        Discarded rows with columns ``source``, ``reason`` and ``count``.
    """

    detections: pd.DataFrame
    discarded: pd.DataFrame


def standardize_detections(
    raw_frames: Iterable[pd.DataFrame], config: PipelineConfig
) -> StandardizedDetections:
    """
    Normalizes, validates, deduplicates and classifies by FRP one or several raw FIRMS tables.

    Parameters
    ----------
    raw_frames : Iterable[pd.DataFrame]
        Raw FIRMS tables (MODIS and/or VIIRS, in any order).
    config : PipelineConfig
        Pipeline configuration.

    Returns
    -------
    StandardizedDetections
        Valid detections and summary of discards.
    """
    normalized = pd.concat(
        [normalize_firms(frame, config) for frame in raw_frames], ignore_index=True
    )
    reasons = invalid_reasons(normalized, config.validation)
    discards = [summarize_discards(normalized["source"], reasons)]

    valid = normalized[reasons.isna()].copy()
    valid["detection_id"] = build_detection_ids(valid)

    duplicated = valid["detection_id"].duplicated(keep="first")
    discards.append(summarize_discards(valid["source"], duplicated.map({True: "duplicate"})))
    valid = valid[~duplicated]

    valid = classify_frp(
        valid, instrument_col="source", thresholds=config.validation.frp_thresholds_mw
    )
    noise = valid["frp_category"] == CATEGORY_DISCARDED
    if not config.validation.apply_frp_thresholds:
        noise = pd.Series(False, index=valid.index)

    not_wildfire = pd.Series(None, index=valid.index, dtype="object")
    if config.validation.apply_source_filters:
        not_wildfire = source_skip_reasons(valid, config.validation)

    # They are not discarded: they are flagged and the following steps skip them
    # (is_valid / skip_reason).
    # If there are several reasons, the source one (volcano, industry, sea) wins over low FRP.
    frp_reason = pd.Series(
        np.where(noise, BELOW_FRP_REASON, None), index=valid.index, dtype="object"
    )
    skip_reason = not_wildfire.where(not_wildfire.notna(), frp_reason)
    valid["is_valid"] = skip_reason.isna()
    valid["skip_reason"] = skip_reason
    for reason, count in skip_reason.value_counts().items():
        LOGGER.info("Detections flagged as not valid (%s): %s", reason, count)

    discarded = pd.concat(discards, ignore_index=True)
    for row in discarded.itertuples(index=False):
        LOGGER.warning("Discarded %s %s rows: %s", row.count, row.source, row.reason)

    LOGGER.info("Standardized detections (valid or flagged): %s", len(valid))
    detections = valid.loc[:, list(DETECTION_COLUMNS)].sort_values(
        ["acq_datetime", "detection_id"], ignore_index=True
    )

    return StandardizedDetections(detections=detections, discarded=discarded)
