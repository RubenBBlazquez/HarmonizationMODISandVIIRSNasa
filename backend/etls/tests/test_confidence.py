"""Tests of the complete fused confidence matrix and of the normalization."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.config import ConfidenceConfig
from fire_etl.transform.processing.confidence import (
    FusedConfidence,
    MissingSensorConfidenceError,
    fuse_confidence,
    max_fused,
    max_level,
    normalize_modis_confidence,
    normalize_viirs_confidence,
)

LOW, NOMINAL, HIGH = "low", "nominal", "high"

# (modis, viirs) -> (fused level, isolated low). None = sensor absent.
FULL_MATRIX = [
    (None, LOW, FusedConfidence.LOW, True),
    (None, NOMINAL, FusedConfidence.MEDIUM, False),
    (None, HIGH, FusedConfidence.MEDIUM, False),
    (LOW, None, FusedConfidence.LOW, True),
    (NOMINAL, None, FusedConfidence.MEDIUM, False),
    (HIGH, None, FusedConfidence.MEDIUM, False),
    (LOW, LOW, FusedConfidence.LOW, False),
    (LOW, NOMINAL, FusedConfidence.MEDIUM, False),
    (LOW, HIGH, FusedConfidence.MEDIUM, False),
    (NOMINAL, LOW, FusedConfidence.MEDIUM, False),
    (HIGH, LOW, FusedConfidence.MEDIUM, False),
    (NOMINAL, NOMINAL, FusedConfidence.HIGH, False),
    (NOMINAL, HIGH, FusedConfidence.HIGH, False),
    (HIGH, NOMINAL, FusedConfidence.HIGH, False),
    (HIGH, HIGH, FusedConfidence.VERY_HIGH, False),
]


@pytest.mark.parametrize(("modis", "viirs", "expected", "isolated"), FULL_MATRIX)
def test_fuse_confidence_full_matrix(modis, viirs, expected, isolated):
    outcome = fuse_confidence(modis, viirs)

    assert outcome.level is expected
    assert outcome.is_isolated_low is isolated


@pytest.mark.parametrize(("modis", "viirs"), [(HIGH, None), (None, HIGH)])
def test_fuse_confidence_single_sensor_high_configurable(modis, viirs):
    outcome = fuse_confidence(modis, viirs, single_sensor_high_level="high")

    assert outcome.level is FusedConfidence.HIGH


def test_fuse_confidence_without_sensors_raises():
    with pytest.raises(MissingSensorConfidenceError):
        fuse_confidence(None, None)


def test_normalize_modis_confidence_boundaries():
    cfg = ConfidenceConfig()
    raw = pd.Series(["0", "29", "30", "79", "80", "100", "101", "-1", "abc"])

    levels = normalize_modis_confidence(raw, cfg.modis_low_max, cfg.modis_nominal_max)

    assert levels.tolist() == [LOW, LOW, NOMINAL, NOMINAL, HIGH, HIGH, None, None, None]


def test_normalize_modis_confidence_custom_thresholds():
    levels = normalize_modis_confidence(pd.Series(["40", "60"]), low_max=50, nominal_max=55)

    assert levels.tolist() == [LOW, HIGH]


def test_normalize_viirs_confidence_accepts_codes_and_words():
    raw = pd.Series(["l", "n", "h", "low", "nominal", " High ", "x"])

    levels = normalize_viirs_confidence(raw, ConfidenceConfig().viirs_mapping)

    assert levels.tolist() == [LOW, NOMINAL, HIGH, LOW, NOMINAL, HIGH, None]


def test_max_helpers_ignore_missing_values():
    assert max_level([LOW, None, HIGH, NOMINAL]) == HIGH
    assert max_level([None]) is None
    assert max_fused(["medium", "very_high", "high"]) == "very_high"
