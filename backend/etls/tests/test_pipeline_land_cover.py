"""El pipeline principal enriquece las detecciones con SIGPAC cuando está configurado."""

from __future__ import annotations

from dataclasses import replace

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from fire_etl.config import LandCoverConfig
from fire_etl.outputs import write_tables
from fire_etl.pipeline import NoDetectionsError, process_detections
from tests.conftest import BASE_LAT, BASE_LON, ingest_rows, modis_row


def _sigpac_folder(tmp_path, use="FO"):
    folder = tmp_path / "sigpac"
    folder.mkdir()
    recinto = box(BASE_LON - 0.01, BASE_LAT - 0.01, BASE_LON + 0.01, BASE_LAT + 0.01)
    gpd.GeoDataFrame({"uso_sigpac": [use]}, geometry=[recinto], crs=4326).to_crs(25829).to_file(
        folder / "recintos.gpkg", layer="recintos", driver="GPKG"
    )

    return folder


def test_pipeline_adds_land_cover_columns_to_detections(config, tmp_path):
    config = replace(config, land_cover=LandCoverConfig(sigpac_dir=str(_sigpac_folder(tmp_path))))
    tables = process_detections(ingest_rows(config, modis=[modis_row("2026-09-15 12:00")]), config)

    row = tables.detections.iloc[0]
    assert row["land_cover"] == "trees" and bool(row["is_forest"])

    write_tables(tables, tmp_path / "out", config.geometry.storage_crs)
    written = pd.read_parquet(tmp_path / "out" / "detections.parquet")
    assert {"land_cover", "land_cover_source", "is_forest"} <= set(written.columns)


def test_pipeline_without_sigpac_dir_adds_no_columns(config):
    tables = process_detections(ingest_rows(config, modis=[modis_row("2026-09-15 12:00")]), config)

    assert "land_cover" not in tables.detections.columns


def _with_frp_thresholds(config, tmp_path, use):
    land_cover = LandCoverConfig(sigpac_dir=str(_sigpac_folder(tmp_path, use)))
    validation = replace(config.validation, apply_frp_thresholds=True)

    return replace(config, land_cover=land_cover, validation=validation)


def test_small_fire_in_forest_is_kept_valid_as_low(config, tmp_path):
    config = _with_frp_thresholds(config, tmp_path, "FO")
    rows = [modis_row("2026-09-15 12:00", frp=1.0)]  # por debajo del mínimo de MODIS

    row = process_detections(ingest_rows(config, modis=rows), config).detections.iloc[0]

    assert bool(row["is_valid"]) and row["skip_reason"] is None
    assert row["frp_category"] == "low" and row["frp_class"] == 1


def test_small_fire_in_farmland_is_still_discarded(config, tmp_path):
    config = _with_frp_thresholds(config, tmp_path, "TA")
    rows = [modis_row("2026-09-15 12:00", frp=1.0)]

    with pytest.raises(NoDetectionsError):
        process_detections(ingest_rows(config, modis=rows), config)


def test_forest_rescue_can_be_disabled(config, tmp_path):
    config = _with_frp_thresholds(config, tmp_path, "FO")
    config = replace(config, validation=replace(config.validation, keep_forest_below_frp=False))
    rows = [modis_row("2026-09-15 12:00", frp=1.0)]

    with pytest.raises(NoDetectionsError):
        process_detections(ingest_rows(config, modis=rows), config)
