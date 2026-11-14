"""Tests de carga y validación de la configuración."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from shapely.geometry import Point

from fire_etl.config import (
    AreaOfInterestConfig,
    InvalidConfigError,
    PipelineConfig,
    config_from_dict,
    load_config,
)
from fire_etl.geometry import area_of_interest_geometry

DEFAULT_YAML = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


def test_default_yaml_matches_dataclass_defaults_except_spain_area():
    loaded = load_config(DEFAULT_YAML)

    assert loaded.area_of_interest.is_defined
    assert replace(loaded, area_of_interest=AreaOfInterestConfig()) == PipelineConfig()


@pytest.mark.parametrize(
    ("lat", "lon", "inside"),
    [
        (40.42, -3.70, True),  # Madrid
        (39.57, 2.65, True),  # Palma
        (28.46, -16.25, True),  # Santa Cruz de Tenerife
        (35.29, -2.94, True),  # Melilla
        (48.86, 2.35, False),  # París
        (51.50, -0.12, False),  # Londres
        (41.90, 12.50, False),  # Roma
        (34.02, -6.84, False),  # Rabat
    ],
)
def test_default_area_of_interest_covers_spain_only(lat, lon, inside):
    area = area_of_interest_geometry(load_config(DEFAULT_YAML).area_of_interest)

    assert area.intersects(Point(lon, lat)) is inside


def test_partial_override_keeps_other_defaults():
    config = config_from_dict(
        {"geometry": {"footprint_buffer_m": 250}, "windows": {"mode": "hours_12"}}
    )

    assert config.geometry.footprint_buffer_m == 250
    assert config.geometry.metric_crs == "EPSG:25830"
    assert config.windows.mode == "hours_12"


@pytest.mark.parametrize(
    "raw",
    [
        {"unknown": {}},
        {"geometry": {"buffer": 1}},
        {"windows": {"mode": "weekly"}},
        {"confidence": {"modis_low_max": 90, "modis_nominal_max": 80}},
        {"confidence": {"single_sensor_high_level": "very_high"}},
        {"area_of_interest": {"bbox": [0, 0, 1, 1], "polygon_wkt": "POINT (0 0)"}},
        {"events": {"reference_time": "2026-09-15T00:00:00"}},
    ],
)
def test_invalid_config_is_rejected(raw):
    with pytest.raises((InvalidConfigError, ValueError)):
        config_from_dict(raw)
