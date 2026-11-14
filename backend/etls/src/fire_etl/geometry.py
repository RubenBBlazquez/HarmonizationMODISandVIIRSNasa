"""
Geometrías de las detecciones: puntos, filtro por área de interés y footprints.

Todos los cálculos de distancia y área se hacen en un CRS métrico (EPSG:3035 o UTM); nunca en
grados. Las geometrías se almacenan después en EPSG:4326 (ver :mod:`fire_etl.outputs`).

Limitación conocida del footprint: se aproxima como un rectángulo alineado a los ejes del CRS
métrico, de ``scan`` km en X (dirección de barrido, aproximadamente E-O en órbitas polares) por
``track`` km en Y (dirección de la órbita, aproximadamente N-S), centrado en el punto. No
considera la inclinación real de la órbita, el solape *bow-tie* ni la función de dispersión del
píxel; el buffer configurable absorbe parte de ese error y del de geolocalización.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS

from fire_etl.config import AreaOfInterestConfig, GeometryConfig

METERS_PER_KM = 1000.0
UTM_ZONE_WIDTH_DEG = 6.0


class MetricCrsError(ValueError):
    """El CRS configurado para los cálculos no es métrico."""


@dataclass(frozen=True)
class DetectionGeometries:
    """
    Detecciones con geometría en el CRS métrico.

    Attributes
    ----------
    detections : gpd.GeoDataFrame
        Detecciones dentro del área de interés con ``geom_point``, ``geom_footprint`` (activa) y
        ``footprint_area_m2``.
    metric_crs : CRS
        CRS métrico usado en los cálculos.
    outside_aoi : int
        Detecciones descartadas por quedar fuera del área de interés.
    """

    detections: gpd.GeoDataFrame
    metric_crs: CRS
    outside_aoi: int


def area_of_interest_geometry(aoi: AreaOfInterestConfig) -> shapely.Geometry | None:
    """
    Construye la geometría del área de interés en EPSG:4326.

    Parameters
    ----------
    aoi : AreaOfInterestConfig
        Área configurada.

    Returns
    -------
    shapely.Geometry | None
        Polígono del área o ``None`` si no hay filtro.
    """
    if aoi.bbox is not None:
        return shapely.box(*aoi.bbox)

    if aoi.polygon_wkt is not None:
        return shapely.from_wkt(aoi.polygon_wkt)

    return None


def utm_crs_for_lon_lat(lon: float, lat: float) -> CRS:
    """
    Devuelve el CRS UTM (WGS84) de la zona que contiene ``(lon, lat)``.

    Parameters
    ----------
    lon : float
        Longitud en grados.
    lat : float
        Latitud en grados.

    Returns
    -------
    CRS
        EPSG:326xx (hemisferio norte) o EPSG:327xx (hemisferio sur).
    """
    zone = int(np.floor((lon + 180.0) / UTM_ZONE_WIDTH_DEG)) % 60 + 1
    base = 32600 if lat >= 0 else 32700

    return CRS.from_epsg(base + zone)


def resolve_metric_crs(spec: str, reference: shapely.Geometry | None) -> CRS:
    """
    Resuelve el CRS métrico de cálculo.

    Parameters
    ----------
    spec : str
        Código de CRS (p. ej. ``EPSG:3035``) o ``auto`` para la zona UTM del centro de
        ``reference``.
    reference : shapely.Geometry | None
        Geometría en EPSG:4326 (área de interés o envolvente de los datos) usada en ``auto``.

    Returns
    -------
    CRS
        CRS proyectado en metros.

    Raises
    ------
    MetricCrsError
        Si el CRS no es proyectado en metros o ``auto`` no tiene referencia.
    """
    if spec.lower() == "auto":
        if reference is None or reference.is_empty:
            raise MetricCrsError("metric_crs=auto necesita área de interés o datos")

        center = reference.centroid
        crs = utm_crs_for_lon_lat(center.x, center.y)
    else:
        crs = CRS.from_user_input(spec)

    units = {axis.unit_name for axis in crs.axis_info}
    if not crs.is_projected or units != {"metre"}:
        raise MetricCrsError(f"El CRS {crs.to_string()} no es proyectado en metros")

    return crs


def build_footprints(
    points: gpd.GeoSeries, scan_km: pd.Series, track_km: pd.Series, buffer_m: float
) -> gpd.GeoSeries:
    """
    Construye el footprint de cada detección: rectángulo ``scan`` × ``track`` + buffer.

    Parameters
    ----------
    points : gpd.GeoSeries
        Puntos en un CRS métrico.
    scan_km : pd.Series
        Tamaño del píxel en la dirección de barrido (km), aplicado en X.
    track_km : pd.Series
        Tamaño del píxel en la dirección de la órbita (km), aplicado en Y.
    buffer_m : float
        Buffer en metros para absorber el error de posición.

    Returns
    -------
    gpd.GeoSeries
        Polígonos en el mismo CRS que ``points``.
    """
    x = points.x.to_numpy()
    y = points.y.to_numpy()
    half_width = scan_km.to_numpy(dtype=float) * METERS_PER_KM / 2.0
    half_height = track_km.to_numpy(dtype=float) * METERS_PER_KM / 2.0

    boxes = shapely.box(x - half_width, y - half_height, x + half_width, y + half_height)
    if buffer_m > 0:
        boxes = shapely.buffer(boxes, buffer_m, join_style="mitre")

    return gpd.GeoSeries(boxes, index=points.index, crs=points.crs)


def build_detection_geometries(
    detections: pd.DataFrame, geometry: GeometryConfig, aoi: AreaOfInterestConfig
) -> DetectionGeometries:
    """
    Crea puntos y footprints, filtra por área de interés y reproyecta al CRS métrico.

    Parameters
    ----------
    detections : pd.DataFrame
        Detecciones en el esquema común (``lat``, ``lon``, ``scan``, ``track``...).
    geometry : GeometryConfig
        CRS y buffer.
    aoi : AreaOfInterestConfig
        Área de interés.

    Returns
    -------
    DetectionGeometries
        Detecciones geolocalizadas en el CRS métrico.
    """
    points = gpd.GeoSeries(
        gpd.points_from_xy(detections["lon"], detections["lat"]),
        index=detections.index,
        crs=geometry.storage_crs,
    )

    aoi_geometry = area_of_interest_geometry(aoi)
    inside = np.ones(len(points), dtype=bool)
    if aoi_geometry is not None:
        inside = points.intersects(aoi_geometry).to_numpy()

    reference = aoi_geometry
    if reference is None and inside.any():
        reference = shapely.box(*points[inside].total_bounds)

    metric_crs = resolve_metric_crs(geometry.metric_crs, reference)

    kept = detections[inside].copy()
    metric_points = points[inside].to_crs(metric_crs)
    footprints = build_footprints(
        metric_points, kept["scan"], kept["track"], geometry.footprint_buffer_m
    )

    kept["geom_footprint"] = footprints
    kept["geom_point"] = metric_points
    frame = gpd.GeoDataFrame(kept, geometry="geom_footprint", crs=metric_crs)
    frame["footprint_area_m2"] = frame["geom_footprint"].area

    return DetectionGeometries(
        detections=frame, metric_crs=metric_crs, outside_aoi=int((~inside).sum())
    )
