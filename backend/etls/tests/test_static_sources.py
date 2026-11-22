"""Tests of the static thermal sources (analysis, flagging and loader; no database)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from fire_etl.config import InvalidConfigError, StaticSourcesConfig
from fire_etl.load.static_sources import (
    CREATE_TABLE_SQL,
    INSERT_SQL,
    StaticSourcesLoadError,
    ensure_table,
    load_static_sources,
)
from fire_etl.transform.firms.validation import BELOW_FRP_REASON
from fire_etl.transform.static_sources import (
    CRITERION_BOTH,
    CRITERION_FIRMS,
    CRITERION_PERSISTENCE,
    STAT_COLUMNS,
    StaticSourcesError,
    cell_indices,
    flag_static_sources,
    grid_srid,
    select_static_cells,
    stats_query_params,
)

CONFIG = StaticSourcesConfig(enabled=True)
# Steelworks in Nueva Montaña (Santander): static in the 2000-2025 history.
SANTANDER = (-3.842, 43.447)


def stats_row(ix, iy, days, years, months, firms=0):
    return {
        "ix": ix,
        "iy": iy,
        "n_detections": days,
        "n_days": days,
        "n_years": years,
        "n_months_of_year": months,
        "n_firms_static": firms,
        "first_date": date(2018, 1, 1),
        "last_date": date(2025, 12, 31),
    }


def detections(points, skip_reason=None, land_cover=None):
    n = len(points)
    return pd.DataFrame(
        {
            "lon": [p[0] for p in points],
            "lat": [p[1] for p in points],
            "is_valid": [skip_reason is None] * n,
            "skip_reason": [skip_reason] * n,
            "land_cover": [land_cover] * n,
        }
    )


def cell_of(lon, lat, config=CONFIG):
    ix, iy = cell_indices(pd.Series([lon]), pd.Series([lat]), config)
    return int(ix[0]), int(iy[0])


def offset(cell, dx, dy, config=CONFIG):
    """Lon/lat of the center of the cell ``cell + (dx, dy)``."""
    from pyproj import Transformer

    transformer = Transformer.from_crs(grid_srid(config), 4326, always_xy=True)
    size = config.cell_size_m
    return transformer.transform((cell[0] + dx + 0.5) * size, (cell[1] + dy + 0.5) * size)


# --- Configuration --------------------------------------------------------------------------


def test_config_rejects_invalid_thresholds():
    with pytest.raises(InvalidConfigError):
        StaticSourcesConfig(cell_size_m=0)
    with pytest.raises(InvalidConfigError):
        StaticSourcesConfig(min_months_of_year=13)
    with pytest.raises(InvalidConfigError):
        StaticSourcesConfig(min_firms_static=-1)


def test_grid_must_be_projected_in_meters():
    assert grid_srid(CONFIG) == 25830
    with pytest.raises(StaticSourcesError):
        grid_srid(StaticSourcesConfig(grid_crs="EPSG:4326"))


def test_stats_params_disable_firms_criterion_with_zero():
    params = stats_query_params(StaticSourcesConfig(min_firms_static=0))
    assert params["min_firms"] > 10**9
    assert params["ignored"] == ["active_volcano", "volcanic_eruption"]


# --- Analysis -------------------------------------------------------------------------------


def test_select_static_cells_applies_persistence_and_firms_criteria():
    stats = pd.DataFrame(
        [
            stats_row(1, 1, days=681, years=16, months=12, firms=734),  # industry
            stats_row(2, 2, days=40, years=8, months=3),  # summer wildfires every year
            stats_row(3, 3, days=12, years=1, months=8),  # a single year
            stats_row(4, 4, days=10, years=2, months=6),  # just on the thresholds
            stats_row(5, 5, days=4, years=2, months=3, firms=3),  # labeled only by FIRMS
        ]
    )

    cells = select_static_cells(stats, CONFIG)

    assert dict(zip(cells["ix"], cells["criterion"])) == {
        1: CRITERION_BOTH,
        4: CRITERION_PERSISTENCE,
        5: CRITERION_FIRMS,
    }
    assert list(cells["ix"]) == [1, 4, 5]  # sorted by n_days
    assert set(STAT_COLUMNS) <= set(cells.columns)


def test_select_static_cells_without_firms_criterion():
    stats = pd.DataFrame([stats_row(5, 5, days=4, years=2, months=3, firms=30)])

    assert select_static_cells(stats, StaticSourcesConfig(min_firms_static=0)).empty


def test_select_static_cells_requires_the_statistics():
    with pytest.raises(StaticSourcesError):
        select_static_cells(pd.DataFrame({"ix": [1]}), CONFIG)


# --- Flagging -------------------------------------------------------------------------------


def test_cell_indices_match_the_grid_formula():
    ix, iy = cell_of(*SANTANDER)
    # Same cell as floor(ST_X(ST_Transform(p, 25830)) / 400) in PostGIS.
    assert (ix, iy) == (1079, 12026)


def test_flags_detections_in_a_static_cell():
    cell = cell_of(*SANTANDER)
    frame = detections([SANTANDER, (-4.5, 42.0)])

    flagged, summary = flag_static_sources(frame, {cell}, CONFIG)

    assert list(flagged["is_valid"]) == [False, True]
    assert list(flagged["skip_reason"]) == ["persistent_thermal_source", None]
    assert (summary.static_cells, summary.in_cell, summary.in_neighbor) == (1, 1, 0)
    assert frame["is_valid"].all()  # the input is not modified


def test_source_reason_wins_over_low_frp_but_not_over_other_reasons():
    cell = cell_of(*SANTANDER)
    low_frp = detections([SANTANDER], skip_reason=BELOW_FRP_REASON)
    volcano = detections([SANTANDER], skip_reason="active_volcano")

    assert flag_static_sources(low_frp, {cell}, CONFIG)[0]["skip_reason"].iloc[0] == (
        "persistent_thermal_source"
    )
    assert flag_static_sources(volcano, {cell}, CONFIG)[0]["skip_reason"].iloc[0] == (
        "active_volcano"
    )


def test_neighbors_are_flagged_unless_their_land_cover_is_vegetation():
    cell = cell_of(*SANTANDER)
    neighbor = offset(cell, 1, -1)
    far = offset(cell, 2, 0)
    frame = pd.concat(
        [
            detections([neighbor], land_cover="built"),
            detections([neighbor], land_cover="crops|trees"),
            detections([far], land_cover="built"),
        ],
        ignore_index=True,
    )

    flagged, summary = flag_static_sources(frame, {cell}, CONFIG)

    assert list(flagged["is_valid"]) == [False, True, True]
    assert (summary.in_cell, summary.in_neighbor, summary.protected) == (0, 1, 1)


def test_neighbors_are_ignored_when_disabled():
    cell = cell_of(*SANTANDER)
    frame = detections([offset(cell, 0, 1)], land_cover="built")

    flagged, summary = flag_static_sources(
        frame, {cell}, StaticSourcesConfig(match_neighbors=False)
    )

    assert flagged["is_valid"].all()
    assert summary.flagged == 0


def test_without_cells_nothing_changes():
    frame = detections([SANTANDER])

    flagged, summary = flag_static_sources(frame, set(), CONFIG)

    assert flagged is frame
    assert summary.flagged == 0


# --- Loader ---------------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, query, params=None):
        self.log.append((query, params))

    def executemany(self, query, params_seq):
        self.log.append((query, list(params_seq)))


class FakeConnection:
    def __init__(self):
        self.log = []
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield

    @contextmanager
    def cursor(self):
        yield FakeCursor(self.log)


def test_ensure_table_creates_only_its_table():
    connection = FakeConnection()

    ensure_table(connection)

    assert connection.log == [(CREATE_TABLE_SQL, None)]
    assert "fire.detections" not in CREATE_TABLE_SQL


def test_load_replaces_the_cells_of_the_grid_in_one_transaction():
    cells = select_static_cells(pd.DataFrame([stats_row(1, 2, 681, 16, 12, 734)]), CONFIG)
    connection = FakeConnection()

    assert load_static_sources(connection, cells, CONFIG) == 1

    (delete, delete_params), (insert, rows) = connection.log
    assert connection.transactions == 1
    assert delete.startswith("DELETE FROM fire.static_sources WHERE grid_srid")
    assert delete_params == (25830, 400.0)
    assert insert == INSERT_SQL
    assert rows[0]["ix"] == 1 and rows[0]["iy"] == 2
    assert rows[0]["srid"] == 25830 and rows[0]["criterion"] == CRITERION_BOTH
    assert isinstance(rows[0]["n_days"], int)


def test_load_without_cells_keeps_the_previous_ones():
    connection = FakeConnection()
    empty = select_static_cells(pd.DataFrame(columns=list(STAT_COLUMNS)), CONFIG)

    with pytest.raises(StaticSourcesLoadError):
        load_static_sources(connection, empty, CONFIG)
    assert connection.log == []
