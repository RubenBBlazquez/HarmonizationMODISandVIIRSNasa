"""
Escritura y lectura de las salidas del pipeline en GeoParquet.

Las tablas reproducen el esquema PostGIS previsto
(``backend/databases/postgis/init/01_schema.sql``):

- ``detections.parquet``: ``geom_point`` (Point) y ``geom_footprint`` (Polygon).
- ``observations.parquet``: ``geom_centroid`` (Point) y ``geom_hull`` (Polygon).
- ``fire_events.parquet``: ``geom_centroid`` (Point) y ``geom_hull`` (Polygon).
- ``event_detections.parquet`` y ``event_merges.parquet``: tablas sin geometría.

Todas las geometrías se escriben en el CRS de almacenamiento (EPSG:4326); las áreas
(``*_area_m2``) se calcularon antes en el CRS métrico. El área de los footprints **no** es
superficie quemada: es la huella de los píxeles detectados más el buffer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from fire_etl.events import MERGE_COLUMNS, PreviousState
from fire_etl.ingest import DETECTION_COLUMNS
from fire_etl.sigpac_land_cover import NEW_COLUMNS as LAND_COVER_COLUMNS

LOGGER = logging.getLogger(__name__)

DETECTIONS_FILE = "detections.parquet"
OBSERVATIONS_FILE = "observations.parquet"
EVENTS_FILE = "fire_events.parquet"
EVENT_DETECTIONS_FILE = "event_detections.parquet"
MERGES_FILE = "event_merges.parquet"
DISCARDS_FILE = "ingest_discards.csv"
RUN_FILE = "run_parameters.json"

DETECTION_OUTPUT_COLUMNS = (
    *DETECTION_COLUMNS,
    "observation_id",
    "is_representative",
    "footprint_area_m2",
    "geom_point",
    "geom_footprint",
)


@dataclass(frozen=True)
class PipelineTables:
    """
    Tablas finales del pipeline (geometrías en el CRS métrico).

    Attributes
    ----------
    detections, observations, events : gpd.GeoDataFrame
        Capas con geometría.
    event_detections, merges, discarded : pd.DataFrame
        Tablas sin geometría.
    run_parameters : dict[str, Any]
        Parámetros y métricas de la ejecución.
    """

    detections: gpd.GeoDataFrame
    observations: gpd.GeoDataFrame
    events: gpd.GeoDataFrame
    event_detections: pd.DataFrame
    merges: pd.DataFrame
    discarded: pd.DataFrame
    run_parameters: dict[str, Any]


def to_storage_crs(frame: gpd.GeoDataFrame, crs: str) -> gpd.GeoDataFrame:
    """
    Reproyecta todas las columnas de geometría de ``frame`` al CRS de almacenamiento.

    Parameters
    ----------
    frame : gpd.GeoDataFrame
        Tabla con una o varias columnas de geometría.
    crs : str
        CRS destino (``EPSG:4326``).

    Returns
    -------
    gpd.GeoDataFrame
        Copia reproyectada.
    """
    stored = frame.copy()
    geometry_columns = stored.columns[stored.dtypes == "geometry"]
    for column in geometry_columns:
        stored[column] = gpd.GeoSeries(stored[column]).to_crs(crs)

    return stored.set_crs(crs, allow_override=True)


def write_tables(tables: PipelineTables, directory: str | Path, storage_crs: str) -> Path:
    """
    Escribe las tablas en ``directory``.

    Parameters
    ----------
    tables : PipelineTables
        Resultado del pipeline.
    directory : str | Path
        Directorio de salida (se crea si no existe).
    storage_crs : str
        CRS de almacenamiento de las geometrías.

    Returns
    -------
    Path
        Directorio de salida.
    """
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)

    land_cover = [name for name in LAND_COVER_COLUMNS if name in tables.detections.columns]
    detections = tables.detections.loc[:, [*DETECTION_OUTPUT_COLUMNS, *land_cover]]
    to_storage_crs(detections, storage_crs).to_parquet(output / DETECTIONS_FILE, index=False)
    to_storage_crs(tables.observations, storage_crs).to_parquet(
        output / OBSERVATIONS_FILE, index=False
    )
    to_storage_crs(tables.events, storage_crs).to_parquet(output / EVENTS_FILE, index=False)

    tables.event_detections.to_parquet(output / EVENT_DETECTIONS_FILE, index=False)
    tables.merges.loc[:, list(MERGE_COLUMNS)].to_parquet(output / MERGES_FILE, index=False)
    tables.discarded.to_csv(output / DISCARDS_FILE, index=False)

    with (output / RUN_FILE).open("w", encoding="utf-8") as handle:
        json.dump(tables.run_parameters, handle, indent=2, sort_keys=True, default=str)

    return output


def read_previous_state(directory: str | Path) -> PreviousState | None:
    """
    Lee el estado de una ejecución anterior, si existe.

    Parameters
    ----------
    directory : str | Path
        Directorio de salida de la ejecución anterior.

    Returns
    -------
    PreviousState | None
        Estado previo o ``None`` si faltan las tablas necesarias.
    """
    output = Path(directory)
    required = (DETECTIONS_FILE, EVENTS_FILE, EVENT_DETECTIONS_FILE)
    if not all((output / name).exists() for name in required):
        return None

    stored_columns = set(pq.read_schema(output / DETECTIONS_FILE).names)
    missing = set(DETECTION_COLUMNS) - stored_columns
    if missing:
        LOGGER.warning(
            "El estado previo en %s tiene un esquema antiguo (faltan %s): se ignora.",
            output,
            sorted(missing),
        )
        return None

    detections = pd.read_parquet(output / DETECTIONS_FILE, columns=list(DETECTION_COLUMNS))
    events = pd.read_parquet(output / EVENTS_FILE, columns=["event_id", "first_detection"])
    event_detections = pd.read_parquet(output / EVENT_DETECTIONS_FILE)

    merges_path = output / MERGES_FILE
    merges = (
        pd.read_parquet(merges_path)
        if merges_path.exists()
        else pd.DataFrame(columns=list(MERGE_COLUMNS))
    )

    return PreviousState(
        detections=detections,
        event_detections=event_detections,
        events=events,
        merges=merges,
    )
