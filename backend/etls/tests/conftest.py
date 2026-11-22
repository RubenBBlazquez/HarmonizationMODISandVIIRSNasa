"""Fixtures and builders of synthetic FIRMS detections shared by the tests."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from fire_etl.config import PipelineConfig
from fire_etl.transform.firms.standardization import StandardizedDetections, standardize_detections

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
MODIS_SAMPLE = SAMPLE_DIR / "MODIS_C6_1_Europe_MCD14DL_NRT_2026258_sample.txt"
VIIRS_SAMPLE = SAMPLE_DIR / "J1_VIIRS_C2_Europe_VJ114IMGTDL_NRT_2026318_sample.txt"

# Reference point (Galicia) and approximate meters -> degrees conversion.
BASE_LAT = 42.5
BASE_LON = -7.2
METERS_PER_DEG_LAT = 111_320.0


def offset(north_m: float = 0.0, east_m: float = 0.0) -> tuple[float, float]:
    """Returns ``(lat, lon)`` shifted ``north_m`` / ``east_m`` meters from the base point."""
    lat = BASE_LAT + north_m / METERS_PER_DEG_LAT
    lon = BASE_LON + east_m / (METERS_PER_DEG_LAT * math.cos(math.radians(BASE_LAT)))

    return lat, lon


def modis_row(
    when: str,
    north_m: float = 0.0,
    east_m: float = 0.0,
    confidence: int | str = 70,
    scan: float = 1.0,
    track: float = 1.0,
    frp: float = 10.0,
    satellite: str = "A",
    daynight: str = "D",
) -> dict[str, str]:
    """MODIS row in FIRMS format; ``when`` is ``"YYYY-MM-DD HH:MM"`` UTC."""
    lat, lon = offset(north_m, east_m)
    acq_date, acq_time = when.split(" ")

    return {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "brightness": "320.0",
        "scan": str(scan),
        "track": str(track),
        "acq_date": acq_date,
        "acq_time": acq_time,
        "satellite": satellite,
        "confidence": str(confidence),
        "version": "6.1NRT",
        "bright_t31": "290.0",
        "frp": str(frp),
        "daynight": daynight,
    }


def viirs_row(
    when: str,
    north_m: float = 0.0,
    east_m: float = 0.0,
    confidence: str = "nominal",
    scan: float = 0.4,
    track: float = 0.4,
    frp: float = 5.0,
    satellite: str = "N20",
    daynight: str = "D",
) -> dict[str, str]:
    """VIIRS row in FIRMS format; ``when`` is ``"YYYY-MM-DD HH:MM"`` UTC."""
    lat, lon = offset(north_m, east_m)
    acq_date, acq_time = when.split(" ")

    return {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "bright_ti4": "330.0",
        "scan": str(scan),
        "track": str(track),
        "acq_date": acq_date,
        "acq_time": acq_time,
        "satellite": satellite,
        "confidence": confidence,
        "version": "2.0NRT",
        "bright_ti5": "280.0",
        "frp": str(frp),
        "daynight": daynight,
    }


def standardize_rows(
    config: PipelineConfig,
    modis: list[dict[str, str]] | None = None,
    viirs: list[dict[str, str]] | None = None,
) -> StandardizedDetections:
    """Standardizes synthetic MODIS and/or VIIRS rows."""
    frames = [pd.DataFrame(rows, dtype=str) for rows in (modis, viirs) if rows]

    return standardize_detections(frames, config)


@pytest.fixture
def config() -> PipelineConfig:
    """Default configuration without the FRP filter (synthetic rows use low FRP values)."""
    base = PipelineConfig()

    return replace(base, validation=replace(base.validation, apply_frp_thresholds=False))
