"""Tests de enlace entre ventanas, ids estables, fusión de eventos e idempotencia."""

from __future__ import annotations

import json
from dataclasses import replace

import pandas as pd
import pytest

from fire_etl import pipeline
from fire_etl.config import PipelineConfig
from fire_etl.outputs import read_previous_state, write_tables
from fire_etl.pipeline import process_detections
from tests.conftest import MODIS_SAMPLE, VIIRS_SAMPLE, ingest_rows, modis_row, viirs_row

THREE_KM_EAST = 3000.0


def run_rows(config: PipelineConfig, output_dir=None, modis=None, viirs=None):
    """Procesa filas sintéticas reutilizando (y actualizando) el estado de ``output_dir``."""
    previous = read_previous_state(output_dir) if output_dir is not None else None
    tables = process_detections(ingest_rows(config, modis=modis, viirs=viirs), config, previous)
    if output_dir is not None:
        write_tables(tables, output_dir, config.geometry.storage_crs)

    return tables


def test_detections_across_midnight_are_linked_into_one_event(config):
    tables = run_rows(
        config, viirs=[viirs_row("2026-09-14 23:50"), viirs_row("2026-09-15 00:10", north_m=200)]
    )

    assert len(tables.observations) == 2
    assert len(tables.events) == 1
    assert tables.event_detections["event_id"].nunique() == 1
    assert len(tables.event_detections) == 2


def test_observations_beyond_link_distance_form_distinct_events(config):
    tables = run_rows(
        config, viirs=[viirs_row("2026-09-14 12:00"), viirs_row("2026-09-15 12:00", north_m=5000)]
    )

    assert len(tables.events) == 2


def test_non_consecutive_windows_are_not_linked(config):
    tables = run_rows(
        config, viirs=[viirs_row("2026-09-13 12:00"), viirs_row("2026-09-15 12:00")]
    )

    assert len(tables.events) == 2


def test_event_records_parameters_and_frp_series(config):
    tables = run_rows(
        config,
        viirs=[viirs_row("2026-09-14 12:00", frp=4.0), viirs_row("2026-09-15 12:00", frp=9.0)],
    )
    event = tables.events.iloc[0]
    series = json.loads(event["frp_series"])

    assert event["param_window_mode"] == "day_utc"
    assert event["param_footprint_buffer_m"] == 400.0
    assert event["param_link_max_distance_m"] == 1000.0
    assert json.loads(event["params"])["link_max_window_gap"] == 1
    assert [item["frp_total"] for item in series] == [4.0, 9.0]
    assert event["frp_max"] == 9.0
    assert event["biomass_max_kg_s"] == pytest.approx(9.0 * 0.368)
    assert event["biomass_total_kg_s"] == pytest.approx(13.0 * 0.368)
    assert [item["biomass_total_kg_s"] for item in series] == pytest.approx([4 * 0.368, 9 * 0.368])


def test_event_status_depends_on_inactivity(config):
    reference = pd.Timestamp("2026-09-20 00:00", tz="UTC").to_pydatetime()
    closed_cfg = replace(config, events=replace(config.events, reference_time=reference))

    active = run_rows(config, viirs=[viirs_row("2026-09-15 12:00")])
    closed = run_rows(closed_cfg, viirs=[viirs_row("2026-09-15 12:00")])

    assert active.events["status"].tolist() == ["active"]
    assert closed.events["status"].tolist() == ["closed"]


def test_isolated_low_observations_are_flagged_and_excluded(config):
    tables = run_rows(config, viirs=[viirs_row("2026-09-15 12:00", confidence="low")])

    assert tables.observations["is_isolated_low"].tolist() == [True]
    assert tables.observations["event_id"].isna().all()
    assert tables.events.empty


def test_pipeline_is_idempotent_on_same_files(config, tmp_path):
    inputs = [MODIS_SAMPLE, VIIRS_SAMPLE]

    clean_dir = tmp_path / "clean_data"
    first = pipeline.run(inputs, config, tmp_path, clean_dir)
    second = pipeline.run(inputs, config, tmp_path, clean_dir)

    assert len(second.detections) == len(first.detections) == 40
    assert second.detections["detection_id"].is_unique
    assert sorted(second.events["event_id"]) == sorted(first.events["event_id"])
    assert len(pd.read_parquet(tmp_path / "detections.parquet")) == 40
    assert second.merges.empty


def test_event_id_is_kept_when_new_data_extends_event(config, tmp_path):
    first = run_rows(config, tmp_path, viirs=[viirs_row("2026-09-14 12:00")])
    second = run_rows(config, tmp_path, viirs=[viirs_row("2026-09-15 12:00", north_m=300)])

    assert second.events["event_id"].tolist() == first.events["event_id"].tolist()
    assert second.events["n_detections"].tolist() == [2]


def test_two_events_merge_into_oldest_and_merge_is_recorded(config, tmp_path):
    first = run_rows(
        config,
        tmp_path,
        viirs=[
            viirs_row("2026-09-14 10:00", east_m=-THREE_KM_EAST / 2),
            viirs_row("2026-09-14 12:00", east_m=THREE_KM_EAST / 2),
        ],
    )
    first_events = first.events.sort_values("first_detection")
    oldest_id, newest_id = first_events["event_id"].tolist()

    # Una detección MODIS del día siguiente (3 km x 1 km + buffer) toca ambos eventos.
    second = run_rows(config, tmp_path, modis=[modis_row("2026-09-15 10:30", scan=3.0)])
    event = second.events.iloc[0]

    assert len(first.events) == 2
    assert len(second.events) == 1
    assert event["event_id"] == oldest_id
    assert json.loads(event["merged_event_ids"]) == [newest_id]
    assert second.merges[["merged_event_id", "kept_event_id"]].values.tolist() == [
        [newest_id, oldest_id]
    ]
    assert set(read_previous_state(tmp_path).merges["merged_event_id"]) == {newest_id}
