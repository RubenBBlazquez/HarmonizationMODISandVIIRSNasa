"""Tests de la energía radiada y la biomasa quemada estimadas por evento."""

from __future__ import annotations

import pandas as pd
import pytest

from fire_etl.config import BiomassConfig
from fire_etl.energy import event_energy

CONFIG = BiomassConfig(coefficient_kg_per_mj=0.368, pass_gap_minutes=30, pass_window_hours=6)
SECONDS_PER_HOUR = 3600


def detections(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    """Filas ``(evento, "AAAA-MM-DD HH:MM", sensor, frp)``."""
    return pd.DataFrame(
        {
            "event_id": [row[0] for row in rows],
            "acq_datetime": pd.to_datetime([row[1] for row in rows], utc=True),
            "source": [row[2] for row in rows],
            "frp": [row[3] for row in rows],
        }
    )


def test_single_pass_represents_the_whole_window():
    result = event_energy(detections([("e1", "2022-06-16 01:15", "VIIRS", 10.0)]), CONFIG)

    assert result.loc["e1", "n_passes"] == 1
    assert result.loc["e1", "max_pass_gap_hours"] == 0
    assert result.loc["e1", "fre_mj"] == pytest.approx(10.0 * 6 * SECONDS_PER_HOUR)
    assert result.loc["e1", "biomass_burned_kg"] == pytest.approx(10.0 * 6 * 3600 * 0.368)


def test_detections_of_the_same_pass_are_added_up():
    rows = [("e1", "2022-06-16 01:15", "VIIRS", 10.0), ("e1", "2022-06-16 01:16", "VIIRS", 5.0)]

    result = event_energy(detections(rows), CONFIG)

    assert result.loc["e1", "n_passes"] == 1
    assert result.loc["e1", "fre_mj"] == pytest.approx(15.0 * 6 * SECONDS_PER_HOUR)


def test_two_sensors_in_the_same_pass_are_not_double_counted():
    rows = [("e1", "2022-06-16 10:00", "MODIS", 30.0), ("e1", "2022-06-16 10:10", "VIIRS", 20.0)]

    result = event_energy(detections(rows), CONFIG)

    assert result.loc["e1", "n_passes"] == 1
    assert result.loc["e1", "fre_mj"] == pytest.approx(30.0 * 6 * SECONDS_PER_HOUR)  # el mayor


def test_passes_split_the_interval_at_the_midpoint_and_cap_at_half_a_window():
    # Pasadas a las 0:00 (FRP 10) y a las 4:00 (FRP 20), ventana de 6 h.
    rows = [("e1", "2022-06-16 00:00", "VIIRS", 10.0), ("e1", "2022-06-16 04:00", "VIIRS", 20.0)]

    result = event_energy(detections(rows), CONFIG)

    # 1.ª: 3 h antes (media ventana) + 2 h después (mitad del hueco) = 5 h.
    # 2.ª: 2 h antes + 3 h después = 5 h.
    expected_mj = (10.0 * 5 + 20.0 * 5) * SECONDS_PER_HOUR
    assert result.loc["e1", "n_passes"] == 2
    assert result.loc["e1", "max_pass_gap_hours"] == pytest.approx(4.0)
    assert result.loc["e1", "fre_mj"] == pytest.approx(expected_mj)


def test_a_long_gap_does_not_inflate_the_energy():
    rows = [("e1", "2022-06-16 00:00", "VIIRS", 10.0), ("e1", "2022-06-17 00:00", "VIIRS", 10.0)]

    result = event_energy(detections(rows), CONFIG)

    assert result.loc["e1", "max_pass_gap_hours"] == pytest.approx(24.0)
    assert result.loc["e1", "fre_mj"] == pytest.approx(2 * 10.0 * 6 * SECONDS_PER_HOUR)  # 6 h c/u


def test_events_are_independent_and_unsorted_input_is_handled():
    rows = [
        ("b", "2022-06-16 12:00", "VIIRS", 1.0),
        ("a", "2022-06-16 04:00", "VIIRS", 20.0),
        ("a", "2022-06-16 00:00", "VIIRS", 10.0),
    ]

    result = event_energy(detections(rows), CONFIG)

    assert result["n_passes"].to_dict() == {"a": 2, "b": 1}
    assert result.loc["b", "fre_mj"] == pytest.approx(1.0 * 6 * SECONDS_PER_HOUR)
    assert result.loc["a", "fre_mj"] == pytest.approx((10 * 5 + 20 * 5) * SECONDS_PER_HOUR)


def test_no_detections_gives_an_empty_table_with_the_expected_columns():
    result = event_energy(detections([]), CONFIG)

    assert result.empty
    expected = ["n_passes", "max_pass_gap_hours", "fre_mj", "biomass_burned_kg"]
    assert list(result.columns) == expected


def test_biomass_config_rejects_non_positive_values():
    with pytest.raises(ValueError):
        BiomassConfig(pass_window_hours=0)
