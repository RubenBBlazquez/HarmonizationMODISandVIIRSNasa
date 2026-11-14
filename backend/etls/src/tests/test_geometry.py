"""Tests de geometrías: CRS métrico, footprints y área de interés."""

from __future__ import annotations

import pytest
import shapely

from fire_etl.config import AreaOfInterestConfig, GeometryConfig
from fire_etl.geometry import (
    MetricCrsError,
    build_detection_geometries,
    resolve_metric_crs,
    utm_crs_for_lon_lat,
)
from tests.conftest import BASE_LAT, BASE_LON, ingest_rows, modis_row, viirs_row


def test_footprint_is_scan_by_track_plus_buffer(config):
    detections = ingest_rows(config, modis=[modis_row("2026-09-15 10:00", scan=2.0, track=1.5)])

    result = build_detection_geometries(
        detections.detections, GeometryConfig(footprint_buffer_m=400.0), AreaOfInterestConfig()
    )
    min_x, min_y, max_x, max_y = result.detections.geometry.iloc[0].bounds

    assert result.metric_crs.to_epsg() == 3035
    assert max_x - min_x == pytest.approx(2000 + 2 * 400, abs=1e-6)
    assert max_y - min_y == pytest.approx(1500 + 2 * 400, abs=1e-6)
    assert result.detections["footprint_area_m2"].iloc[0] == pytest.approx(2800 * 2300)


def test_geometry_keeps_point_and_footprint_columns(config):
    detections = ingest_rows(config, viirs=[viirs_row("2026-09-15 10:00")]).detections

    frame = build_detection_geometries(
        detections, GeometryConfig(), AreaOfInterestConfig()
    ).detections

    assert frame.geometry.name == "geom_footprint"
    assert frame["geom_point"].iloc[0].geom_type == "Point"
    assert frame["geom_footprint"].iloc[0].contains(frame["geom_point"].iloc[0])


def test_area_of_interest_filters_and_counts_outside(config):
    rows = [modis_row("2026-09-15 10:00"), modis_row("2026-09-15 10:00", north_m=50_000)]
    detections = ingest_rows(config, modis=rows).detections
    bbox = (BASE_LON - 0.1, BASE_LAT - 0.1, BASE_LON + 0.1, BASE_LAT + 0.1)
    aoi = AreaOfInterestConfig(bbox=bbox)

    result = build_detection_geometries(detections, GeometryConfig(), aoi)

    assert len(result.detections) == 1
    assert result.outside_aoi == 1


def test_auto_metric_crs_uses_utm_zone_of_reference():
    crs = resolve_metric_crs("auto", shapely.box(-7.5, 42.0, -7.0, 43.0))

    assert crs.to_epsg() == 32629
    assert utm_crs_for_lon_lat(3.8, 51.2).to_epsg() == 32631
    assert utm_crs_for_lon_lat(-60.0, -10.0).to_epsg() == 32720


def test_geographic_crs_is_rejected_for_calculations():
    with pytest.raises(MetricCrsError):
        resolve_metric_crs("EPSG:4326", None)
