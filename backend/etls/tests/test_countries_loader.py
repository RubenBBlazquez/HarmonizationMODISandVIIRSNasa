"""Tests of the country borders loader (no database: a fake connection records the SQL)."""

from __future__ import annotations

from contextlib import contextmanager

import geopandas as gpd
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Polygon, box

from fire_etl.load.countries import (
    CREATE_TABLE_SQL,
    INSERT_SQL,
    CountriesLoadError,
    as_multipolygon,
    ensure_table,
    load_countries,
    prepare_rows,
    read_borders,
)

SQUARE = box(0, 0, 1, 1)
# Bow-tie: invalid polygon that make_valid splits into two triangles.
BOW_TIE = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])


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


def countries(**columns):
    geometry = columns.pop("geometry")
    return gpd.GeoDataFrame(columns, geometry=geometry, crs=4326)


def test_as_multipolygon_wraps_polygons_and_repairs_invalid_ones():
    assert as_multipolygon(SQUARE) == MultiPolygon([SQUARE])

    repaired = as_multipolygon(BOW_TIE)

    assert repaired is not None and repaired.is_valid and len(repaired.geoms) == 2


def test_as_multipolygon_keeps_only_the_polygons_of_a_collection():
    collection = GeometryCollection([SQUARE, LineString([(0, 0), (5, 5)])])

    assert as_multipolygon(collection) == MultiPolygon([SQUARE])
    assert as_multipolygon(LineString([(0, 0), (1, 1)])) is None
    assert as_multipolygon(Polygon()) is None


def test_prepare_rows_skips_countries_without_code_or_geometry_and_duplicates():
    frame = countries(
        code=["ESP", None, "PRT", "ESP"],
        name=["Spain", "Nowhere", "Portugal", "Spain again"],
        name_es=["España", None, float("nan"), None],
        continent=["Europe", None, "Europe", "Europe"],
        geometry=[SQUARE, SQUARE, LineString([(0, 0), (1, 1)]), box(2, 2, 3, 3)],
    )

    rows = prepare_rows(frame)

    assert [row[:4] for row in rows] == [("ESP", "Spain", "España", "Europe")]
    assert shapely.from_wkb(rows[0][4]) == MultiPolygon([SQUARE])


def test_load_countries_replaces_the_table_in_one_transaction():
    connection = FakeConnection()
    rows = [("ESP", "Spain", "España", "Europe", b"wkb")]

    assert load_countries(connection, rows) == 1

    assert connection.transactions == 1
    assert connection.log == [("DELETE FROM fire.countries", None), (INSERT_SQL, rows)]


def test_ensure_table_only_creates_the_countries_table():
    connection = FakeConnection()

    ensure_table(connection)

    assert connection.log == [(CREATE_TABLE_SQL, None)]
    assert "CREATE TABLE IF NOT EXISTS fire.countries" in CREATE_TABLE_SQL
    # No foreign keys or other tables: it works on databases with a reduced copy (Railway).
    assert "FOREIGN KEY" not in CREATE_TABLE_SQL and CREATE_TABLE_SQL.count("CREATE TABLE") == 1


def test_load_countries_refuses_to_empty_the_table():
    with pytest.raises(CountriesLoadError):
        load_countries(FakeConnection(), [])


def test_read_borders_renames_the_natural_earth_columns(tmp_path):
    path = tmp_path / "countries.gpkg"
    gpd.GeoDataFrame(
        {"ADM0_A3": ["FRA"], "NAME": ["France"], "NAME_ES": ["Francia"], "CONTINENT": ["Europe"]},
        geometry=[SQUARE],
        crs=4326,
    ).to_file(path)

    frame = read_borders(path)

    assert list(frame.columns) == ["code", "name", "name_es", "continent", "geometry"]
    assert frame.iloc[0]["code"] == "FRA"


def test_read_borders_rejects_files_without_the_expected_columns(tmp_path):
    path = tmp_path / "other.gpkg"
    gpd.GeoDataFrame({"NAME": ["France"]}, geometry=[SQUARE], crs=4326).to_file(path)

    with pytest.raises(CountriesLoadError):
        read_borders(path)
