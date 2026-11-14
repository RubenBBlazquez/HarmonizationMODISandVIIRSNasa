"""Tests de carga y validación de la configuración."""

from __future__ import annotations

from pathlib import Path

import pytest

from fire_etl.config import InvalidConfigError, PipelineConfig, config_from_dict, load_config

DEFAULT_YAML = Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def test_default_yaml_matches_dataclass_defaults():
    assert load_config(DEFAULT_YAML) == PipelineConfig()


def test_partial_override_keeps_other_defaults():
    config = config_from_dict(
        {"geometry": {"footprint_buffer_m": 250}, "windows": {"mode": "hours_12"}}
    )

    assert config.geometry.footprint_buffer_m == 250
    assert config.geometry.metric_crs == "EPSG:3035"
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
