"""Tests de los datos finales (clean_data) para el mapa y el chatbot."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from fire_etl.clean_data import (
    DETECTION_COLUMNS,
    EVENT_COLUMNS,
    build_clean_data,
    write_clean_data,
)
from fire_etl.config import PipelineConfig
from fire_etl.ingest import read_firms_csv
from fire_etl.pipeline import process_detections, run_frames
from tests.conftest import BASE_LAT, BASE_LON, MODIS_SAMPLE, VIIRS_SAMPLE, ingest_rows, viirs_row


def clean_for(config, modis=None, viirs=None):
    tables = process_detections(ingest_rows(config, modis=modis, viirs=viirs), config)

    return build_clean_data(tables, config)


@pytest.fixture
def midnight_event(config):
    """Un único evento VIIRS (intensidad media) que cruza la medianoche."""
    return clean_for(
        config,
        viirs=[
            viirs_row("2026-09-14 23:50", frp=50.0),
            viirs_row("2026-09-15 00:10", north_m=200, frp=60.0),
        ],
    )


def test_clean_events_have_only_final_columns_and_readable_values(midnight_event):
    events = midnight_event.events
    event = events.iloc[0]

    assert list(events.columns) == [*EVENT_COLUMNS, "frp_series"]
    assert len(events) == 1
    assert event["status"] == "active"
    assert event["n_detections"] == 2
    assert event["duration_hours"] == pytest.approx(0.33)
    assert event["lat"] == pytest.approx(BASE_LAT, abs=0.01)
    assert event["lon"] == pytest.approx(BASE_LON, abs=0.01)
    assert event["frp_max_mw"] == 60.0
    assert event["frp_category"] == "medium"
    assert event["footprint_area_km2"] > 0
    assert len(event["frp_series"]) == 2


def test_event_summary_is_a_spanish_sentence_with_the_caveat(midnight_event):
    summary = midnight_event.events.iloc[0]["summary"]

    assert summary.startswith("Incendio activo detectado por VIIRS")
    assert "Intensidad media" in summary
    assert "(no es superficie quemada)" in summary


def test_clean_detections_only_include_detections_of_events(config):
    clean = clean_for(
        config,
        viirs=[
            viirs_row("2026-09-15 12:00", frp=50.0),
            viirs_row("2026-09-15 12:00", north_m=20_000, frp=50.0, confidence="low"),
        ],
    )

    assert len(clean.events) == 1
    assert len(clean.detections) == 1
    assert list(clean.detections.columns) == list(DETECTION_COLUMNS)
    assert clean.detections["event_id"].tolist() == clean.events["event_id"].tolist()


def test_written_files_are_valid_geojson_and_json(config, midnight_event, tmp_path):
    write_clean_data(midnight_event, tmp_path)

    events_geojson = json.loads((tmp_path / "fire_events.geojson").read_text(encoding="utf-8"))
    areas = json.loads((tmp_path / "fire_event_areas.geojson").read_text(encoding="utf-8"))
    detections = json.loads((tmp_path / "fire_detections.geojson").read_text(encoding="utf-8"))
    records = json.loads((tmp_path / "fire_events.json").read_text(encoding="utf-8"))
    metadata = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))

    point = events_geojson["features"][0]
    assert point["geometry"]["type"] == "Point"
    assert point["geometry"]["coordinates"] == [records[0]["lon"], records[0]["lat"]]
    assert "frp_series" not in point["properties"]
    assert areas["features"][0]["geometry"]["type"] == "Polygon"
    assert len(detections["features"]) == 2
    assert records[0]["first_detection"] == "2026-09-14T23:50:00Z"
    assert isinstance(records[0]["frp_series"], list)
    assert records[0]["land_cover"] is None  # sin SIGPAC configurado: nulo, no NaN
    assert metadata["counts"] == {"fire_events": 1, "fire_detections": 2}
    assert any("NO es superficie quemada" in note for note in metadata["notes"])


def test_flagged_noise_stays_in_detections_but_not_in_events_or_clean_data():
    enabled = PipelineConfig()
    rows = [
        viirs_row("2026-09-15 12:00", frp=50.0),  # válida
        viirs_row("2026-09-15 12:00", north_m=20_000, frp=3.6),  # < 3,7 MW de día: ruido
    ]

    tables = process_detections(ingest_rows(enabled, viirs=rows), enabled)
    clean = build_clean_data(tables, enabled)
    detections = tables.detections.set_index("frp")

    assert len(tables.detections) == 2
    assert detections["is_valid"].to_dict() == {50.0: True, 3.6: False}
    assert detections.loc[3.6, "skip_reason"] == "below_frp_threshold"
    assert detections.loc[50.0, "skip_reason"] is None
    assert pd.isna(detections.loc[3.6, "observation_id"])
    assert len(tables.events) == 1 and len(tables.event_detections) == 1
    assert len(clean.detections) == 1
    assert tables.run_parameters["counts"]["skipped_detections"] == 1


def test_pipeline_writes_clean_data_folder(config, tmp_path):
    frames = [read_firms_csv(MODIS_SAMPLE), read_firms_csv(VIIRS_SAMPLE)]

    run_frames(frames, config, tmp_path / "output", tmp_path / "clean_data")

    written = sorted(path.name for path in (tmp_path / "clean_data").iterdir())
    assert written == [
        "fire_detections.geojson",
        "fire_event_areas.geojson",
        "fire_events.geojson",
        "fire_events.json",
        "metadata.json",
    ]


def test_clean_data_without_events_writes_empty_collections(config, tmp_path):
    clean = clean_for(config, viirs=[viirs_row("2026-09-15 12:00", confidence="low")])

    write_clean_data(clean, tmp_path)

    assert json.loads((tmp_path / "fire_events.json").read_text(encoding="utf-8")) == []
    events = json.loads((tmp_path / "fire_events.geojson").read_text(encoding="utf-8"))
    assert events["features"] == []
