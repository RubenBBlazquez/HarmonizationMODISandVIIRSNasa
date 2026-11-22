"""
Writing and reading of the pipeline outputs as GeoParquet.

The tables reproduce the planned PostGIS schema
(``backend/databases/postgis/init/01_schema.sql``):

- ``detections.parquet``: ``geom_point`` (Point) and ``geom_footprint`` (Polygon).
- ``observations.parquet``: ``geom_centroid`` (Point) and ``geom_hull`` (Polygon).
- ``fire_events.parquet``: ``geom_centroid`` (Point) and ``geom_hull`` (Polygon).
- ``event_detections.parquet`` and ``event_merges.parquet``: tables without geometry.

All geometries are written in the storage CRS (EPSG:4326); the areas (``*_area_m2``) were
computed beforehand in the metric CRS. The footprint area is **not** burned area: it is the
footprint of the detected pixels plus the buffer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from fire_etl.transform.firms.normalization import DETECTION_COLUMNS
from fire_etl.transform.processing.events import MERGE_COLUMNS, PreviousState
from fire_etl.transform.sigpac_land_cover import NEW_COLUMNS as LAND_COVER_COLUMNS
from fire_etl.transform.tables import PipelineTables

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


def to_storage_crs(frame: gpd.GeoDataFrame, crs: str) -> gpd.GeoDataFrame:
    """
    Reprojects all geometry columns of ``frame`` to the storage CRS.

    Parameters
    ----------
    frame : gpd.GeoDataFrame
        Table with one or several geometry columns.
    crs : str
        Target CRS (``EPSG:4326``).

    Returns
    -------
    gpd.GeoDataFrame
        Reprojected copy.
    """
    stored = frame.copy()
    geometry_columns = stored.columns[stored.dtypes == "geometry"]
    for column in geometry_columns:
        stored[column] = gpd.GeoSeries(stored[column]).to_crs(crs)

    return stored.set_crs(crs, allow_override=True)


def write_tables(tables: PipelineTables, directory: str | Path, storage_crs: str) -> Path:
    """
    Writes the tables into ``directory``.

    Parameters
    ----------
    tables : PipelineTables
        Pipeline result.
    directory : str | Path
        Output directory (created if it does not exist).
    storage_crs : str
        Storage CRS of the geometries.

    Returns
    -------
    Path
        Output directory.
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
    Reads the state of a previous run, if it exists.

    Parameters
    ----------
    directory : str | Path
        Output directory of the previous run.

    Returns
    -------
    PreviousState | None
        Previous state, or ``None`` if the required tables are missing.
    """
    output = Path(directory)
    required = (DETECTIONS_FILE, EVENTS_FILE, EVENT_DETECTIONS_FILE)
    if not all((output / name).exists() for name in required):
        return None

    stored_columns = set(pq.read_schema(output / DETECTIONS_FILE).names)
    missing = set(DETECTION_COLUMNS) - stored_columns
    if missing:
        LOGGER.warning(
            "The previous state in %s has an old schema (missing %s): ignoring it.",
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
