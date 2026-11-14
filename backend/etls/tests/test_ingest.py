"""Tests de ingesta y normalización de CSV FIRMS."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.config import config_from_dict
from fire_etl.ingest import (
    DETECTION_COLUMNS,
    Source,
    UnknownFirmsFormatError,
    detect_source,
    ingest_files,
    parse_acq_datetime,
)
from tests.conftest import MODIS_SAMPLE, VIIRS_SAMPLE, ingest_rows, modis_row, viirs_row


def test_ingest_sample_files_normalizes_both_sensors(config):
    result = ingest_files([MODIS_SAMPLE, VIIRS_SAMPLE], config)
    detections = result.detections

    assert list(detections.columns) == list(DETECTION_COLUMNS)
    assert len(detections) == 40
    assert result.discarded.empty
    assert detections["source"].value_counts().to_dict() == {"MODIS": 20, "VIIRS": 20}
    assert str(detections["acq_datetime"].dt.tz) == "UTC"
    assert detections["detection_id"].is_unique


def test_ingest_sample_viirs_keeps_raw_confidence_word(config):
    detections = ingest_files([VIIRS_SAMPLE], config).detections

    assert set(detections["confidence_raw"]) == {"nominal"}
    assert set(detections["confidence_norm"]) == {"nominal"}
    assert set(detections["satellite"]) == {"N20"}


def test_ingest_sample_modis_maps_numeric_confidence(config):
    detections = ingest_files([MODIS_SAMPLE], config).detections
    by_raw = detections.set_index("confidence_raw")["confidence_norm"]

    assert by_raw["0"] == "low"
    assert by_raw["20"] == "low"
    assert by_raw["71"] == "nominal"
    assert by_raw["100"].unique().tolist() == ["high"]


@pytest.mark.parametrize(
    ("acq_time", "expected"),
    [("03:04", "03:04"), ("304", "03:04"), ("0304", "03:04"), ("5", "00:05"), ("2359", "23:59")],
)
def test_parse_acq_datetime_accepts_nrt_and_api_formats(acq_time, expected):
    parsed = parse_acq_datetime(pd.Series(["2026-09-15"]), pd.Series([acq_time]))

    assert parsed.iloc[0] == pd.Timestamp(f"2026-09-15 {expected}", tz="UTC")


def test_parse_acq_datetime_invalid_values_are_nat():
    parsed = parse_acq_datetime(pd.Series(["2026-09-15", "bad"]), pd.Series(["25:00", "10:00"]))

    assert parsed.isna().all()


def test_ingest_discards_invalid_rows_with_reasons(config):
    rows = [
        modis_row("2026-09-15 10:00"),
        {**modis_row("2026-09-15 10:00"), "latitude": "95"},
        {**modis_row("2026-09-15 10:00", north_m=1), "frp": "-1"},
        {**modis_row("2026-09-15 10:00", north_m=2), "confidence": "150"},
        {**modis_row("2026-09-15 10:00", north_m=3), "acq_time": "xx"},
        {**modis_row("2026-09-15 10:00", north_m=4), "daynight": "Z"},
    ]

    result = ingest_rows(config, modis=rows)
    reasons = result.discarded.set_index("reason")["count"].to_dict()

    assert len(result.detections) == 1
    assert reasons == {
        "invalid_lat": 1,
        "invalid_frp": 1,
        "invalid_confidence": 1,
        "invalid_datetime": 1,
        "invalid_daynight": 1,
    }


def test_ingest_deduplicates_by_deterministic_detection_id(config):
    row = viirs_row("2026-09-15 10:00")

    first = ingest_rows(config, viirs=[row, row])
    second = ingest_rows(config, viirs=[row])

    assert len(first.detections) == 1
    assert first.discarded.set_index("reason")["count"].to_dict() == {"duplicate": 1}
    assert first.detections["detection_id"].tolist() == second.detections["detection_id"].tolist()


def test_biomass_rate_is_frp_times_configured_coefficient(config):
    result = ingest_rows(config, modis=[modis_row("2026-09-15 10:00", frp=100.0)])

    assert result.detections["biomass_kg_s"].iloc[0] == pytest.approx(36.8)


def test_biomass_coefficient_comes_from_config():
    custom = config_from_dict({"biomass": {"coefficient_kg_per_mj": 0.5}})

    result = ingest_rows(custom, viirs=[viirs_row("2026-09-15 10:00", frp=10.0)])

    assert result.detections["biomass_kg_s"].iloc[0] == pytest.approx(5.0)


def test_detect_source_by_columns():
    assert detect_source(modis_row("2026-09-15 10:00")) is Source.MODIS
    assert detect_source(viirs_row("2026-09-15 10:00")) is Source.VIIRS

    with pytest.raises(UnknownFirmsFormatError):
        detect_source(["latitude", "longitude"])
