"""
Final data (``clean_data``): the cleaned result of the pipeline, ready to use.

They are built here and :mod:`fire_etl.load.clean_data` writes them. The files are meant for
their consumers:

- ``fire_events.geojson``: one point (centroid) per event. Main layer of the **map**.
- ``fire_event_areas.geojson``: the envelope of each event (polygon), to draw its extent.
- ``fire_detections.geojson``: the detections that make up the events (points), for the detail.
- ``fire_events.json``: a flat record per event with a text summary and the FRP time series.
  Meant for the **chatbot** (search, filtering and answers).
- ``metadata.json``: generation, counts, parameters, sources and warnings.

It only contains what is useful to whoever consumes the data: no duplicated geometries, no
intermediate columns of the algorithm and no detections that do not belong to any event. All
dates are in UTC (``YYYY-MM-DDTHH:MM:SSZ``) and coordinates in EPSG:4326 (``lon, lat`` in
GeoJSON). The area is the footprint of the pixels plus the buffer: **it is not burned area**.

The text values of the output (event summary, notes, labels) are intentionally in Spanish: they
are content for the end users, not documentation of the code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from fire_etl.config import PipelineConfig
from fire_etl.transform.processing.frp_classification import (
    CATEGORY_HIGH,
    CATEGORY_LOW,
    CATEGORY_MEDIUM,
)
from fire_etl.transform.tables import PipelineTables

COORD_DECIMALS = 5
M2_PER_KM2 = 1_000_000
KG_PER_TONNE = 1000.0
SECONDS_PER_HOUR = 3600
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

FRP_CATEGORY_ES = {"low": "baja", "medium": "media", "high": "alta"}
FRP_CATEGORY_BY_CLASS = {1: CATEGORY_LOW, 2: CATEGORY_MEDIUM, 3: CATEGORY_HIGH}
STATUS_ES = {"active": "activo", "closed": "cerrado"}
CONFIDENCE_ES = {"low": "baja", "medium": "media", "high": "alta", "very_high": "muy alta"}
LAND_COVER_ES = {
    "crops": "cultivos",
    "trees": "forestal / arbolado",
    "shrub_and_scrub": "matorral",
    "grass": "pastizal",
    "built": "zona urbana",
    "bare": "suelo desnudo",
    "water": "agua",
    "flooded_vegetation": "vegetación inundada",
    "other": "otros usos",
}

# Properties of each file (the same columns, in this order).
EVENT_COLUMNS = (
    "event_id",
    "status",
    "first_detection",
    "last_detection",
    "duration_hours",
    "lat",
    "lon",
    "n_detections",
    "sensors",
    "frp_max_mw",
    "frp_category",
    "biomass_max_kg_s",
    "biomass_burned_t",
    "n_passes",
    "max_pass_gap_hours",
    "confidence",
    "footprint_area_km2",
    "land_cover",
    "is_agricultural",
    "is_forest",
    "summary",
)
DETECTION_COLUMNS = (
    "detection_id",
    "event_id",
    "acq_datetime",
    "source",
    "satellite",
    "lat",
    "lon",
    "frp_mw",
    "frp_category",
    "biomass_kg_s",
    "confidence",
    "daynight",
    "land_cover",
)
NOTES = (
    "footprint_area_km2 es la huella de los píxeles detectados más el buffer: NO es superficie "
    "quemada.",
    "biomass_*_kg_s es una tasa de combustión (FRP en MW x coeficiente en kg/MJ), no una masa "
    "total.",
    "Fechas en UTC; coordenadas en EPSG:4326.",
)
FIRMS_SOURCE = "NASA FIRMS (MODIS y VIIRS)"
SIGPAC_SOURCE = "Land cover: SIGPAC (FEGA / Ministerio de Agricultura, Pesca y Alimentación)"


@dataclass(frozen=True)
class CleanData:
    """
    Final data ready for the map and the chatbot.

    Attributes
    ----------
    events : pd.DataFrame
        One row per event: ``EVENT_COLUMNS`` + ``frp_series`` (list of dicts).
    areas : list
        Envelope (polygon in EPSG:4326) of each event, aligned with ``events``.
    detections : pd.DataFrame
        Detections of the events (``DETECTION_COLUMNS``).
    metadata : dict[str, Any]
        Generation, counts, parameters, sources and warnings.
    """

    events: pd.DataFrame
    areas: list
    detections: pd.DataFrame
    metadata: dict[str, Any]


def _to_python(value: Any) -> Any:
    """Converts pandas/numpy values to JSON types (``None`` for nulls)."""
    if isinstance(value, (list, dict)):
        return value

    if pd.isna(value):
        return None

    if isinstance(value, pd.Timestamp):
        utc = value.tz_convert("UTC") if value.tzinfo else value
        return utc.strftime(TIMESTAMP_FORMAT)

    if isinstance(value, np.generic):
        return value.item()

    return value


def to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Converts a DataFrame into a list of dicts with JSON types."""
    return [
        {key: _to_python(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _mode(values: pd.Series) -> Any:
    present = values.dropna()

    return present.mode().iloc[0] if not present.empty else None


def _frp_category(classes: pd.Series) -> pd.Series:
    return classes.astype("float").map(FRP_CATEGORY_BY_CLASS)


def _event_detection_facts(tables: PipelineTables) -> pd.DataFrame:
    """Maximum FRP category and dominant land cover of each event."""
    columns = ["detection_id", "frp_class"]
    if "land_cover" in tables.detections.columns:
        columns.append("land_cover")

    link = tables.event_detections[["event_id", "detection_id"]]
    merged = link.merge(pd.DataFrame(tables.detections[columns]), on="detection_id")
    grouped = merged.groupby("event_id")
    facts = pd.DataFrame({"frp_category": _frp_category(grouped["frp_class"].max())})
    facts["land_cover"] = grouped["land_cover"].agg(_mode) if "land_cover" in merged else None

    return facts.reset_index()


def _summary(row: pd.Series) -> str:
    """Sentence in Spanish that summarizes the event (for the chatbot and the map popups)."""
    sensors = " + ".join(str(row["sensors"]).split(","))
    category = FRP_CATEGORY_ES.get(row["frp_category"], "sin clasificar")
    text = (
        f"Incendio {STATUS_ES.get(row['status'], row['status'])} detectado por {sensors} "
        f"en torno a ({row['lat']:.4f}, {row['lon']:.4f}), entre "
        f"{row['first_detection']:{DISPLAY_FORMAT}} y {row['last_detection']:{DISPLAY_FORMAT}} "
        f"UTC ({row['duration_hours']:g} h). Intensidad {category} "
        f"(FRP máximo {row['frp_max_mw']:.1f} MW), confianza "
        f"{CONFIDENCE_ES.get(row['confidence'], row['confidence'])}. "
        f"{row['n_detections']} detecciones en {row['n_passes']} pasadas de satélite; huella de "
        f"{row['footprint_area_km2']:.2f} km² (no es superficie quemada). Biomasa quemada "
        f"estimada: {row['biomass_burned_t']:g} t."
    )
    if isinstance(row["land_cover"], str):
        covers = [LAND_COVER_ES.get(part, part) for part in row["land_cover"].split("|")]
        text += f" Cobertura del suelo: {' / '.join(covers)}."

    return text


def _clean_series(raw: str) -> list[dict[str, Any]]:
    """FRP time series per observation, without internal ids and with dates in ``Z`` format."""
    return [
        {
            "window_start": pd.Timestamp(item["window_start"]).strftime(TIMESTAMP_FORMAT),
            "n_detections": item["n_detections"],
            "frp_total": round(item["frp_total"], 2),
            "frp_max": round(item["frp_max"], 2),
            "biomass_total_kg_s": round(item["biomass_total_kg_s"], 3),
        }
        for item in json.loads(raw)
    ]


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=[*EVENT_COLUMNS, "frp_series"])


def _clean_events(tables: PipelineTables, storage_crs: str) -> tuple[pd.DataFrame, list]:
    if tables.events.empty:
        return _empty_events(), []

    events = tables.events.reset_index(drop=True)
    centroids = gpd.GeoSeries(events["geom_centroid"], crs=events.crs).to_crs(storage_crs)
    hulls = events.geometry.to_crs(storage_crs)
    first, last = events["first_detection"], events["last_detection"]

    frame = pd.DataFrame(
        {
            "event_id": events["event_id"],
            "status": events["status"],
            "first_detection": first,
            "last_detection": last,
            "duration_hours": ((last - first).dt.total_seconds() / SECONDS_PER_HOUR).round(2),
            "lat": centroids.y.round(COORD_DECIMALS),
            "lon": centroids.x.round(COORD_DECIMALS),
            "n_detections": events["n_detections"],
            "sensors": events["sensors"],
            "frp_max_mw": events["frp_max"].round(2),
            "biomass_max_kg_s": events["biomass_max_kg_s"].round(3),
            "biomass_burned_t": (events["biomass_burned_kg"] / KG_PER_TONNE).round(1),
            "n_passes": events["n_passes"],
            "max_pass_gap_hours": events["max_pass_gap_hours"].round(1),
            "confidence": events["confidence_fused"],
            "footprint_area_km2": (events["hull_area_m2"] / M2_PER_KM2).round(3),
        }
    )
    frame = frame.merge(_event_detection_facts(tables), on="event_id", how="left")

    land_cover = frame["land_cover"].astype("object")
    frame["is_agricultural"] = land_cover.map(lambda v: None if pd.isna(v) else v == "crops")
    frame["is_forest"] = land_cover.map(lambda v: None if pd.isna(v) else v == "trees")
    frame["summary"] = frame.apply(_summary, axis=1)
    frame["frp_series"] = events["frp_series"].map(_clean_series)

    frame = frame.loc[:, [*EVENT_COLUMNS, "frp_series"]]
    order = frame["last_detection"].sort_values(ascending=False, kind="stable").index
    areas = list(hulls)

    return frame.loc[order].reset_index(drop=True), [areas[i] for i in order]


def _clean_detections(tables: PipelineTables, storage_crs: str) -> pd.DataFrame:
    link = tables.event_detections[["detection_id", "event_id"]]
    detections = tables.detections.merge(link, on="detection_id", how="inner")
    if detections.empty:
        return pd.DataFrame(columns=list(DETECTION_COLUMNS))

    points = gpd.GeoSeries(detections["geom_point"], crs=detections.crs).to_crs(storage_crs)
    land_cover = detections["land_cover"] if "land_cover" in detections else None
    frame = pd.DataFrame(
        {
            "detection_id": detections["detection_id"],
            "event_id": detections["event_id"],
            "acq_datetime": detections["acq_datetime"],
            "source": detections["source"],
            "satellite": detections["satellite"],
            "lat": points.y.round(COORD_DECIMALS),
            "lon": points.x.round(COORD_DECIMALS),
            "frp_mw": detections["frp"].round(2),
            "frp_category": _frp_category(detections["frp_class"]),
            "biomass_kg_s": detections["biomass_kg_s"].round(3),
            "confidence": detections["confidence_norm"],
            "daynight": detections["daynight"],
            "land_cover": land_cover,
        }
    )

    return frame.sort_values(["acq_datetime", "detection_id"], ignore_index=True)


def build_clean_data(tables: PipelineTables, config: PipelineConfig) -> CleanData:
    """
    Builds the final data from the pipeline tables.

    Only the events and the detections that belong to an event are included (isolated low
    observations and FRP noise do not get here).

    Parameters
    ----------
    tables : PipelineTables
        Result of :func:`fire_etl.transform.main.process_detections`.
    config : PipelineConfig
        Configuration (storage CRS and sources).

    Returns
    -------
    CleanData
        Events, areas, detections and metadata.
    """
    storage_crs = config.geometry.storage_crs
    events, areas = _clean_events(tables, storage_crs)
    detections = _clean_detections(tables, storage_crs)

    sources = [FIRMS_SOURCE]
    if config.land_cover.use_database or config.land_cover.sigpac_dir:
        sources.append(SIGPAC_SOURCE)

    metadata = {
        "generated_at": datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT),
        "counts": {"fire_events": len(events), "fire_detections": len(detections)},
        "parameters": config.algorithm_parameters(),
        "sources": sources,
        "notes": list(NOTES),
    }

    return CleanData(events=events, areas=areas, detections=detections, metadata=metadata)
