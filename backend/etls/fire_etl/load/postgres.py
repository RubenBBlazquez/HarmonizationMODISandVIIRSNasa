"""
Loading of the pipeline tables into PostGIS, in addition to the GeoParquet files.

The GeoParquet is the backup: it is always written first and, if the database is unavailable
or the load fails, the error is logged and the pipeline continues.

Each load **replaces** the content of the tables with the current snapshot of the pipeline
(``TRUNCATE`` + ``INSERT`` in a single transaction). The pipeline reprocesses the whole history
on every run and observation identifiers may change when new data arrives, so an *upsert* would
leave stale rows. The database ends up identical to ``output/``. The tables follow the schema
of ``backend/databases/postgis/init/01_schema.sql``, which is reapplied on every load (it is
idempotent) so that databases created with an old schema receive the new columns.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from fire_etl.config import PipelineConfig
from fire_etl.load.geoparquet import DETECTION_OUTPUT_COLUMNS, to_storage_crs
from fire_etl.transform.processing.events import (
    EVENT_COLUMNS,
    EVENT_DETECTION_COLUMNS,
    MERGE_COLUMNS,
)
from fire_etl.transform.processing.fusion import OBSERVATION_COLUMNS
from fire_etl.transform.sigpac_land_cover import NEW_COLUMNS as LAND_COVER_COLUMNS
from fire_etl.transform.tables import PipelineTables

LOGGER = logging.getLogger(__name__)

SCHEMA = "fire"
SCHEMA_FILE = (
    Path(__file__).resolve().parents[3] / "databases" / "postgis" / "init" / "01_schema.sql"
)
CONNECT_TIMEOUT_S = 5
WKT_PRECISION = 7  # decimal places of the coordinates in degrees (~1 cm)
INSERT_CHUNK_ROWS = 20_000  # rows per ``executemany``: allows reporting progress

# Insertion order compatible with the foreign keys (detections -> observations is
# DEFERRABLE INITIALLY DEFERRED in the schema).
TABLE_ORDER = ("fire_events", "detections", "observations", "event_detections", "event_merges")
GEOMETRY_COLUMNS = frozenset({"geom_point", "geom_footprint", "geom_centroid", "geom_hull"})
JSON_COLUMNS = frozenset({"frp_series", "merged_event_ids", "params"})


class _Cursor(Protocol):
    def execute(self, query: str) -> Any: ...

    def executemany(self, query: str, params: Sequence[Sequence[Any]]) -> Any: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[Any]: ...

    def cursor(self) -> AbstractContextManager[_Cursor]: ...


@dataclass(frozen=True)
class TableLoad:
    """
    Rows of a table ready to insert.

    Attributes
    ----------
    table : str
        Table name (without schema).
    columns : tuple[str, ...]
        Columns, in the order of ``rows``.
    rows : list[tuple]
        Values already converted to Python types (geometries as WKT).
    """

    table: str
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


def describe_url(url: str) -> str:
    """``host:port/database`` without credentials, for the logs."""
    parts = urlsplit(url)

    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def _cell(value: Any) -> Any:
    """Converts a pandas/shapely value to a type psycopg knows how to adapt."""
    if isinstance(value, shapely.Geometry):
        return shapely.to_wkt(value, rounding_precision=WKT_PRECISION, trim=True)

    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return None

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    if isinstance(value, np.generic):
        return value.item()

    return value


def _rows(frame: pd.DataFrame, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    if frame.empty:
        return []

    values = [[_cell(value) for value in frame[column]] for column in columns]

    return list(zip(*values))


def _table(frame: pd.DataFrame, table: str, columns: Sequence[str], crs: str) -> TableLoad:
    subset = frame.loc[:, list(columns)]
    if any(column in GEOMETRY_COLUMNS for column in columns):
        subset = to_storage_crs(subset, crs)

    return TableLoad(table=table, columns=tuple(columns), rows=_rows(subset, columns))


def build_load_plan(tables: PipelineTables, storage_crs: str) -> list[TableLoad]:
    """
    Converts the pipeline tables into rows for PostGIS (pure function, no connection).

    Parameters
    ----------
    tables : PipelineTables
        Pipeline result (geometries in the metric CRS).
    storage_crs : str
        Storage CRS of the geometries (EPSG:4326).

    Returns
    -------
    list[TableLoad]
        One entry per table, in insertion order.
    """
    land_cover = [name for name in LAND_COVER_COLUMNS if name in tables.detections.columns]
    *attributes, geom_point, geom_footprint = DETECTION_OUTPUT_COLUMNS
    detection_columns = [*attributes, *land_cover, geom_point, geom_footprint]
    plan = {
        "fire_events": _table(tables.events, "fire_events", EVENT_COLUMNS, storage_crs),
        "detections": _table(tables.detections, "detections", detection_columns, storage_crs),
        "observations": _table(
            tables.observations, "observations", [*OBSERVATION_COLUMNS, "event_id"], storage_crs
        ),
        "event_detections": _table(
            tables.event_detections, "event_detections", EVENT_DETECTION_COLUMNS, storage_crs
        ),
        "event_merges": _table(tables.merges, "event_merges", MERGE_COLUMNS, storage_crs),
    }

    return [plan[name] for name in TABLE_ORDER]


def insert_statement(load: TableLoad, srid: int) -> str:
    """Parameterized ``INSERT`` of a table (geometries from WKT and JSON with ``::jsonb``)."""
    placeholders = []
    for column in load.columns:
        if column in GEOMETRY_COLUMNS:
            placeholders.append(f"ST_GeomFromText(%s, {srid})")
        elif column in JSON_COLUMNS:
            placeholders.append("%s::jsonb")
        else:
            placeholders.append("%s")

    return (
        f"INSERT INTO {SCHEMA}.{load.table} ({', '.join(load.columns)}) "
        f"VALUES ({', '.join(placeholders)})"
    )


def truncate_statement() -> str:
    """Empties all tables at once (needed because of the foreign keys)."""
    tables = ", ".join(f"{SCHEMA}.{name}" for name in reversed(TABLE_ORDER))

    return f"TRUNCATE {tables}"


def apply_schema(connection: _Connection, schema_file: Path = SCHEMA_FILE) -> bool:
    """
    Applies the SQL schema (idempotent). Returns ``False`` if the file does not exist.
    """
    if not schema_file.exists():
        LOGGER.warning("Schema %s not found: skipping its application.", schema_file)
        return False

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(schema_file.read_text(encoding="utf-8"))

    return True


def execute_plan(connection: _Connection, plan: Sequence[TableLoad], srid: int) -> dict[str, int]:
    """
    Replaces the content of the tables with ``plan`` in a single transaction.

    Returns
    -------
    dict[str, int]
        Rows inserted per table.
    """
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(truncate_statement())
        for load in plan:
            if not load.rows:
                continue

            total = len(load.rows)
            LOGGER.info("Inserting %s rows into %s.%s...", f"{total:,}", SCHEMA, load.table)
            statement = insert_statement(load, srid)
            for start in range(0, total, INSERT_CHUNK_ROWS):
                cursor.executemany(statement, load.rows[start : start + INSERT_CHUNK_ROWS])
                done = min(start + INSERT_CHUNK_ROWS, total)
                if total > INSERT_CHUNK_ROWS and done < total:
                    LOGGER.info("  %s: %s of %s rows", load.table, f"{done:,}", f"{total:,}")

    return {load.table: len(load.rows) for load in plan}


def load_to_postgres(tables: PipelineTables, config: PipelineConfig) -> bool:
    """
    Inserts the pipeline tables into PostGIS if ``database.enabled``.

    Any error (no driver, no connection, SQL failure) is logged and ``False`` is returned: the
    GeoParquet files are already written and serve as a backup.

    Parameters
    ----------
    tables : PipelineTables
        Pipeline result.
    config : PipelineConfig
        Configuration (``database`` and storage CRS).

    Returns
    -------
    bool
        ``True`` if the load completed.
    """
    if not config.database.enabled:
        return False

    url = config.database.url
    if not url:
        LOGGER.error("database.enabled without database.url: set the URL in the configuration.")
        return False

    try:
        import psycopg
    except ImportError:
        LOGGER.error("Missing driver: pip install 'psycopg[binary]'. Skipping the load.")
        return False

    srid = CRS.from_user_input(config.geometry.storage_crs).to_epsg()
    try:
        LOGGER.info(
            "PostGIS: preparing the rows (converting geometries; this may take a while)..."
        )
        plan = build_load_plan(tables, config.geometry.storage_crs)
        LOGGER.info("PostGIS: connecting to %s...", describe_url(url))
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
            if config.database.apply_schema:
                LOGGER.info("PostGIS: applying the schema (creates any missing tables)...")
                apply_schema(connection)

            counts = execute_plan(connection, plan, srid)
    except Exception as error:  # system boundary: the GeoParquet is the backup
        LOGGER.error(
            "Could not load into PostGIS (%s): %s. The GeoParquet files were written.",
            describe_url(url),
            error,
        )
        return False

    LOGGER.info("Loaded into PostGIS (%s): %s", describe_url(url), counts)

    return True


def iter_statements(plan: Sequence[TableLoad], srid: int) -> Iterator[str]:
    """Statements the load would run, in order (useful for debugging)."""
    yield truncate_statement()
    for load in plan:
        yield insert_statement(load, srid)
