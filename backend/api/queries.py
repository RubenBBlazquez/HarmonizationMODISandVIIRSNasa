"""
Consultas y conversión a GeoJSON de los datos que muestra el mapa.

Solo se sirven datos **válidos**: las detecciones con ``is_valid = true`` (las marcadas con
``skip_reason`` no salen) y los eventos, que por construcción solo se forman con detecciones
válidas. Las funciones de este módulo no dependen de FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Protocol

SRID = 4326
DEFAULT_LIMIT = 5000
MAX_LIMIT = 50000
COORD_DECIMALS = 5
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
M2_PER_KM2 = 1_000_000.0
# Pasadas de satélite y biomasa quemada (estimada) de cada evento; las rellena el pipeline.
EVENT_STATS_SQL = "e.n_passes, e.max_pass_gap_hours, e.biomass_burned_kg"
EVENT_GEOMETRIES = {"point": "geom_centroid", "area": "geom_hull"}
# Cómo se decide que un evento está en el recuadro:
# - "intersects": su envolvente lo toca (lo que se ve en pantalla; puede incluir eventos cuyas
#   detecciones están fuera del recuadro).
# - "inside": tiene al menos una detección dentro del recuadro (y dentro de las fechas). Es el
#   criterio exacto para una zona seleccionada: el punto devuelto es el centro de las detecciones
#   que caen en la zona, así que siempre queda dentro del recuadro.
EVENT_MATCHES = ("intersects", "inside")
DETECTION_GEOMETRIES = ("point", "pixel")
# CRS métrico en el que el pipeline construye el píxel (geometry.metric_crs de la configuración).
PIXEL_SRID = 25830
HALF_KM_IN_M = 500.0  # ancho y alto están en km: la mitad, en metros, es km * 500


class InvalidBBoxError(ValueError):
    """El parámetro ``bbox`` no es ``minLon,minLat,maxLon,maxLat`` válido."""


class _Connection(Protocol):
    def execute(self, query: str, params: Any = ...) -> Any: ...


@dataclass(frozen=True)
class Filters:
    """
    Filtros comunes de las consultas del mapa.

    Attributes
    ----------
    bbox : tuple[float, float, float, float] | None
        ``(min_lon, min_lat, max_lon, max_lat)`` en EPSG:4326.
    start, end : date | None
        Rango de fechas UTC, ambas incluidas.
    source : str | None
        ``MODIS`` o ``VIIRS``.
    frp_category : str | None
        ``low``, ``medium`` o ``high`` (solo detecciones).
    limit : int
        Máximo de elementos devueltos.
    day_window : tuple[int, int] | None
        Ventana de días del año ``(inicio, fin)`` con formato ``mes*100 + día`` (``812`` = 12 ago).
        Si se informa, se devuelven las **mismas fechas de todos los años** y se ignoran los años
        de ``start`` y ``end`` (ver ``day_window``). Si ``inicio > fin`` cruza el fin de año.
    """

    bbox: tuple[float, float, float, float] | None = None
    start: date | None = None
    end: date | None = None
    source: str | None = None
    frp_category: str | None = None
    limit: int = DEFAULT_LIMIT
    day_window: tuple[int, int] | None = None


@dataclass(frozen=True)
class Query:
    """Consulta de filas, consulta de recuento y parámetros comunes de ambas."""

    sql: str
    count_sql: str
    params: tuple[Any, ...]
    limit: int


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """
    Interpreta ``minLon,minLat,maxLon,maxLat``.

    Raises
    ------
    InvalidBBoxError
        Si no son 4 números, están fuera de rango o los mínimos superan a los máximos.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in text.split(","))
    except ValueError as error:
        raise InvalidBBoxError("bbox debe ser minLon,minLat,maxLon,maxLat") from error

    in_range = -180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90
    if not in_range:
        raise InvalidBBoxError("bbox fuera de rango o con mínimos mayores que máximos")

    return min_lon, min_lat, max_lon, max_lat


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def day_window(start: date, end: date) -> tuple[int, int]:
    """
    Ventana de días del año (``mes*100 + día``) que cubre el rango ``start``-``end``.

    Sirve para pedir las mismas fechas en todos los años: 12-18 ago → ``(812, 818)``; 25 dic →
    5 ene cruza el fin de año → ``(1225, 105)``. Un rango de un año o más da el año entero.
    """
    if start > end:
        raise InvalidPeriodError("start no puede ser posterior a end")

    if (end - start).days >= 365:
        return 101, 1231

    return start.month * 100 + start.day, end.month * 100 + end.day


def _window_clause(column: str, window: tuple[int, int]) -> tuple[str, list[int]]:
    """Condición "el día del año (UTC) de ``column`` cae en la ventana" y sus parámetros."""
    key = (
        f"(EXTRACT(MONTH FROM {column} AT TIME ZONE 'UTC')::int * 100 "
        f"+ EXTRACT(DAY FROM {column} AT TIME ZONE 'UTC')::int)"
    )
    first, last = window
    if first <= last:
        return f"{key} BETWEEN %s AND %s", [first, last]

    return f"({key} >= %s OR {key} <= %s)", [first, last]


def _window_where(first: str, last: str, window: tuple[int, int]) -> tuple[str, list[int]]:
    """Ventana para una fecha (``first == last``) o para el inicio o el final de un evento."""
    clause, params = _window_clause(first, window)
    if first == last:
        return clause, params

    other, other_params = _window_clause(last, window)

    return f"({clause} OR {other})", [*params, *other_params]


def _where(
    filters: Filters, base: list[str], geom: str, first: str, last: str, source_sql: str
) -> tuple[str, list[Any]]:
    clauses, params = list(base), []
    if filters.bbox is not None:
        clauses.append(f"{geom} && ST_MakeEnvelope(%s, %s, %s, %s, {SRID})")
        params.extend(filters.bbox)

    if filters.day_window is not None:
        clause, window_params = _window_where(first, last, filters.day_window)
        clauses.append(clause)
        params.extend(window_params)
    else:
        if filters.start is not None:
            clauses.append(f"{last} >= %s")
            params.append(_day_start(filters.start))

        if filters.end is not None:
            clauses.append(f"{first} < %s")
            params.append(_day_start(filters.end) + timedelta(days=1))

    if filters.source is not None:
        clauses.append(source_sql)
        params.append(filters.source if "=" in source_sql else f"%{filters.source}%")

    return " AND ".join(clauses), params


def pixel_geometry_sql() -> str:
    """
    Polígono del área de la detección: rectángulo ``area_width_km`` (X) × ``area_height_km`` (Y)
    centrado en el punto, construido en el CRS métrico del pipeline y devuelto en EPSG:4326. No
    incluye el buffer de solape de ``geom_footprint`` (aproximación alineada a los ejes, sin la
    inclinación de la órbita).
    """
    half_width = f"d.area_width_km * {HALF_KM_IN_M}"
    half_height = f"d.area_height_km * {HALF_KM_IN_M}"
    return (
        "ST_Transform(ST_MakeEnvelope("
        f"ST_X(p.geom) - {half_width}, ST_Y(p.geom) - {half_height}, "
        f"ST_X(p.geom) + {half_width}, ST_Y(p.geom) + {half_height}, "
        f"{PIXEL_SRID}), {SRID})"
    )


def detections_query(filters: Filters, geometry: str = "point") -> Query:
    """
    Detecciones válidas (``is_valid``), de la más reciente a la más antigua.

    ``geometry="point"`` devuelve el punto; ``"pixel"``, el área (``area_width_km`` × ``area_height_km``).
    """
    if geometry not in DETECTION_GEOMETRIES:
        raise ValueError(f"geometry debe ser uno de {DETECTION_GEOMETRIES}")

    where, params = _where(
        filters,
        ["d.is_valid"],
        "d.geom_point",
        "d.acq_datetime",
        "d.acq_datetime",
        "d.source = %s",
    )
    if filters.frp_category is not None:
        where += " AND d.frp_category = %s"
        params.append(filters.frp_category)

    tables = (
        "FROM fire.detections d "
        "LEFT JOIN fire.event_detections ed ON ed.detection_id = d.detection_id"
    )
    if geometry == "pixel":
        geom = pixel_geometry_sql()
        projected = f"(SELECT ST_Transform(d.geom_point, {PIXEL_SRID}) AS geom) p"
        rows_from = f"{tables} CROSS JOIN LATERAL {projected}"
    else:
        geom, rows_from = "d.geom_point", tables

    sql = (
        "SELECT d.detection_id, d.source, d.satellite, d.acq_datetime, d.daynight, d.frp, "
        "d.frp_category, d.biomass_kg_s, d.confidence_norm, d.land_cover, d.area_width_km, d.area_height_km, "
        f"ed.event_id, ST_AsGeoJSON({geom}, {COORD_DECIMALS})::json AS geometry "
        f"{rows_from} WHERE {where} ORDER BY d.acq_datetime DESC, d.detection_id LIMIT %s"
    )
    count_sql = f"SELECT count(*) AS total {tables} WHERE {where}"

    return Query(sql, count_sql, tuple(params), filters.limit)


def _inside_lateral(filters: Filters) -> tuple[str, list[Any]]:
    """Detecciones de cada evento dentro del recuadro y las fechas: recuento, FRP y centro."""
    assert filters.bbox is not None
    where = [
        "ed.event_id = e.event_id",
        "d.is_valid",
        f"d.geom_point && ST_MakeEnvelope(%s, %s, %s, %s, {SRID})",
    ]
    params: list[Any] = list(filters.bbox)
    if filters.day_window is not None:
        clause, window_params = _window_where("d.acq_datetime", "d.acq_datetime", filters.day_window)
        where.append(clause)
        params.extend(window_params)
    else:
        if filters.start is not None:
            where.append("d.acq_datetime >= %s")
            params.append(_day_start(filters.start))

        if filters.end is not None:
            where.append("d.acq_datetime < %s")
            params.append(_day_start(filters.end) + timedelta(days=1))

    lateral = (
        "CROSS JOIN LATERAL (SELECT count(*) AS zone_detections, max(d.frp) AS zone_frp_max, "
        "ST_Centroid(ST_Collect(d.geom_point)) AS center "
        "FROM fire.event_detections ed JOIN fire.detections d ON d.detection_id = ed.detection_id "
        f"WHERE {' AND '.join(where)}) z"
    )

    return lateral, params


def events_query(filters: Filters, geometry: str = "point", match: str = "intersects") -> Query:
    """
    Eventos en el recuadro y las fechas, del más reciente al más antiguo.

    ``match="intersects"`` incluye los eventos cuya envolvente toca el recuadro.
    ``match="inside"`` (necesita ``bbox``) solo los que tienen detecciones dentro del recuadro
    y las fechas; añade ``zone_detections`` y ``zone_frp_max`` y, con ``geometry="point"``,
    devuelve el centro de esas detecciones.
    """
    if match not in EVENT_MATCHES:
        raise ValueError(f"match debe ser uno de {EVENT_MATCHES}")

    if match == "inside":
        return _inside_events_query(filters, geometry)

    column = EVENT_GEOMETRIES[geometry]
    where, params = _where(
        filters,
        ["TRUE"],
        "e.geom_hull",
        "e.first_detection",
        "e.last_detection",
        "e.sensors LIKE %s",
    )
    source = f"FROM fire.fire_events e WHERE {where}"
    sql = (
        "SELECT e.event_id, e.status, e.first_detection, e.last_detection, e.n_detections, "
        f"e.sensors, e.frp_max, e.biomass_max_kg_s, {EVENT_STATS_SQL}, e.confidence_fused, "
        f"e.hull_area_m2 / {M2_PER_KM2} AS footprint_area_km2, "
        f"ST_AsGeoJSON(e.{column}, {COORD_DECIMALS})::json AS geometry {source} "
        "ORDER BY e.last_detection DESC, e.event_id LIMIT %s"
    )

    return Query(sql, f"SELECT count(*) AS total {source}", tuple(params), filters.limit)


def _inside_events_query(filters: Filters, geometry: str) -> Query:
    if filters.bbox is None:
        raise ValueError("match=inside necesita bbox")

    lateral, lateral_params = _inside_lateral(filters)
    # La envolvente del evento tiene que tocar el recuadro si alguna detección cae dentro:
    # filtro barato (con índice) antes de mirar las detecciones.
    clauses = [f"e.geom_hull && ST_MakeEnvelope(%s, %s, %s, %s, {SRID})", "z.zone_detections > 0"]
    params = [*lateral_params, *filters.bbox]
    if filters.source is not None:
        clauses.append("e.sensors LIKE %s")
        params.append(f"%{filters.source}%")

    geom = "z.center" if geometry == "point" else "e.geom_hull"
    source = f"FROM fire.fire_events e {lateral} WHERE {' AND '.join(clauses)}"
    sql = (
        "SELECT e.event_id, e.status, e.first_detection, e.last_detection, e.n_detections, "
        f"e.sensors, e.frp_max, e.biomass_max_kg_s, {EVENT_STATS_SQL}, e.confidence_fused, "
        f"e.hull_area_m2 / {M2_PER_KM2} AS footprint_area_km2, z.zone_detections, "
        f"z.zone_frp_max, ST_AsGeoJSON({geom}, {COORD_DECIMALS})::json AS geometry {source} "
        "ORDER BY e.last_detection DESC, e.event_id LIMIT %s"
    )

    return Query(sql, f"SELECT count(*) AS total {source}", tuple(params), filters.limit)


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def _round(value: float | None, decimals: int) -> float | None:
    return None if value is None else round(float(value), decimals)


def detection_feature(row: dict[str, Any]) -> dict[str, Any]:
    """Fila de ``fire.detections`` -> Feature GeoJSON (punto)."""
    properties = {
        "detection_id": row["detection_id"],
        "event_id": row["event_id"],
        "source": row["source"],
        "satellite": row["satellite"],
        "acq_datetime": _timestamp(row["acq_datetime"]),
        "daynight": row["daynight"],
        "frp_mw": _round(row["frp"], 2),
        "frp_category": row["frp_category"],
        "biomass_kg_s": _round(row["biomass_kg_s"], 3),
        "confidence": row["confidence_norm"],
        "land_cover": row["land_cover"],
        "area_width_km": _round(row["area_width_km"], 3),
        "area_height_km": _round(row["area_height_km"], 3),
    }

    return {"type": "Feature", "geometry": row["geometry"], "properties": properties}


def event_feature(row: dict[str, Any]) -> dict[str, Any]:
    """Fila de ``fire.fire_events`` -> Feature GeoJSON (centroide o envolvente)."""
    properties = {
        "event_id": row["event_id"],
        "status": row["status"],
        "first_detection": _timestamp(row["first_detection"]),
        "last_detection": _timestamp(row["last_detection"]),
        "n_detections": row["n_detections"],
        "sensors": row["sensors"],
        "frp_max_mw": _round(row["frp_max"], 2),
        "biomass_max_kg_s": _round(row["biomass_max_kg_s"], 3),
        "confidence": row["confidence_fused"],
        "footprint_area_km2": _round(row["footprint_area_km2"], 3),
        # Estimación (FRP integrado x coeficiente); null hasta que el pipeline la calcula.
        "biomass_burned_kg": _round(row.get("biomass_burned_kg"), 0),
        "n_passes": row.get("n_passes"),
        "max_pass_gap_hours": _round(row.get("max_pass_gap_hours"), 1),
    }
    if row.get("zone_detections") is not None:  # solo cuando se buscó dentro de una zona
        properties["zone_detections"] = int(row["zone_detections"])
        properties["zone_frp_max_mw"] = _round(row.get("zone_frp_max"), 2)

    return {"type": "Feature", "geometry": row["geometry"], "properties": properties}


def fetch_collection(
    connection: _Connection, query: Query, to_feature: Callable[[dict[str, Any]], dict[str, Any]]
) -> dict[str, Any]:
    """
    Ejecuta la consulta y devuelve una FeatureCollection con ``count``, ``total`` y ``truncated``.

    Se pide una fila más que el límite para saber si hay más; el recuento total solo se calcula
    cuando se ha truncado.
    """
    rows = connection.execute(query.sql, (*query.params, query.limit + 1)).fetchall()
    truncated = len(rows) > query.limit
    rows = rows[: query.limit]
    total = len(rows)
    if truncated:
        total = connection.execute(query.count_sql, query.params).fetchone()["total"]

    return {
        "type": "FeatureCollection",
        "count": len(rows),
        "total": total,
        "truncated": truncated,
        "features": [to_feature(row) for row in rows],
    }


def fetch_summary(connection: _Connection) -> dict[str, Any]:
    """Recuentos y rango de fechas de los datos válidos."""
    detections = connection.execute(
        "SELECT count(*) FILTER (WHERE is_valid) AS valid_detections, "
        "count(*) FILTER (WHERE NOT is_valid) AS skipped_detections, "
        "min(acq_datetime) FILTER (WHERE is_valid) AS first_detection, "
        "max(acq_datetime) FILTER (WHERE is_valid) AS last_detection FROM fire.detections"
    ).fetchone()
    events = connection.execute(
        "SELECT count(*) AS events, count(*) FILTER (WHERE status = 'active') AS active_events "
        "FROM fire.fire_events"
    ).fetchone()

    return {
        "valid_detections": detections["valid_detections"],
        "skipped_detections": detections["skipped_detections"],
        "first_detection": _timestamp(detections["first_detection"]),
        "last_detection": _timestamp(detections["last_detection"]),
        "events": events["events"],
        "active_events": events["active_events"],
    }


# --- Actividad reciente: últimos incendios registrados -----------------------------------------
DEFAULT_ACTIVITY_LIMIT = 10
MAX_ACTIVITY_LIMIT = 50


def fetch_activity(connection: _Connection, limit: int = DEFAULT_ACTIVITY_LIMIT) -> dict[str, Any]:
    """Los ``limit`` incendios con la detección más reciente (primero el más reciente)."""
    rows = connection.execute(
        "SELECT event_id, status, sensors, n_detections, frp_max, first_detection, "
        "last_detection, ST_Y(geom_centroid) AS lat, ST_X(geom_centroid) AS lon "
        "FROM fire.fire_events ORDER BY last_detection DESC, event_id LIMIT %s",
        (limit,),
    ).fetchall()

    return {
        "count": len(rows),
        "items": [
            {
                "event_id": row["event_id"],
                "status": row["status"],
                "sensors": row["sensors"],
                "n_detections": row["n_detections"],
                "frp_max_mw": _round(row["frp_max"], 2),
                "first_detection": _timestamp(row["first_detection"]),
                "last_detection": _timestamp(row["last_detection"]),
                "lat": _round(row["lat"], 5),
                "lon": _round(row["lon"], 5),
            }
            for row in rows
        ],
    }


# --- Histórico de una zona: incendios de años anteriores ---------------------------
# Criterio: misma zona (recuadro) y mismas fechas del año (el rango elegido desplazado 1, 2…
# años atrás). El periodo elegido se incluye como referencia para comparar.
DEFAULT_HISTORY_YEARS = 10
MAX_HISTORY_YEARS = 30
MAX_PERIOD_DAYS = 366
DEFAULT_HISTORY_LIMIT = 200
MAX_HISTORY_LIMIT = 2000


class InvalidPeriodError(ValueError):
    """El rango de fechas no permite comparar con años anteriores."""


@dataclass(frozen=True)
class Period:
    """
    Periodo de comparación.

    Attributes
    ----------
    label : str
        Año (``2024``) o años que abarca (``2023-2024``).
    start, end : date
        Fechas UTC, ambas incluidas.
    is_reference : bool
        ``True`` para el periodo elegido en el mapa.
    """

    label: str
    start: date
    end: date
    is_reference: bool


def _shift_years(day: date, years: int) -> date:
    """Mismo día ``years`` años antes (el 29 de febrero pasa al 28 en años no bisiestos)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _period_label(start: date, end: date) -> str:
    return str(start.year) if start.year == end.year else f"{start.year}-{end.year}"


def history_periods(start: date, end: date, years: int) -> list[Period]:
    """
    Periodo elegido y los mismos días en cada uno de los ``years`` años anteriores.

    Raises
    ------
    InvalidPeriodError
        Si ``start`` es posterior a ``end`` o el rango supera un año (los periodos se solaparían).
    """
    if start > end:
        raise InvalidPeriodError("start no puede ser posterior a end")

    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise InvalidPeriodError(
            "El rango de fechas debe ser de un año como máximo para comparar con años anteriores"
        )

    periods = []
    for offset in range(years + 1):
        shifted_start, shifted_end = _shift_years(start, offset), _shift_years(end, offset)
        label = _period_label(shifted_start, shifted_end)
        periods.append(Period(label, shifted_start, shifted_end, is_reference=offset == 0))

    return periods


def _periods_cte(periods: list[Period]) -> tuple[str, list[Any]]:
    rows = ", ".join("(%s::text, %s::boolean, %s::timestamptz, %s::timestamptz)" for _ in periods)
    params: list[Any] = []
    for period in periods:
        params.extend(
            [
                period.label,
                period.is_reference,
                _day_start(period.start),
                _day_start(period.end) + timedelta(days=1),
            ]
        )

    return f"WITH periods(label, is_reference, p_start, p_end) AS (VALUES {rows})", params


def _history_cte(
    bbox: tuple[float, float, float, float], periods: list[Period]
) -> tuple[str, list[Any]]:
    """
    Periodos y, para cada uno, los eventos con detecciones dentro de la zona en esas fechas
    (recuento, FRP máximo y centro de esas detecciones).
    """
    periods_cte, params = _periods_cte(periods)
    matched = (
        ", matched AS (SELECT p.label, p.is_reference, p.p_start, ed.event_id, "
        "count(*) AS zone_detections, max(d.frp) AS zone_frp_max, "
        "ST_Centroid(ST_Collect(d.geom_point)) AS center "
        "FROM periods p JOIN fire.detections d ON d.is_valid "
        f"AND d.geom_point && ST_MakeEnvelope(%s, %s, %s, %s, {SRID}) "
        "AND d.acq_datetime >= p.p_start AND d.acq_datetime < p.p_end "
        "JOIN fire.event_detections ed ON ed.detection_id = d.detection_id "
        "GROUP BY p.label, p.is_reference, p.p_start, ed.event_id)"
    )

    return periods_cte + matched, [*params, *bbox]


def history_summary_query(
    bbox: tuple[float, float, float, float], periods: list[Period]
) -> tuple[str, tuple[Any, ...]]:
    """Incendios, detecciones y FRP máximo dentro de la zona por periodo (también los vacíos)."""
    cte, params = _history_cte(bbox, periods)
    sql = (
        f"{cte} SELECT p.label, count(m.event_id) AS events, "
        "coalesce(sum(m.zone_detections), 0) AS detections, max(m.zone_frp_max) AS frp_max "
        "FROM periods p LEFT JOIN matched m ON m.label = p.label "
        "GROUP BY p.label, p.p_start ORDER BY p.p_start DESC"
    )

    return sql, tuple(params)


def history_events_query(
    bbox: tuple[float, float, float, float], periods: list[Period], limit: int
) -> tuple[str, tuple[Any, ...]]:
    """Eventos de los años anteriores (sin el elegido), con más detecciones en la zona primero."""
    cte, params = _history_cte(bbox, periods)
    sql = (
        f"{cte} SELECT m.label AS period, e.event_id, e.status, e.first_detection, "
        "e.last_detection, e.n_detections, e.sensors, e.frp_max, e.biomass_max_kg_s, "
        f"{EVENT_STATS_SQL}, e.confidence_fused, "
        f"e.hull_area_m2 / {M2_PER_KM2} AS footprint_area_km2, m.zone_detections, m.zone_frp_max, "
        f"ST_AsGeoJSON(m.center, {COORD_DECIMALS})::json AS geometry "
        "FROM matched m JOIN fire.fire_events e ON e.event_id = m.event_id "
        "WHERE NOT m.is_reference "
        "ORDER BY m.zone_detections DESC, e.frp_max DESC, e.event_id LIMIT %s"
    )

    return sql, (*params, limit)


def fetch_history(
    connection: _Connection,
    bbox: tuple[float, float, float, float],
    periods: list[Period],
    limit: int,
) -> dict[str, Any]:
    """
    Histórico de la zona: resumen por periodo y eventos de los años anteriores.

    Returns
    -------
    dict[str, Any]
        ``bbox``, ``periods`` (del más reciente al más antiguo) y ``events`` (FeatureCollection).
    """
    sql, params = history_summary_query(bbox, periods)
    totals = {row["label"]: row for row in connection.execute(sql, params).fetchall()}
    sql, params = history_events_query(bbox, periods, limit)
    rows = connection.execute(sql, params).fetchall()

    summary = []
    for period in periods:
        row = totals.get(period.label, {})
        summary.append(
            {
                "label": period.label,
                "start": period.start.isoformat(),
                "end": period.end.isoformat(),
                "is_reference": period.is_reference,
                "events": int(row.get("events") or 0),
                "detections": int(row.get("detections") or 0),
                "frp_max_mw": _round(row.get("frp_max"), 2),
            }
        )

    features = []
    for row in rows:
        feature = event_feature(row)
        feature["properties"]["period"] = row["period"]
        features.append(feature)

    return {
        "bbox": list(bbox),
        "periods": summary,
        "events": {"type": "FeatureCollection", "count": len(features), "features": features},
    }


# --- Resumen por año de las mismas fechas de un rango (botón "Comparar con todos los años") -----------
def yearly_query(filters: Filters) -> Query:
    """
    Detecciones válidas, incendios y FRP máximo por año, solo de las fechas de ``filters.day_window``.

    Necesita ``day_window``; ``bbox`` y ``source`` son opcionales. No usa ``limit``.
    """
    if filters.day_window is None:
        raise ValueError("yearly_query necesita day_window")

    where, params = _where(
        filters,
        ["d.is_valid"],
        "d.geom_point",
        "d.acq_datetime",
        "d.acq_datetime",
        "d.source = %s",
    )
    sql = (
        "SELECT EXTRACT(YEAR FROM d.acq_datetime AT TIME ZONE 'UTC')::int AS year, "
        "count(*) AS detections, count(DISTINCT ed.event_id) AS events, max(d.frp) AS frp_max "
        "FROM fire.detections d "
        "LEFT JOIN fire.event_detections ed ON ed.detection_id = d.detection_id "
        f"WHERE {where} GROUP BY year ORDER BY year DESC"
    )

    return Query(sql, "", tuple(params), 0)


def fetch_yearly(connection: _Connection, filters: Filters) -> dict[str, Any]:
    """
    Resumen por año de las mismas fechas del rango.

    Returns
    -------
    dict[str, Any]
        ``window`` (``[inicio, fin]`` como mes*100 + día) y ``years`` (del más reciente al más antiguo, con ceros en los años
        intermedios sin datos): ``year``, ``events``, ``detections`` y ``frp_max_mw``.
    """
    query = yearly_query(filters)
    rows = {row["year"]: row for row in connection.execute(query.sql, query.params).fetchall()}
    years = []
    if rows:
        for year in range(max(rows), min(rows) - 1, -1):
            row = rows.get(year, {})
            years.append(
                {
                    "year": year,
                    "events": int(row.get("events") or 0),
                    "detections": int(row.get("detections") or 0),
                    "frp_max_mw": _round(row.get("frp_max"), 2),
                }
            )

    return {"window": list(filters.day_window or ()), "years": years}


# --- Detalle de un incendio: su envolvente y los píxeles de sus detecciones --------------------
DEFAULT_EVENT_DETECTIONS = 2000

EVENT_DETAIL_SQL = (
    "SELECT e.event_id, e.status, e.first_detection, e.last_detection, e.n_detections, "
    f"e.sensors, e.frp_max, e.biomass_max_kg_s, {EVENT_STATS_SQL}, e.confidence_fused, "
    f"e.hull_area_m2 / {M2_PER_KM2} AS footprint_area_km2, "
    f"ST_AsGeoJSON(e.geom_hull, {COORD_DECIMALS})::json AS geometry "
    "FROM fire.fire_events e WHERE e.event_id = %s"
)


def event_detections_sql() -> str:
    """Detecciones de un evento con la geometría de su píxel, de la más antigua a la más nueva."""
    return (
        "SELECT d.detection_id, d.source, d.satellite, d.acq_datetime, d.daynight, d.frp, "
        "d.frp_category, d.biomass_kg_s, d.confidence_norm, d.land_cover, d.area_width_km, d.area_height_km, "
        f"ed.event_id, ST_AsGeoJSON({pixel_geometry_sql()}, {COORD_DECIMALS})::json AS geometry "
        "FROM fire.event_detections ed "
        "JOIN fire.detections d ON d.detection_id = ed.detection_id "
        f"CROSS JOIN LATERAL (SELECT ST_Transform(d.geom_point, {PIXEL_SRID}) AS geom) p "
        "WHERE ed.event_id = %s ORDER BY d.acq_datetime, d.detection_id LIMIT %s"
    )


def fetch_event_detail(
    connection: _Connection, event_id: str, limit: int = DEFAULT_EVENT_DETECTIONS
) -> dict[str, Any] | None:
    """
    Evento (con su envolvente) y sus detecciones como rectángulos ``area_width_km`` × ``area_height_km``.

    Returns
    -------
    dict[str, Any] | None
        ``event`` (Feature) y ``detections`` (FeatureCollection con ``count``, ``total`` y
        ``truncated``), o ``None`` si el evento no existe.
    """
    event_row = connection.execute(EVENT_DETAIL_SQL, (event_id,)).fetchone()
    if event_row is None:
        return None

    rows = connection.execute(event_detections_sql(), (event_id, limit + 1)).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]

    return {
        "event": event_feature(event_row),
        "detections": {
            "type": "FeatureCollection",
            "count": len(rows),
            "total": event_row["n_detections"] if truncated else len(rows),
            "truncated": truncated,
            "features": [detection_feature(row) for row in rows],
        },
    }
