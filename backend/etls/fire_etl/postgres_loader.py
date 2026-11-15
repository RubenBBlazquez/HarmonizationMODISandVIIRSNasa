"""
Carga de las tablas del pipeline en PostGIS, además de los GeoParquet.

El GeoParquet es el respaldo: se escribe siempre primero y, si la base de datos no está
disponible o falla la carga, se registra el error y el pipeline continúa.

Cada carga **sustituye** el contenido de las tablas por la foto actual del pipeline (``TRUNCATE``
+ ``INSERT`` en una sola transacción). El pipeline reprocesa todo el histórico en cada
ejecución y los identificadores de observación pueden cambiar al llegar datos nuevos, así que un
*upsert* dejaría filas obsoletas. La base de datos queda idéntica a ``output/``. Las tablas
siguen el esquema de ``backend/databases/postgis/init/01_schema.sql``, que se reaplica en cada
carga (es idempotente) para que las bases creadas con un esquema antiguo reciban las columnas
nuevas.
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
from fire_etl.events import EVENT_COLUMNS, EVENT_DETECTION_COLUMNS, MERGE_COLUMNS
from fire_etl.fusion import OBSERVATION_COLUMNS
from fire_etl.outputs import DETECTION_OUTPUT_COLUMNS, PipelineTables, to_storage_crs
from fire_etl.sigpac_land_cover import NEW_COLUMNS as LAND_COVER_COLUMNS

LOGGER = logging.getLogger(__name__)

SCHEMA = "fire"
SCHEMA_FILE = (
    Path(__file__).resolve().parents[2] / "databases" / "postgis" / "init" / "01_schema.sql"
)
CONNECT_TIMEOUT_S = 5
WKT_PRECISION = 7  # decimales de las coordenadas en grados (~1 cm)
INSERT_CHUNK_ROWS = 20_000  # filas por ``executemany``: permite informar del avance

# Orden de inserción compatible con las claves foráneas (detections -> observations es
# DEFERRABLE INITIALLY DEFERRED en el esquema).
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
    Filas de una tabla listas para insertar.

    Attributes
    ----------
    table : str
        Nombre de la tabla (sin esquema).
    columns : tuple[str, ...]
        Columnas, en el orden de ``rows``.
    rows : list[tuple]
        Valores ya convertidos a tipos de Python (geometrías como WKT).
    """

    table: str
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]


def describe_url(url: str) -> str:
    """``host:puerto/base`` sin credenciales, para los logs."""
    parts = urlsplit(url)

    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"


def _cell(value: Any) -> Any:
    """Convierte un valor de pandas/shapely a un tipo que psycopg sepa adaptar."""
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
    Convierte las tablas del pipeline en filas para PostGIS (función pura, sin conexión).

    Parameters
    ----------
    tables : PipelineTables
        Resultado del pipeline (geometrías en el CRS métrico).
    storage_crs : str
        CRS de almacenamiento de las geometrías (EPSG:4326).

    Returns
    -------
    list[TableLoad]
        Una entrada por tabla, en el orden de inserción.
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
    """``INSERT`` parametrizado de una tabla (geometrías desde WKT y JSON con ``::jsonb``)."""
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
    """Vacía todas las tablas a la vez (necesario por las claves foráneas)."""
    tables = ", ".join(f"{SCHEMA}.{name}" for name in reversed(TABLE_ORDER))

    return f"TRUNCATE {tables}"


def apply_schema(connection: _Connection, schema_file: Path = SCHEMA_FILE) -> bool:
    """
    Aplica el esquema SQL (idempotente). Devuelve ``False`` si el fichero no existe.
    """
    if not schema_file.exists():
        LOGGER.warning("No se encontró el esquema %s: se omite su aplicación.", schema_file)
        return False

    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(schema_file.read_text(encoding="utf-8"))

    return True


def execute_plan(connection: _Connection, plan: Sequence[TableLoad], srid: int) -> dict[str, int]:
    """
    Sustituye el contenido de las tablas por ``plan`` en una única transacción.

    Returns
    -------
    dict[str, int]
        Filas insertadas por tabla.
    """
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(truncate_statement())
        for load in plan:
            if not load.rows:
                continue

            total = len(load.rows)
            LOGGER.info("Insertando %s filas en %s.%s...", f"{total:,}", SCHEMA, load.table)
            statement = insert_statement(load, srid)
            for start in range(0, total, INSERT_CHUNK_ROWS):
                cursor.executemany(statement, load.rows[start : start + INSERT_CHUNK_ROWS])
                done = min(start + INSERT_CHUNK_ROWS, total)
                if total > INSERT_CHUNK_ROWS and done < total:
                    LOGGER.info("  %s: %s de %s filas", load.table, f"{done:,}", f"{total:,}")

    return {load.table: len(load.rows) for load in plan}


def load_to_postgres(tables: PipelineTables, config: PipelineConfig) -> bool:
    """
    Inserta las tablas del pipeline en PostGIS si ``database.enabled``.

    Cualquier error (sin driver, sin conexión, fallo de SQL) se registra y se devuelve
    ``False``: los GeoParquet ya están escritos y sirven de respaldo.

    Parameters
    ----------
    tables : PipelineTables
        Resultado del pipeline.
    config : PipelineConfig
        Configuración (``database`` y CRS de almacenamiento).

    Returns
    -------
    bool
        ``True`` si la carga se completó.
    """
    if not config.database.enabled:
        return False

    url = config.database.url
    if not url:
        LOGGER.error("database.enabled sin database.url: define la URL en la configuración.")
        return False

    try:
        import psycopg
    except ImportError:
        LOGGER.error("Falta el driver: pip install 'psycopg[binary]'. Se omite la carga.")
        return False

    srid = CRS.from_user_input(config.geometry.storage_crs).to_epsg()
    try:
        LOGGER.info("PostGIS: preparando las filas (convierte geometrías; puede tardar)...")
        plan = build_load_plan(tables, config.geometry.storage_crs)
        LOGGER.info("PostGIS: conectando a %s...", describe_url(url))
        with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S) as connection:
            if config.database.apply_schema:
                LOGGER.info("PostGIS: aplicando el esquema (crea las tablas que falten)...")
                apply_schema(connection)

            counts = execute_plan(connection, plan, srid)
    except Exception as error:  # límite del sistema: el GeoParquet es el respaldo
        LOGGER.error(
            "No se pudo cargar en PostGIS (%s): %s. Los GeoParquet sí se han escrito.",
            describe_url(url),
            error,
        )
        return False

    LOGGER.info("Cargado en PostGIS (%s): %s", describe_url(url), counts)

    return True


def iter_statements(plan: Sequence[TableLoad], srid: int) -> Iterator[str]:
    """Sentencias que ejecutaría la carga, en orden (útil para depurar)."""
    yield truncate_statement()
    for load in plan:
        yield insert_statement(load, srid)
