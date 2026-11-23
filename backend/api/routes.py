"""Endpoints de la API para el mapa del frontend (todos de solo lectura)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import date
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from backend.api import queries
from backend.api.cache import TTLCache
from backend.api.db import get_connection

LOGGER = logging.getLogger(__name__)

GEOJSON_MEDIA_TYPE = "application/geo+json"
YEARLY_CACHE_TTL_S = 3600  # los datos solo cambian cuando corre el ETL
_yearly_cache = TTLCache(YEARLY_CACHE_TTL_S)
COUNTRIES_CACHE_TTL_S = 3600
SUMMARY_CACHE_TTL_S = 300
_summary_cache = TTLCache(SUMMARY_CACHE_TTL_S, max_entries=1)
HISTORY_CACHE_TTL_S = 600
_history_cache = TTLCache(HISTORY_CACHE_TTL_S, max_entries=64)
# El navegador reutiliza durante unos minutos lo ya descargado (recargar la página no repite las
# consultas grandes). Los datos solo cambian cuando corre el ETL.
BROWSER_CACHE_S = 300
_countries_cache = TTLCache(COUNTRIES_CACHE_TTL_S, max_entries=1)

router = APIRouter(prefix="/api", tags=["fires"])

# --- Compatibilidad con bases sin migrar --------------------------------------------------------
# La columna fire_events.land_cover la añade la migración de 01_schema.sql. Si la base aún no la
# tiene (p. ej. Railway antes de migrar), las consultas la piden como NULL en vez de fallar: el mapa
# funciona y el tipo de zona sale vacío. Se vuelve a comprobar cada pocos minutos, así que tras
# migrar se empieza a usar sin reiniciar la API.
SCHEMA_CHECK_TTL_S = 300
_schema_cache = TTLCache(SCHEMA_CHECK_TTL_S, max_entries=4)
LAND_COVER_COLUMN_SQL = (
    "SELECT 1 FROM information_schema.columns WHERE table_schema = 'fire' "
    "AND table_name = 'fire_events' AND column_name = 'land_cover'"
)
MISSING_LAND_COVER_SQL = "NULL::text AS land_cover"


def has_event_land_cover(connection: Any) -> bool:
    """``True`` si la base tiene ``fire.fire_events.land_cover`` (se cachea unos minutos)."""

    def check() -> bool:
        present = connection.execute(LAND_COVER_COLUMN_SQL).fetchone() is not None
        if not present:
            LOGGER.warning(
                "fire.fire_events.land_cover is missing: apply the migration in 01_schema.sql. "
                "Serving events without land type meanwhile."
            )
        return present

    return bool(_schema_cache.get_or_compute("event_land_cover", check))


class SchemaCompatConnection:
    """Conexión que adapta las consultas a una base sin la columna ``land_cover`` en eventos."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, query: str, params: Any = None) -> Any:
        if queries.EVENT_LAND_COVER_SQL in query and not has_event_land_cover(self._connection):
            query = query.replace(queries.EVENT_LAND_COVER_SQL, MISSING_LAND_COVER_SQL)
        return self._connection.execute(query, params)


def get_api_connection() -> Iterator[SchemaCompatConnection]:
    """Dependencia de FastAPI: conexión por petición, tolerante a bases sin migrar."""
    connections = get_connection()
    try:
        yield SchemaCompatConnection(next(connections))
    finally:
        connections.close()  # cierra la conexión real al acabar la petición


Connection = Annotated[SchemaCompatConnection, Depends(get_api_connection)]


def get_filters(
    bbox: Annotated[
        str | None,
        Query(description="Box minLon,minLat,maxLon,maxLat (EPSG:4326)."),
    ] = None,
    start: Annotated[date | None, Query(description="From (UTC, inclusive).")] = None,
    end: Annotated[date | None, Query(description="To (UTC, inclusive).")] = None,
    source: Annotated[Literal["MODIS", "VIIRS"] | None, Query()] = None,
    frp_category: Annotated[
        Literal["low", "medium", "high"] | None,
        Query(description="Detections only."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=queries.MAX_LIMIT)] = queries.DEFAULT_LIMIT,
    country: Annotated[
        str | None,
        Query(
            pattern=r"^[A-Z0-9]{3}$",
            description="Country (ADM0_A3 code from /countries: ESP, PRT...).",
        ),
    ] = None,
    all_years: Annotated[
        bool,
        Query(
            description=(
                "Returns the same dates of the start-end range in every year "
                "(needs start and end)."
            )
        ),
    ] = False,
) -> queries.Filters:
    """Valida y agrupa los filtros comunes (422 si el recuadro o las fechas no son válidos)."""
    parsed_bbox = None
    if bbox is not None:
        try:
            parsed_bbox = queries.parse_bbox(bbox)
        except queries.InvalidBBoxError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start cannot be later than end")

    window = None
    if all_years:
        if start is None or end is None:
            raise HTTPException(status_code=422, detail="all_years needs start and end")

        window = queries.day_window(start, end)

    return queries.Filters(
        parsed_bbox, start, end, source, frp_category, limit, window, country=country
    )


FiltersDep = Annotated[queries.Filters, Depends(get_filters)]


def _geojson(content: dict[str, Any], max_age: int | None = None) -> JSONResponse:
    response = JSONResponse(content, media_type=GEOJSON_MEDIA_TYPE)
    if max_age:
        response.headers["Cache-Control"] = f"public, max-age={max_age}"

    return response


@router.get("/health")
def health(connection: Connection) -> dict[str, str]:
    """Comprueba que la API llega a la base de datos."""
    connection.execute("SELECT 1")

    return {"status": "ok"}


@router.get("/summary")
def summary(connection: Connection) -> dict[str, Any]:
    """Recuentos y rango de fechas de los datos válidos (para inicializar el mapa)."""
    return _summary_cache.get_or_compute("summary", lambda: queries.fetch_summary(connection))


@router.get("/activity")
def activity(
    connection: Connection,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_ACTIVITY_LIMIT, description="Maximum number of fires.")
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

    ``geometry=point`` devuelve el punto; ``geometry=pixel``, el área del píxel (un
    rectángulo ``area_width_km`` × ``area_height_km`` centrado en el punto, sin el buffer del
    pipeline).

    Devuelve ``count`` (elementos devueltos), ``total`` (los que cumplen el filtro) y
    ``truncated`` (si ``total`` supera ``limit``).
    """
    query = queries.detections_query(filters, geometry)

    return _geojson(
        queries.fetch_collection(connection, query, queries.detection_feature), BROWSER_CACHE_S
    )


def warm_caches() -> None:
    """
    Precalcula las respuestas cacheadas que necesita la primera pantalla (países y resumen), para
    que el primer visitante tras arrancar la API no espere a calcularlas. Si falla (por ejemplo,
    la base aún no está lista) no pasa nada: se calcularán en la primera petición.
    """
    try:
        for connection in get_connection():
            _countries_cache.get_or_compute(
                "countries", lambda: queries.fetch_countries(connection)
            )
            _summary_cache.get_or_compute("summary", lambda: queries.fetch_summary(connection))
    except Exception as error:  # arranque: nunca debe impedir que la API sirva
        LOGGER.warning("Could not warm the API caches: %s", error)


@router.get("/countries")
def countries(connection: Connection) -> JSONResponse:
    """
    Fronteras de los países (Natural Earth, simplificadas) para el globo del mapa.

    Cada país lleva ``code`` (el que acepta ``?country=``), ``name``, ``continent`` y
    ``has_data``. Se cachea ``COUNTRIES_CACHE_TTL_S`` segundos.
    """
    content = _countries_cache.get_or_compute(
        "countries", lambda: queries.fetch_countries(connection)
    )
    geojson = _geojson(content)
    geojson.headers["Cache-Control"] = f"public, max-age={COUNTRIES_CACHE_TTL_S}"

    return geojson


@router.get("/history")
def history(
    connection: Connection,
    bbox: Annotated[str, Query(description="Zone minLon,minLat,maxLon,maxLat (EPSG:4326).")],
    start: Annotated[date, Query(description="Start of the reference period (inclusive).")],
    end: Annotated[date, Query(description="End of the reference period (inclusive).")],
    years: Annotated[
        int, Query(ge=1, le=queries.MAX_HISTORY_YEARS, description="Previous years to compare.")
    ] = queries.DEFAULT_HISTORY_YEARS,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_HISTORY_LIMIT, description="Maximum number of events.")
    ] = queries.DEFAULT_HISTORY_LIMIT,
    sort: Annotated[
        Literal["severity", "recent", "detections"],
        Query(description="Order of the returned events (the most severe, latest or largest first)."),
    ] = "severity",
) -> dict[str, Any]:
    """
    Incendios de años anteriores de una zona: los de la **misma zona** y las **mismas fechas del año**
    en los ``years`` años anteriores.

    Devuelve un resumen por periodo (el elegido, marcado con ``is_reference``, y cada año
    anterior) y los eventos de los años anteriores, los mayores primero.
    """
    try:
        parsed_bbox = queries.parse_bbox(bbox)
        periods = queries.history_periods(start, end, years)
    except (queries.InvalidBBoxError, queries.InvalidPeriodError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    key = (parsed_bbox, start, end, years, limit, sort)
    return _history_cache.get_or_compute(
        key, lambda: queries.fetch_history(connection, parsed_bbox, periods, limit, sort)
    )


@router.get("/yearly")
def yearly(
    filters: FiltersDep,
    connection: Connection,
    response: Response,
) -> dict[str, Any]:
    """
    Totales por año de las mismas fechas del rango ``start``-``end`` (``all_years`` implícito).

    Pensado para contadores globales: sin ``bbox`` cuenta todo el mapa. Devuelve ``window`` y,
    para cada año (del más reciente al más antiguo), ``events``, ``detections`` y
    ``frp_max_mw``. El resultado se cachea ``YEARLY_CACHE_TTL_S`` segundos por ventana de días,
    zona, fuente y país (no por año, así que 12-18 ago de 2024 y de 2025 comparten entrada).
    """
    if filters.start is None or filters.end is None:
        raise HTTPException(status_code=422, detail="yearly needs start and end")

    window = queries.day_window(filters.start, filters.end)
    key = (window, filters.bbox, filters.source, filters.country)
    response.headers["Cache-Control"] = f"public, max-age={YEARLY_CACHE_TTL_S}"

    return _yearly_cache.get_or_compute(
        key,
        lambda: queries.fetch_yearly(connection, replace(filters, day_window=window)),
    )


@router.get("/events/{event_id}")
def event_detail(
    event_id: str,
    connection: Connection,
    limit: Annotated[
        int, Query(ge=1, le=queries.MAX_LIMIT, description="Maximum number of detections.")
    ] = queries.DEFAULT_EVENT_DETECTIONS,
) -> dict[str, Any]:
    """
    Un incendio: su envolvente (``event``) y sus detecciones dibujadas como el píxel de área
    ``area_width_km`` × ``area_height_km`` (``detections``). 404 si no existe.
    """
    detail = queries.fetch_event_detail(connection, event_id, limit)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    return detail


@router.get("/events")
def events(
    filters: FiltersDep,
    connection: Connection,
    geometry: Annotated[Literal["point", "area"], Query()] = "point",
    match: Annotated[
        Literal["intersects", "inside"],
        Query(description="intersects: the hull touches; inside: there are detections inside."),
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
        raise HTTPException(status_code=422, detail="match=inside needs bbox")

    query = queries.events_query(filters, geometry, match)

    return _geojson(
        queries.fetch_collection(connection, query, queries.event_feature), BROWSER_CACHE_S
    )
