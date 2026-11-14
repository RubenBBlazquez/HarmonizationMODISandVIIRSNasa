"""
Datos finales (``clean_data``): el resultado depurado del pipeline, listo para usar.

Se generan cuatro ficheros pensados para sus consumidores:

- ``fire_events.geojson``: un punto (centroide) por evento. Capa principal del **mapa**.
- ``fire_event_areas.geojson``: la envolvente de cada evento (polígono), para dibujar su extensión.
- ``fire_detections.geojson``: las detecciones que forman los eventos (puntos), para el detalle.
- ``fire_events.json``: una ficha plana por evento con un resumen en texto y la serie temporal de
  FRP. Pensado para el **chatbot** (búsqueda, filtrado y respuestas).
- ``metadata.json``: generación, recuentos, parámetros, fuentes y avisos.

Solo contiene lo que sirve a quien consume el dato: sin geometrías duplicadas, sin columnas
intermedias del algoritmo y sin detecciones que no pertenezcan a ningún evento. Todas las
fechas están en UTC (``YYYY-MM-DDTHH:MM:SSZ``) y las coordenadas en EPSG:4326 (``lon, lat`` en
GeoJSON). El área es la huella de los píxeles más el buffer: **no es superficie quemada**.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point, mapping

from fire_etl.config import PipelineConfig
from fire_etl.frp_classification import CATEGORIA_ALTA, CATEGORIA_BAJA, CATEGORIA_MEDIA
from fire_etl.outputs import PipelineTables

EVENTS_GEOJSON = "fire_events.geojson"
AREAS_GEOJSON = "fire_event_areas.geojson"
DETECTIONS_GEOJSON = "fire_detections.geojson"
EVENTS_JSON = "fire_events.json"
METADATA_JSON = "metadata.json"

COORD_DECIMALS = 5
M2_PER_KM2 = 1_000_000
KG_PER_TONNE = 1000.0
SECONDS_PER_HOUR = 3600
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DISPLAY_FORMAT = "%d/%m/%Y %H:%M"

FRP_CATEGORY_BY_CLASS = {1: CATEGORIA_BAJA, 2: CATEGORIA_MEDIA, 3: CATEGORIA_ALTA}
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

# Propiedades de cada fichero (las mismas columnas, en este orden).
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
    Datos finales listos para el mapa y el chatbot.

    Attributes
    ----------
    events : pd.DataFrame
        Una fila por evento: ``EVENT_COLUMNS`` + ``frp_series`` (lista de dicts).
    areas : list
        Envolvente (polígono en EPSG:4326) de cada evento, alineada con ``events``.
    detections : pd.DataFrame
        Detecciones de los eventos (``DETECTION_COLUMNS``).
    metadata : dict[str, Any]
        Generación, recuentos, parámetros, fuentes y avisos.
    """

    events: pd.DataFrame
    areas: list
    detections: pd.DataFrame
    metadata: dict[str, Any]


def _to_python(value: Any) -> Any:
    """Convierte valores de pandas/numpy a tipos JSON (``None`` para los nulos)."""
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


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
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
    """Categoría de FRP máxima y cobertura del suelo dominante de cada evento."""
    columns = ["detection_id", "frp_clase"]
    if "land_cover" in tables.detections.columns:
        columns.append("land_cover")

    link = tables.event_detections[["event_id", "detection_id"]]
    merged = link.merge(pd.DataFrame(tables.detections[columns]), on="detection_id")
    grouped = merged.groupby("event_id")
    facts = pd.DataFrame({"frp_category": _frp_category(grouped["frp_clase"].max())})
    facts["land_cover"] = grouped["land_cover"].agg(_mode) if "land_cover" in merged else None

    return facts.reset_index()


def _summary(row: pd.Series) -> str:
    """Frase en castellano que resume el evento (para el chatbot y los popups del mapa)."""
    sensors = " + ".join(str(row["sensors"]).split(","))
    category = row["frp_category"] if isinstance(row["frp_category"], str) else "sin clasificar"
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
        text += f" Cobertura del suelo: {LAND_COVER_ES.get(row['land_cover'], row['land_cover'])}."

    return text


def _clean_series(raw: str) -> list[dict[str, Any]]:
    """Serie temporal de FRP por observación, sin ids internos y con fechas en formato ``Z``."""
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
            "frp_category": _frp_category(detections["frp_clase"]),
            "biomass_kg_s": detections["biomass_kg_s"].round(3),
            "confidence": detections["confidence_norm"],
            "daynight": detections["daynight"],
            "land_cover": land_cover,
        }
    )

    return frame.sort_values(["acq_datetime", "detection_id"], ignore_index=True)


def build_clean_data(tables: PipelineTables, config: PipelineConfig) -> CleanData:
    """
    Construye los datos finales a partir de las tablas del pipeline.

    Solo se incluyen los eventos y las detecciones que pertenecen a un evento (las
    observaciones low aisladas y el ruido por FRP no llegan aquí).

    Parameters
    ----------
    tables : PipelineTables
        Resultado de :func:`fire_etl.pipeline.process_detections`.
    config : PipelineConfig
        Configuración (CRS de almacenamiento y fuentes).

    Returns
    -------
    CleanData
        Eventos, áreas, detecciones y metadatos.
    """
    storage_crs = config.geometry.storage_crs
    events, areas = _clean_events(tables, storage_crs)
    detections = _clean_detections(tables, storage_crs)

    sources = [FIRMS_SOURCE]
    if config.land_cover.sigpac_dir:
        sources.append(SIGPAC_SOURCE)

    metadata = {
        "generated_at": datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT),
        "counts": {"fire_events": len(events), "fire_detections": len(detections)},
        "parameters": config.algorithm_parameters(),
        "sources": sources,
        "notes": list(NOTES),
    }

    return CleanData(events=events, areas=areas, detections=detections, metadata=metadata)


def _feature_collection(
    properties: pd.DataFrame, geometries: Sequence[Any], columns: Sequence[str]
) -> dict[str, Any]:
    features = [
        {"type": "Feature", "geometry": mapping(geometry), "properties": props}
        for props, geometry in zip(_records(properties.loc[:, list(columns)]), geometries)
    ]

    return {"type": "FeatureCollection", "features": features}


def _points(frame: pd.DataFrame) -> list[Point]:
    return [Point(lon, lat) for lon, lat in zip(frame["lon"], frame["lat"])]


def _dump(data: Any, path: Path, indent: int | None = None) -> None:
    """Escribe JSON compacto (los ficheros de datos son grandes); ``indent`` solo para leerlo."""
    separators = (",", ": ") if indent else (",", ":")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent, separators=separators)


def write_clean_data(clean: CleanData, directory: str | Path) -> Path:
    """
    Escribe los ficheros de ``clean_data`` en ``directory`` (se crea si no existe).

    Parameters
    ----------
    clean : CleanData
        Resultado de :func:`build_clean_data`.
    directory : str | Path
        Carpeta de salida.

    Returns
    -------
    Path
        La carpeta de salida.
    """
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    events, detections = clean.events, clean.detections

    _dump(_feature_collection(events, _points(events), EVENT_COLUMNS), output / EVENTS_GEOJSON)
    area_columns = ["event_id", "status", "frp_category", "footprint_area_km2"]
    _dump(_feature_collection(events, clean.areas, area_columns), output / AREAS_GEOJSON)
    _dump(
        _feature_collection(detections, _points(detections), DETECTION_COLUMNS),
        output / DETECTIONS_GEOJSON,
    )
    _dump(_records(events.loc[:, [*EVENT_COLUMNS, "frp_series"]]), output / EVENTS_JSON)
    _dump(clean.metadata, output / METADATA_JSON, indent=2)

    return output
