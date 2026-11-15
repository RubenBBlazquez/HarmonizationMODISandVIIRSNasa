"""Endpoints de la API para el mapa del frontend (todos de solo lectura)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from backend.api import queries
from backend.api.db import get_connection

GEOJSON_MEDIA_TYPE = "application/geo+json"

router = APIRouter(prefix="/api", tags=["fires"])

Connection = Annotated[psycopg.Connection, Depends(get_connection)]


def get_filters(
    bbox: Annotated[
        str | None,
        Query(description="Recuadro minLon,minLat,maxLon,maxLat (EPSG:4326)."),
    ] = None,
    start: Annotated[date | None, Query(description="Desde (UTC, incluida).")] = None,
    end: Annotated[date | None, Query(description="Hasta (UTC, incluida).")] = None,
    source: Annotated[Literal["MODIS", "VIIRS"] | None, Query()] = None,
    frp_category: Annotated[
        Literal["low", "medium", "high"] | None,
        Query(description="Solo detecciones."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_LIMIT)] = queries.DEFAULT_LIMIT,
) -> queries.Filters:
    """Valida y agrupa los filtros comunes (422 si el recuadro o las fechas no son válidos)."""
    parsed_bbox = None
    if bbox is not None:
        try:
            parsed_bbox = queries.parse_bbox(bbox)
        except queries.InvalidBBoxError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start no puede ser posterior a end")

    return queries.Filters(parsed_bbox, start, end, source, frp_category, limit)


FiltersDep = Annotated[queries.Filters, Depends(get_filters)]


def _geojson(content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(content, media_type=GEOJSON_MEDIA_TYPE)


@router.get("/health")
def health(connection: Connection) -> dict[str, str]:
    """Comprueba que la API llega a la base de datos."""
    connection.execute("SELECT 1")

    return {"status": "ok"}


@router.get("/summary")
def summary(connection: Connection) -> dict[str, Any]:
    """Recuentos y rango de fechas de los datos válidos (para inicializar el mapa)."""
    return queries.fetch_summary(connection)


@router.get("/activity")
def activity(
    connection: Connection,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_ACTIVITY_LIMIT, description="Máximo de incendios.")
    ] = queries.DEFAULT_ACTIVITY_LIMIT,
) -> dict[str, Any]:
    """Actividad reciente: los últimos incendios (por detección más reciente), con su posición."""
    return queries.fetch_activity(connection, limit)


@router.get("/detections")
def detections(
    filters: FiltersDep,
    connection: Connection,
    geometry: Annotated[Literal["point", "pixel"], Query()] = "point",
) -> JSONResponse:
    """
    Detecciones **válidas** (``is_valid = true``) como GeoJSON.

    ``geometry=point`` devuelve el punto; ``geometry=pixel``, el área del píxel (``scan`` ×
    ``track`` km centrado en el punto, sin el buffer del pipeline).

    Devuelve ``count`` (elementos devueltos), ``total`` (los que cumplen el filtro) y
    ``truncated`` (si ``total`` supera ``limit``).
    """
    query = queries.detections_query(filters, geometry)

    return _geojson(queries.fetch_collection(connection, query, queries.detection_feature))


@router.get("/history")
def history(
    connection: Connection,
    bbox: Annotated[str, Query(description="Zona minLon,minLat,maxLon,maxLat (EPSG:4326).")],
    start: Annotated[date, Query(description="Inicio del periodo de referencia (incluido).")],
    end: Annotated[date, Query(description="Fin del periodo de referencia (incluido).")],
    years: Annotated[
        int, Query(ge=1, le=queries.MAX_HISTORY_YEARS, description="Años anteriores a comparar.")
    ] = queries.DEFAULT_HISTORY_YEARS,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_HISTORY_LIMIT, description="Máximo de eventos.")
    ] = queries.DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    """
    Incendios "similares" de una zona: los de la **misma zona** y las **mismas fechas del año**
    en los ``years`` años anteriores.

    Devuelve un resumen por periodo (el elegido, marcado con ``is_reference``, y cada año
    anterior) y los eventos de los años anteriores, los mayores primero.
    """
    try:
        parsed_bbox = queries.parse_bbox(bbox)
        periods = queries.history_periods(start, end, years)
    except (queries.InvalidBBoxError, queries.InvalidPeriodError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return queries.fetch_history(connection, parsed_bbox, periods, limit)


@router.get("/events/{event_id}")
def event_detail(
    event_id: str,
    connection: Connection,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_LIMIT, description="Máximo de detecciones.")
    ] = queries.DEFAULT_EVENT_DETECTIONS,
) -> dict[str, Any]:
    """
    Un incendio: su envolvente (``event``) y sus detecciones dibujadas como el píxel ``scan`` ×
    ``track`` (``detections``). 404 si no existe.
    """
    detail = queries.fetch_event_detail(connection, event_id, limit)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No existe el evento {event_id}")

    return detail


@router.get("/events")
def events(
    filters: FiltersDep,
    connection: Connection,
    geometry: Annotated[Literal["point", "area"], Query()] = "point",
    match: Annotated[
        Literal["intersects", "inside"],
        Query(description="intersects: la envolvente toca; inside: hay detecciones dentro."),
    ] = "intersects",
) -> JSONResponse:
    """
    Eventos de incendio como GeoJSON: centroide (``geometry=point``) o envolvente (``area``).

    Con ``bbox``, ``match=intersects`` (por defecto) incluye los que su envolvente toca; para una
    zona seleccionada usa ``match=inside``: solo los que tienen detecciones dentro del recuadro (y
    de las fechas), con ``zone_detections`` y el punto en el centro de esas detecciones.

    La huella (``footprint_area_km2``) no es superficie quemada.
    """
    if match == "inside" and filters.bbox is None:
        raise HTTPException(status_code=422, detail="match=inside necesita bbox")

    query = queries.events_query(filters, geometry, match)

    return _geojson(queries.fetch_collection(connection, query, queries.event_feature))
