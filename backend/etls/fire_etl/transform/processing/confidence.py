"""
Normalization and fusion of the confidence of MODIS and VIIRS detections.

The fused categories (``low``, ``medium``, ``high``, ``very_high``) are specific to this
algorithm and are **not** equivalent to the official FIRMS categories.

Complete matrix (rows: MODIS level of the observation; columns: VIIRS level; ``—`` = sensor
absent). ``*`` = isolated low (a single sensor), which is flagged and optionally discarded.

=============  ==========  ==========  ==========  ===========
MODIS \\ VIIRS  —           low         nominal     high
=============  ==========  ==========  ==========  ===========
—              (invalid)   low *       medium      medium [1]
low            low *       low         medium      medium
nominal        medium      medium      high        high
high           medium [1]  medium      high        very_high
=============  ==========  ==========  ==========  ===========

[1] Configurable with ``single_sensor_high_level`` (``medium`` by default: without
corroboration between sensors an observation never exceeds ``medium``).

Criteria:

- Corroboration between sensors (MODIS + VIIRS) is the only way to ``high`` / ``very_high``.
- If any coinciding sensor is ``low``, the observation stays at ``medium`` at most: one
  sensor backs it and the other sees it weakly.
- Two satellites of the same sensor (Aqua + Terra, S-NPP + NOAA-20) do not count as
  corroboration.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd


class ConfidenceLevel(StrEnum):
    """Normalized confidence of an individual detection."""

    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"


class FusedConfidence(StrEnum):
    """Fused confidence of an observation (categories specific to the algorithm)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


LEVEL_RANK = {ConfidenceLevel.LOW: 0, ConfidenceLevel.NOMINAL: 1, ConfidenceLevel.HIGH: 2}
FUSED_RANK = {level: rank for rank, level in enumerate(FusedConfidence)}


class MissingSensorConfidenceError(ValueError):
    """An attempt was made to fuse an observation without confidence from any sensor."""


@dataclass(frozen=True)
class FusionOutcome:
    """
    Result of fusing the confidence of an observation.

    Attributes
    ----------
    level : FusedConfidence
        Fused confidence.
    is_isolated_low : bool
        ``True`` if the observation has a single sensor and it is ``low``.
    """

    level: FusedConfidence
    is_isolated_low: bool


def fuse_confidence(
    modis: ConfidenceLevel | str | None,
    viirs: ConfidenceLevel | str | None,
    single_sensor_high_level: FusedConfidence | str = FusedConfidence.MEDIUM,
) -> FusionOutcome:
    """
    Computes the fused confidence of an observation (pure function).

    Parameters
    ----------
    modis : ConfidenceLevel | str | None
        MODIS level of the observation (maximum of its MODIS detections) or ``None`` if there is
        none.
    viirs : ConfidenceLevel | str | None
        VIIRS level of the observation or ``None`` if there is none.
    single_sensor_high_level : FusedConfidence | str
        Level assigned to a single sensor with ``high`` confidence (``medium`` or ``high``).

    Returns
    -------
    FusionOutcome
        Fused level and isolated-low flag.

    Raises
    ------
    MissingSensorConfidenceError
        If both sensors are ``None``.
    """
    modis_level = _as_level(modis)
    viirs_level = _as_level(viirs)
    single_high = FusedConfidence(single_sensor_high_level)

    if modis_level is None and viirs_level is None:
        raise MissingSensorConfidenceError("An observation needs at least one sensor")

    if modis_level is None or viirs_level is None:
        only = modis_level if modis_level is not None else viirs_level
        return _single_sensor(only, single_high)

    levels = {modis_level, viirs_level}

    if levels == {ConfidenceLevel.LOW}:
        return FusionOutcome(FusedConfidence.LOW, is_isolated_low=False)

    if ConfidenceLevel.LOW in levels:
        return FusionOutcome(FusedConfidence.MEDIUM, is_isolated_low=False)

    if levels == {ConfidenceLevel.HIGH}:
        return FusionOutcome(FusedConfidence.VERY_HIGH, is_isolated_low=False)

    return FusionOutcome(FusedConfidence.HIGH, is_isolated_low=False)


def _single_sensor(level: ConfidenceLevel, single_high: FusedConfidence) -> FusionOutcome:
    if level is ConfidenceLevel.LOW:
        return FusionOutcome(FusedConfidence.LOW, is_isolated_low=True)

    if level is ConfidenceLevel.NOMINAL:
        return FusionOutcome(FusedConfidence.MEDIUM, is_isolated_low=False)

    return FusionOutcome(single_high, is_isolated_low=False)


def _as_level(value: ConfidenceLevel | str | None) -> ConfidenceLevel | None:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None

    return ConfidenceLevel(value)


def max_level(levels: Iterable[str]) -> str | None:
    """
    Returns the highest level of a set of normalized levels.

    Parameters
    ----------
    levels : Iterable[str]
        ``low`` / ``nominal`` / ``high`` levels; nulls are ignored.

    Returns
    -------
    str | None
        Maximum level, or ``None`` if there are no levels.
    """
    present = [ConfidenceLevel(level) for level in levels if isinstance(level, str)]
    if not present:
        return None

    return str(max(present, key=LEVEL_RANK.__getitem__))


def max_fused(levels: Iterable[str]) -> str | None:
    """Returns the highest fused confidence (``None`` if there are no values)."""
    present = [FusedConfidence(level) for level in levels if isinstance(level, str)]
    if not present:
        return None

    return str(max(present, key=FUSED_RANK.__getitem__))


def normalize_modis_confidence(values: pd.Series, low_max: int, nominal_max: int) -> pd.Series:
    """
    Normalizes the numeric MODIS confidence (0-100) to low/nominal/high.

    Parameters
    ----------
    values : pd.Series
        Original values (text or numeric).
    low_max : int
        Inclusive maximum of the ``low`` level.
    nominal_max : int
        Inclusive maximum of the ``nominal`` level.

    Returns
    -------
    pd.Series
        Normalized levels; ``None`` if the value is not numeric or is outside 0-100.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.between(0, 100)
    levels = pd.cut(
        numeric,
        bins=[-np.inf, low_max, nominal_max, np.inf],
        labels=[level.value for level in ConfidenceLevel],
        right=True,
    ).astype("object")

    return levels.where(valid, None)


def normalize_viirs_confidence(values: pd.Series, mapping: dict[str, str]) -> pd.Series:
    """
    Normalizes the categorical VIIRS confidence (``l``/``n``/``h`` or full word).

    Parameters
    ----------
    values : pd.Series
        Original values.
    mapping : dict[str, str]
        Lowercase mapping between original value and level.

    Returns
    -------
    pd.Series
        Normalized levels; ``None`` if the value is not in ``mapping``.
    """
    normalized = values.astype("string").str.strip().str.lower().map(mapping)

    return normalized.astype("object").where(normalized.notna(), None)
