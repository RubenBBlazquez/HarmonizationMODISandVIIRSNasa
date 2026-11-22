"""Tests of time windows and grouping into observations."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.config import PipelineConfig, WindowMode
from fire_etl.transform.processing.fusion import assign_time_windows, build_observations
from fire_etl.transform.processing.geometry import build_detection_geometries
from tests.conftest import modis_row, standardize_rows, viirs_row


def observations_for(config: PipelineConfig, modis=None, viirs=None):
    detections = standardize_rows(config, modis=modis, viirs=viirs).detections
    geometries = build_detection_geometries(detections, config.geometry, config.area_of_interest)
    windowed = assign_time_windows(geometries.detections, config.windows.mode)

    return build_observations(windowed, config.confidence)


def test_overlapping_modis_and_viirs_form_one_observation_with_high_confidence(config):
    result = observations_for(
        config,
        modis=[modis_row("2026-09-15 10:30", confidence=70)],
        viirs=[viirs_row("2026-09-15 10:35", north_m=300, confidence="nominal")],
    )
    observation = result.observations.iloc[0]

    assert len(result.observations) == 1
    assert observation["sensors"] == "MODIS,VIIRS"
    assert observation["confidence_modis"] == "nominal"
    assert observation["confidence_viirs"] == "nominal"
    assert observation["confidence_fused"] == "high"
    assert observation["n_modis"] == 1 and observation["n_viirs"] == 1


def test_overlapping_high_confidence_sensors_give_very_high(config):
    result = observations_for(
        config,
        modis=[modis_row("2026-09-15 10:30", confidence=95)],
        viirs=[viirs_row("2026-09-15 10:35", confidence="h")],
    )

    assert result.observations["confidence_fused"].tolist() == ["very_high"]


def test_representative_prefers_viirs_and_keeps_modis_detections(config):
    result = observations_for(
        config,
        modis=[modis_row("2026-09-15 10:30", confidence=95, frp=200.0)],
        viirs=[viirs_row("2026-09-15 10:35", confidence="nominal", frp=3.0)],
    )
    observation = result.observations.iloc[0]
    representative = result.detections.set_index("detection_id").loc[
        observation["representative_detection_id"]
    ]

    assert observation["representative_source"] == "VIIRS"
    assert representative["source"] == "VIIRS"
    assert len(result.detections) == 2
    assert result.detections["observation_id"].nunique() == 1
    assert observation["frp_total"] == 203.0 and observation["frp_max"] == 200.0


def test_observation_biomass_aggregates_detection_biomass(config):
    result = observations_for(
        config,
        modis=[modis_row("2026-09-15 10:30", frp=200.0)],
        viirs=[viirs_row("2026-09-15 10:35", frp=3.0)],
    )
    observation = result.observations.iloc[0]

    assert observation["biomass_total_kg_s"] == pytest.approx(203.0 * 0.368)
    assert observation["biomass_max_kg_s"] == pytest.approx(200.0 * 0.368)


def test_modis_is_representative_only_without_viirs(config):
    result = observations_for(config, modis=[modis_row("2026-09-15 10:30")])

    assert result.observations["representative_source"].tolist() == ["MODIS"]


def test_detections_separated_beyond_buffer_form_distinct_observations(config):
    # VIIRS footprint: 400 m + 2 x 400 m of buffer -> 600 m from the center; 2 km of separation
    # leave an 800 m gap (more than the buffer).
    result = observations_for(
        config,
        viirs=[viirs_row("2026-09-15 10:30"), viirs_row("2026-09-15 10:30", north_m=2000)],
    )

    assert len(result.observations) == 2
    assert result.detections["observation_id"].nunique() == 2


def test_detections_within_buffer_reach_are_grouped(config):
    result = observations_for(
        config,
        viirs=[viirs_row("2026-09-15 10:30"), viirs_row("2026-09-15 10:30", north_m=1100)],
    )

    assert len(result.observations) == 1


def test_overlapping_detections_in_different_windows_are_not_grouped(config):
    result = observations_for(
        config, viirs=[viirs_row("2026-09-14 23:50"), viirs_row("2026-09-15 00:10")]
    )

    assert len(result.observations) == 2
    assert sorted(result.observations["window_index"].diff().dropna().abs()) == [1]


def test_hours_12_windows_split_day():
    times = pd.Series(pd.to_datetime(["2026-09-15 11:59", "2026-09-15 12:00"], utc=True))
    frame = pd.DataFrame({"acq_datetime": times, "acq_date": times.dt.date, "daynight": "D"})

    windowed = assign_time_windows(frame, WindowMode.HOURS_12)

    assert windowed["window_index"].diff().iloc[1] == 1
    assert windowed["window_start"].iloc[1] == pd.Timestamp("2026-09-15 12:00", tz="UTC")


def test_date_daynight_windows_are_consecutive_within_a_date():
    times = pd.Series(pd.to_datetime(["2026-09-15 02:00", "2026-09-15 13:00"], utc=True))
    frame = pd.DataFrame(
        {"acq_datetime": times, "acq_date": times.dt.date, "daynight": ["N", "D"]}
    )

    windowed = assign_time_windows(frame, WindowMode.DATE_DAYNIGHT)

    assert windowed["window_key"].tolist() == [
        "date_daynight:2026-09-15:N",
        "date_daynight:2026-09-15:D",
    ]
    assert windowed["window_index"].diff().iloc[1] == 1


def test_date_daynight_index_is_absolute_across_data_gaps():
    times = pd.Series(
        pd.to_datetime(["2026-09-15 13:00", "2026-09-16 02:00", "2026-09-23 02:00"], utc=True)
    )
    frame = pd.DataFrame(
        {"acq_datetime": times, "acq_date": times.dt.date, "daynight": ["D", "N", "N"]}
    )

    windowed = assign_time_windows(frame, WindowMode.DATE_DAYNIGHT)

    assert windowed["window_index"].diff().tolist()[1:] == [1, 14]
