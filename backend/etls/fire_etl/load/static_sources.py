"""
Loading of the static thermal sources (see :mod:`fire_etl.transform.static_sources`) into
``fire.static_sources``.

It is a reference table: the pipeline load does not empty it, and reloading it replaces every
cell in one transaction. Only this table is created if missing (not the whole schema), so it also
works on a database with a reduced copy of the data, such as Railway.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

import pandas as pd

from fire_etl.config import StaticSourcesConfig
from fire_etl.transform.static_sources import STAT_COLUMNS, TABLE, grid_srid

LOGGER = logging.getLogger(__name__)

STORAGE_SRID = 4326
# Same table as in backend/databases/postgis/init/01_schema.sql.
CREATE_TABLE_SQL = f"""
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS fire;
CREATE TABLE IF NOT EXISTS {TABLE} (
    grid_srid           integer         NOT NULL,
    cell_size_m         double precision NOT NULL,
    ix                  integer         NOT NULL,
    iy                  integer         NOT NULL,
    criterion           text            NOT NULL,
    n_detections        integer         NOT NULL,
    n_days              integer         NOT NULL,
    n_years             integer         NOT NULL,
    n_months_of_year    integer         NOT NULL,
    n_firms_static      integer         NOT NULL,
    first_date          date            NOT NULL,
    last_date           date            NOT NULL,
    computed_at         timestamptz     NOT NULL DEFAULT now(),
    geom                geometry(Polygon, {STORAGE_SRID}) NOT NULL,
    PRIMARY KEY (grid_srid, cell_size_m, ix, iy)
);
CREATE INDEX IF NOT EXISTS static_sources_geom_gix ON {TABLE} USING gist (geom);
"""
# The cell polygon is built in the grid CRS and stored in EPSG:4326.
INSERT_SQL = (
    f"INSERT INTO {TABLE} (grid_srid, cell_size_m, {', '.join(STAT_COLUMNS)}, criterion, geom) "
    f"VALUES (%(srid)s, %(size)s, {', '.join(f'%({column})s' for column in STAT_COLUMNS)}, "
    f"%(criterion)s, ST_Transform(ST_MakeEnvelope(%(ix)s * %(size)s, %(iy)s * %(size)s, "
    f"(%(ix)s + 1) * %(size)s, (%(iy)s + 1) * %(size)s, %(srid)s), {STORAGE_SRID}))"
)


class StaticSourcesLoadError(RuntimeError):
    """Nothing to load."""


class _Cursor(Protocol):
    def execute(self, query: str, params: Any = None) -> Any: ...

    def executemany(self, query: str, params_seq: Sequence[Any]) -> Any: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...

    def cursor(self) -> AbstractContextManager[_Cursor]: ...


def ensure_table(connection: _Connection) -> None:
    """Creates ``fire.static_sources`` (and its index) if it does not exist."""
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(CREATE_TABLE_SQL)


def prepare_rows(cells: pd.DataFrame, config: StaticSourcesConfig) -> list[dict[str, Any]]:
    """
    Parameters of ``INSERT_SQL`` for each static cell.

    Parameters
    ----------
    cells : pd.DataFrame
        Output of :func:`fire_etl.transform.static_sources.select_static_cells`.
    config : StaticSourcesConfig
        Grid of the cells.
    """
    srid = grid_srid(config)
    size = float(config.cell_size_m)
    rows = []
    for record in cells.to_dict("records"):
        row = {column: record[column] for column in STAT_COLUMNS}
        for column in STAT_COLUMNS[:7]:
            row[column] = int(row[column])

        row.update(srid=srid, size=size, criterion=record["criterion"])
        rows.append(row)

    return rows


def load_static_sources(
    connection: _Connection, cells: pd.DataFrame, config: StaticSourcesConfig
) -> int:
    """
    Replaces the cells of the configured grid in ``fire.static_sources`` in one transaction.

    Cells of other grids (another CRS or size) are kept.

    Returns
    -------
    int
        Number of cells loaded.

    Raises
    ------
    StaticSourcesLoadError
        If there are no cells (the previous ones are kept).
    """
    rows = prepare_rows(cells, config)
    if not rows:
        raise StaticSourcesLoadError("No static cells to load: the previous ones are kept")

    srid = grid_srid(config)
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            f"DELETE FROM {TABLE} WHERE grid_srid = %s AND cell_size_m = %s",
            (srid, float(config.cell_size_m)),
        )
        cursor.executemany(INSERT_SQL, rows)

    LOGGER.info("Loaded %s static cells into %s (EPSG:%s)", len(rows), TABLE, srid)

    return len(rows)
