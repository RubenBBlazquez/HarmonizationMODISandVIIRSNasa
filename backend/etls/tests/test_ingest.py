"""Tests de ingesta y normalización de CSV FIRMS."""

from __future__ import annotations

import pandas as pd
import pytest

from dataclasses import replace
from datetime import date

from fire_etl.config import ExclusionZone, PipelineConfig, config_from_dict
from fire_etl.ingest import (
    DETECTION_COLUMNS,
    Source,
    UnknownFirmsFormatError,
    detect_source,
    ingest_files,
    ingest_frames,
    parse_acq_datetime,
)
from tests.conftest import (
    BASE_LAT,
    BASE_LON,
    MODIS_SAMPLE,
    VIIRS_SAMPLE,
    ingest_rows,
    modis_row,
    viirs_row,
)


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


def test_frp_thresholds_flag_noise_instead_of_discarding_it():
    enabled = PipelineConfig()
    modis = [
        modis_row("2026-09-15 10:00", frp=5.6),  # día, < 5,7 MW: ruido
        modis_row("2026-09-15 10:00", north_m=5000, frp=100.0),  # día: media
    ]
    viirs = [
        viirs_row("2026-09-15 02:00", daynight="N", frp=0.6),  # noche, < 0,7 MW: ruido
        viirs_row("2026-09-15 02:00", north_m=5000, daynight="N", frp=0.8),  # noche: baja
    ]

    result = ingest_rows(enabled, modis=modis, viirs=viirs)
    rows = result.detections.set_index("frp")

    assert len(result.detections) == 4
    assert result.discarded.empty
    assert rows["is_valid"].to_dict() == {5.6: False, 100.0: True, 0.6: False, 0.8: True}
    assert rows.loc[[5.6, 0.6], "skip_reason"].tolist() == ["below_frp_threshold"] * 2
    assert rows.loc[[100.0, 0.8], "skip_reason"].isna().all()
    assert rows.loc[100.0, "frp_category"] == "medium"
    assert rows.loc[0.8, "frp_category"] == "low"


def test_frp_thresholds_are_read_from_configuration():
    config = config_from_dict(
        {
            "validation": {
                "frp_thresholds_mw": {
                    "VIIRS": {
                        "min_day": 20.0,
                        "min_night": 0.7,
                        "limit_low": 35.0,
                        "limit_medium": 70.0,
                    },
                    "MODIS": {
                        "min_day": 5.7,
                        "min_night": 3.7,
                        "limit_low": 75.0,
                        "limit_medium": 175.0,
                    },
                }
            }
        }
    )

    result = ingest_rows(config, viirs=[viirs_row("2026-09-15 10:00", frp=10.0)])

    assert result.detections["skip_reason"].tolist() == ["below_frp_threshold"]


def test_frp_thresholds_can_be_disabled(config):
    result = ingest_rows(config, viirs=[viirs_row("2026-09-15 02:00", daynight="N", frp=1.0)])

    assert len(result.detections) == 1
    assert result.detections["is_valid"].tolist() == [True]
    assert result.detections["skip_reason"].isna().all()
    assert result.discarded.empty


def test_biomass_rate_is_frp_times_configured_coefficient(config):
    result = ingest_rows(config, modis=[modis_row("2026-09-15 10:00", frp=100.0)])

    assert result.detections["biomass_kg_s"].iloc[0] == pytest.approx(36.8)


def test_biomass_coefficient_comes_from_config():
    custom = config_from_dict(
        {
            "biomass": {"coefficient_kg_per_mj": 0.5},
            "validation": {"apply_frp_thresholds": False},  # FRP sintético por debajo del umbral
        }
    )

    result = ingest_rows(custom, viirs=[viirs_row("2026-09-15 10:00", frp=10.0)])

    assert result.detections["biomass_kg_s"].iloc[0] == pytest.approx(5.0)


def test_archive_format_rows_are_normalized_like_nrt_rows(config):
    modis_columns = (
        "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,"
        "confidence,version,bright_t31,frp,daynight,type"
    ).split(",")
    viirs_columns = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
        "confidence,version,bright_ti5,frp,daynight,type"
    ).split(",")
    modis_values = "43.1717,-6.3778,322.3,1.1,1,2020-01-01,0242,Aqua,MODIS,100,6.03,276.9,28.9,N,0"
    viirs_values = "41.19945,1.22071,331.38,0.66,0.73,2020-01-01,0043,N,VIIRS,n,2,275.61,3.28,N,0"
    modis = pd.DataFrame([modis_values.split(",")], columns=modis_columns)
    viirs = pd.DataFrame([viirs_values.split(",")], columns=viirs_columns)

    detections = ingest_frames([modis, viirs], config).detections.set_index("source")

    assert detections.loc["MODIS", "satellite"] == "A"  # Aqua -> A, como en los NRT
    assert detections.loc["MODIS", "acq_datetime"] == pd.Timestamp("2020-01-01 02:42", tz="UTC")
    assert detections.loc["VIIRS", "confidence_norm"] == "nominal"
    assert detections.loc["VIIRS", "acq_datetime"] == pd.Timestamp("2020-01-01 00:43", tz="UTC")


def test_detect_source_by_columns():
    assert detect_source(modis_row("2026-09-15 10:00")) is Source.MODIS
    assert detect_source(viirs_row("2026-09-15 10:00")) is Source.VIIRS

    with pytest.raises(UnknownFirmsFormatError):
        detect_source(["latitude", "longitude"])


# --- Fuentes que no son incendios de vegetación (type de FIRMS y zonas de exclusión) ----------


def archive_frame(rows: list[dict[str, str]]) -> pd.DataFrame:
    """Filas en formato de los ficheros anuales de FIRMS (con la columna ``type``)."""
    return pd.DataFrame([{**row, "instrument": "VIIRS"} for row in rows], dtype=str)


def test_firms_type_other_than_zero_is_flagged_with_its_reason(config):
    rows = [
        {**viirs_row("2022-06-16 01:15", north_m=0), "type": "0"},
        {**viirs_row("2022-06-16 01:15", north_m=3000), "type": "1"},
        {**viirs_row("2022-06-16 01:15", north_m=6000), "type": "2"},
        {**viirs_row("2022-06-16 01:15", north_m=9000), "type": "3"},
    ]

    detections = ingest_frames([archive_frame(rows)], config).detections
    result = detections.sort_values("lat", ignore_index=True)  # de sur a norte, como las filas

    assert result["is_valid"].tolist() == [True, False, False, False]
    assert result["skip_reason"].tolist() == [
        None,
        "active_volcano",
        "static_land_source",
        "offshore_detection",
    ]
    assert "firms_type" not in result.columns  # solo se usa para marcar


def test_rows_without_type_column_are_valid(config):
    result = ingest_rows(config, viirs=[viirs_row("2022-06-16 01:15")]).detections

    assert result["is_valid"].tolist() == [True] and result["skip_reason"].isna().all()


def test_exclusion_zone_flags_detections_inside_the_box_and_dates(config):
    zone = ExclusionZone(
        name="volcan",
        bbox=(BASE_LON - 0.05, BASE_LAT - 0.05, BASE_LON + 0.05, BASE_LAT + 0.05),
        start=date(2021, 9, 19),
        end=date(2021, 12, 31),
        reason="volcanic_eruption",
    )
    rules = replace(config.validation, exclusion_zones=(zone,))
    zoned = replace(config, validation=rules)
    rows = [
        viirs_row("2021-10-01 02:00"),  # dentro de la zona y de las fechas
        viirs_row("2021-10-01 02:00", north_m=20_000),  # fuera de la zona
        viirs_row("2022-06-01 02:00"),  # dentro de la zona, fuera de las fechas
        viirs_row("2021-09-19 02:00", north_m=100),  # primer día (incluido)
        viirs_row("2021-12-31 02:00", north_m=200),  # último día (incluido)
    ]

    result = ingest_rows(zoned, viirs=rows).detections.sort_values("acq_datetime")

    flagged = result[~result["is_valid"]]
    assert sorted(flagged["acq_date"].astype(str)) == ["2021-09-19", "2021-10-01", "2021-12-31"]
    assert set(flagged["skip_reason"]) == {"volcanic_eruption"}
    assert int(result["is_valid"].sum()) == 2


def test_type_reason_wins_over_zone_and_frp_reasons():
    enabled = PipelineConfig()
    zone = ExclusionZone("z", (BASE_LON - 1, BASE_LAT - 1, BASE_LON + 1, BASE_LAT + 1))
    cfg = replace(enabled, validation=replace(enabled.validation, exclusion_zones=(zone,)))
    row = {**viirs_row("2022-06-16 01:15", daynight="N", frp=0.6), "type": "2"}  # FRP bajo + zona

    result = ingest_frames([archive_frame([row])], cfg).detections

    assert result["skip_reason"].tolist() == ["static_land_source"]


def test_source_filters_can_be_disabled(config):
    off = replace(config, validation=replace(config.validation, apply_source_filters=False))
    row = {**viirs_row("2022-06-16 01:15"), "type": "1"}

    result = ingest_frames([archive_frame([row])], off).detections

    assert result["is_valid"].tolist() == [True]
