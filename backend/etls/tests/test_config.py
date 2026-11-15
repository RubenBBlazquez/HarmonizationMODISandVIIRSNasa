"""Tests de carga y validación de la configuración."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from shapely.geometry import Point

from fire_etl.config import (
    AreaOfInterestConfig,
    DatabaseConfig,
    DownloadConfig,
    InvalidConfigError,
    LandCoverConfig,
    PipelineConfig,
    config_from_dict,
    load_config,
)
from fire_etl.geometry import area_of_interest_geometry

DEFAULT_YAML = Path(__file__).resolve().parents[1] / "config" / "default.yaml"


def test_default_yaml_matches_dataclass_defaults_except_spain_area():
    loaded = load_config(DEFAULT_YAML)

    assert loaded.area_of_interest.is_defined
    no_zones = replace(loaded.validation, exclusion_zones=())
    neutral = replace(
        loaded,
        area_of_interest=AreaOfInterestConfig(),
        database=DatabaseConfig(),
        land_cover=LandCoverConfig(),
        validation=no_zones,
    )
    assert loaded.download == DownloadConfig()

    assert loaded.database.enabled
    assert neutral == PipelineConfig()


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


def test_default_yaml_defines_the_la_palma_eruption_exclusion_zone():
    zones = load_config(DEFAULT_YAML).validation.exclusion_zones

    assert [zone.name for zone in zones] == ["erupcion_la_palma_2021"]
    zone = zones[0]
    assert zone.reason == "volcanic_eruption"
    assert (zone.start, zone.end) == (date(2021, 9, 19), date(2021, 12, 31))
    # El volcán (28,617 N, 17,889 O) queda dentro; el incendio forestal de La Palma de 2023
    # (28,8 N) y la península quedan fuera.
    min_lon, min_lat, max_lon, max_lat = zone.bbox
    assert min_lon <= -17.889 <= max_lon and min_lat <= 28.617 <= max_lat
    assert not (min_lat <= 28.8 <= max_lat)


def test_default_yaml_configures_conservative_frp_noise_thresholds():
    thresholds = load_config(DEFAULT_YAML).validation.frp_thresholds_mw

    assert thresholds["VIIRS"] == {
        "min_day": 3.7,
        "min_night": 0.7,
        "limit_low": 35.0,
        "limit_medium": 70.0,
    }
    assert thresholds["MODIS"]["min_day"] == 5.7


def test_exclusion_zones_are_parsed_from_plain_dicts():
    config = config_from_dict(
        {
            "validation": {
                "exclusion_zones": [
                    {"name": "z", "bbox": [0, 0, 1, 1], "start": "2020-01-01", "reason": "x"}
                ]
            }
        }
    )

    zone = config.validation.exclusion_zones[0]
    assert zone.bbox == (0.0, 0.0, 1.0, 1.0) and zone.start == date(2020, 1, 1)


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
        {"validation": {"frp_thresholds_mw": {"VIIRS": {}}}},
    ],
)
def test_invalid_config_is_rejected(raw):
    with pytest.raises((InvalidConfigError, ValueError)):
        config_from_dict(raw)
